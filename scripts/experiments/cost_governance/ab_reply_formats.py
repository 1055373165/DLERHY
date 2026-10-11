"""A/B of the slim reply + changed-only polish against the current formats (cost-governance M1/M2).

Run against a COPY of the database (BOOK_AGENT_DATABASE_URL=.../book_agent_ab): every packet is
translated by the real TranslationService once per arm and rolled back, so each arm sees the same
context. Arms: A (full reply, polish returns all), A2 (A again: the model's own run-to-run noise),
B (slim reply, polish returns only changes). Writes one JSON with every arm's text, tokens, cost,
polish outcome and failures:

    ab_reply_formats.py translate <out.json> <doc_id>:<n> [<doc_id>:<n> ...]
    ab_reply_formats.py judge <out.json> <judged.json> [--model deepseek-v4-pro]

The judge sees the source and two translations in random order (X/Y) and answers X, Y or tie;
B vs A is read against A2 vs A, the noise.
"""

from __future__ import annotations

import json
import random
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import text

from book_agent.app.main import _ensure_database_state, create_app
from book_agent.core.config import get_settings
from book_agent.domain.models.provider_credential import ProviderCredential
from book_agent.infra.repositories.translation import TranslationRepository
from book_agent.services.translation import TranslationService
from book_agent.workers.factory import build_worker_from_credential

ARMS = {"A": ("full", "all"), "A2": ("full", "all"), "B": ("slim", "changed")}
ORG = "00000000-0000-4000-8000-000000000001"

JUDGE_SYSTEM = """你是严格的中英技术翻译评审。给你一段英文原文和两个中文译文 X、Y。
只比较两者的质量：先看忠实（漏译、错译、数字、否定、比较、术语是否一致），再看中文是否通顺自然。
两者质量相当或差别只是措辞偏好时，判 tie。只有一方明显更好时才选它。
只输出 JSON：{"winner": "X" 或 "Y" 或 "tie", "reason": "一句话理由"}。"""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"winner": {"type": "string", "enum": ["X", "Y", "tie"]}, "reason": {"type": "string"}},
    "required": ["winner", "reason"],
}


class _Meter:
    """Sums the usage of every provider call a worker makes in this thread."""

    def __init__(self, client) -> None:
        self.local = threading.local()
        for name in ("generate_translation", "generate_structured_object"):
            original = getattr(client, name)

            def wrapped(*args, __original=original, **kwargs):
                result = __original(*args, **kwargs)
                usage = result.usage if hasattr(result, "usage") else result[1]
                self._add(usage)
                return result

            setattr(client, name, wrapped)

    def start(self) -> None:
        self.local.calls = []

    def _add(self, usage) -> None:
        calls = getattr(self.local, "calls", None)
        if calls is not None:
            calls.append(
                {
                    "in": usage.token_in,
                    "out": usage.token_out,
                    "cost": usage.cost_usd or 0.0,
                    "hit": getattr(usage, "prompt_cache_hit_tokens", None),
                    "miss": getattr(usage, "prompt_cache_miss_tokens", None),
                }
            )

    def calls(self) -> list[dict]:
        return list(getattr(self.local, "calls", []))


def _sample(session, specs: list[str]) -> list[str]:
    packets: list[str] = []
    for spec in specs:
        doc, n = spec.split(":")
        rows = session.execute(
            text(
                """
                select p.id from translation_packets p join chapters c on c.id = p.chapter_id
                where c.document_id = :d and p.status = 'translated'
                  and (select count(*) from packet_sentence_map m where m.packet_id = p.id and m.role = 'current') >= 2
                """
            ),
            {"d": doc},
        ).scalars().all()
        random.Random(f"ab-{doc}").shuffle(rows := list(rows))
        packets += [str(row) for row in rows[: int(n)]]
    return packets


def translate(out: str, specs: list[str]) -> None:
    app = create_app()
    settings = get_settings()
    _ensure_database_state(app, settings=settings)
    assert settings.database_url.rsplit("/", 1)[-1] != "book_agent", "run the A/B on a copy of the database"
    workers = {}
    with app.state.session_factory() as session:
        record = session.query(ProviderCredential).filter(ProviderCredential.is_active.is_(True)).first()
        packets = _sample(session, specs)
        for arm, (reply, _polish) in ARMS.items():
            worker = build_worker_from_credential(record, settings.model_copy(update={"translation_reply_format": reply}))
            workers[arm] = (worker, _Meter(worker.client))
        session.rollback()

    def one(packet_id: str) -> dict:
        result = {"packet_id": packet_id, "arms": {}}
        for arm, (_reply, polish_reply) in ARMS.items():
            worker, meter = workers[arm]
            meter.start()
            with app.state.session_factory() as session:
                try:
                    service = TranslationService(TranslationRepository(session), worker=worker, polish_reply=polish_reply)
                    artifacts = service.execute_packet(packet_id)
                    bundle = TranslationRepository(session).load_packet_bundle(packet_id)
                    run = artifacts.translation_run
                    result["source"] = " ".join(sentence.source_text or "" for sentence in bundle.current_sentences)
                    covered = {edge.sentence_id for edge in artifacts.alignment_edges}
                    result["arms"][arm] = {
                        "text": "".join(segment.text_zh for segment in artifacts.target_segments),
                        "covered": len(covered & {sentence.id for sentence in bundle.current_sentences}),
                        "sentences": len(bundle.current_sentences),
                        "polish": (run.output_json or {}).get("polish"),
                        "rejected": len((run.output_json or {}).get("rejected_outputs") or []),
                        "error": run.error_code,
                        "calls": meter.calls(),
                    }
                except Exception as exc:  # a failed packet is a result too
                    result["arms"][arm] = {"error": f"{type(exc).__name__}: {exc}"[:300], "calls": meter.calls()}
                finally:
                    session.rollback()
        print(packet_id, {arm: ("ok" if not data.get("error") else data["error"][:40]) for arm, data in result["arms"].items()}, flush=True)
        return result

    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(one, packets))
    json.dump(results, open(out, "w"), ensure_ascii=False, indent=1, default=str)
    summarize(results)


def summarize(results: list[dict]) -> None:
    for arm in ARMS:
        rows = [r["arms"].get(arm, {}) for r in results]
        calls = [call for row in rows for call in row.get("calls", [])]
        ok = [row for row in rows if not row.get("error")]
        polished = [row["polish"] for row in ok if isinstance(row.get("polish"), dict)]
        hit = sum(call["hit"] or 0 for call in calls)
        miss = sum(call["miss"] or 0 for call in calls)
        print(
            f"{arm}: packets {len(rows)} ok {len(ok)} | calls {len(calls)} out {sum(c['out'] for c in calls)} in {sum(c['in'] for c in calls)}"
            f" cost ${sum(c['cost'] for c in calls):.4f} hit {100 * hit / max(1, hit + miss):.0f}%"
            f" | coverage {sum(row['covered'] for row in ok)}/{sum(row['sentences'] for row in ok)}"
            f" | rejected {sum(row['rejected'] for row in ok)}"
            f" | polish applied {sum(1 for p in polished if p.get('applied'))}/{len(polished)}"
            f" changed-any {sum(1 for p in polished if p.get('changed_segments'))}"
        )


def judge(out: str, judged_path: str, model: str) -> None:
    app = create_app()
    settings = get_settings()
    _ensure_database_state(app, settings=settings)
    with app.state.session_factory() as session:
        record = session.query(ProviderCredential).filter(ProviderCredential.is_active.is_(True)).first()
        client = build_worker_from_credential(record, settings).client
        session.rollback()
    results = json.load(open(out))
    rng = random.Random("ab-judge")
    jobs = []
    for result in results:
        arms = result["arms"]
        for pair in (("A", "B"), ("A", "A2")):
            if all(arms.get(arm) and not arms[arm].get("error") for arm in pair):
                swap = rng.random() < 0.5
                jobs.append((result, pair, swap))

    def ask(job):
        result, (first, second), swap = job
        x, y = (second, first) if swap else (first, second)
        prompt = f"英文原文：\n{result['source']}\n\n译文 X：\n{result['arms'][x]['text']}\n\n译文 Y：\n{result['arms'][y]['text']}"
        try:
            payload, usage = client.generate_structured_object(
                model_name=model, system_prompt=JUDGE_SYSTEM, user_prompt=prompt, response_schema=JUDGE_SCHEMA, schema_name="ab_judgement"
            )
            winner = {"X": x, "Y": y}.get(payload.get("winner"), "tie")
            return {"packet_id": result["packet_id"], "pair": f"{second}vs{first}", "winner": winner, "reason": payload.get("reason"), "cost": usage.cost_usd or 0}
        except Exception as exc:
            return {"packet_id": result["packet_id"], "pair": f"{second}vs{first}", "error": str(exc)[:200]}

    with ThreadPoolExecutor(4) as pool:
        verdicts = list(pool.map(ask, jobs))
    json.dump(verdicts, open(judged_path, "w"), ensure_ascii=False, indent=1)
    for pair, challenger in (("BvsA", "B"), ("A2vsA", "A2")):
        rows = [v for v in verdicts if v["pair"] == pair and "winner" in v]
        wins = sum(1 for v in rows if v["winner"] == challenger)
        losses = sum(1 for v in rows if v["winner"] == "A")
        print(f"{pair}: {challenger} better {wins}, A better {losses}, tie {len(rows) - wins - losses} (n={len(rows)})")
    print(f"judge cost ${sum(v.get('cost', 0) for v in verdicts):.4f}, errors {sum(1 for v in verdicts if 'error' in v)}")


if __name__ == "__main__":
    command = sys.argv[1]
    if command == "translate":
        translate(sys.argv[2], sys.argv[3:])
    elif command == "judge":
        model = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else "deepseek-v4-pro"
        judge(sys.argv[2], sys.argv[3], model)
    elif command == "summary":
        summarize(json.load(open(sys.argv[2])))
