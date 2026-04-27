"""Translate sentences that were skipped during the main packet pass.

The earlier parser revision flagged some body paragraphs as CODE blocks
(because they mentioned monospace identifiers like ``stock``/``capital``
inline). Those blocks had ``translatable=False`` set on every sentence,
so the packet builder skipped them and the translator never saw them.

After ``reclassify_misclassified_code.py`` flips the block_type back to
PARAGRAPH and re-marks the sentences as translatable, this script walks
the affected sentences, calls the OpenAI-compatible translation API one
sentence at a time, and writes the resulting TargetSegment +
AlignmentEdge rows so the export picks them up. Bypasses packets and
prompt scaffolding entirely — works on the small leftover set, not as
the main translation path.
"""
# ruff: noqa: E402
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

for _v in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
    os.environ.pop(_v, None)

from sqlalchemy import select

from book_agent.core.config import get_settings
from book_agent.domain.enums import TargetSegmentStatus
from book_agent.domain.models import Block, Sentence
from book_agent.domain.models.translation import (
    AlignmentEdge,
    TargetSegment,
    TranslationRun,
)
from book_agent.infra.db.session import build_engine, build_session_factory, session_scope


SYSTEM_PROMPT = (
    "You are a professional English-to-Chinese book translator. "
    "Translate the user's English sentence into clear, natural simplified Chinese (zh-CN). "
    "Preserve all inline code identifiers, numbers, and proper nouns verbatim "
    "(e.g. `stock`, `capital`, `dict`, `5.2`, `Figure 3.3`). "
    "Output ONLY the translated Chinese sentence — no quotes, no prefix, no explanation."
)


def call_openai_chat(api_key: str, base_url: str, model: str, source: str) -> str:
    """Call an OpenAI Chat Completions endpoint and return the assistant text."""
    normalized = base_url.rstrip("/")
    # Mirror the path resolution from
    # ``book_agent.workers.providers.openai_compatible``: drop ``/responses``
    # if the user pointed at OpenAI's responses URL, otherwise append
    # ``/chat/completions`` exactly once. Works for DeepSeek's
    # ``https://api.deepseek.com`` base URL.
    if normalized.endswith("/responses"):
        normalized = normalized[: -len("/responses")]
    if not normalized.endswith("/chat/completions"):
        normalized = normalized + "/chat/completions"
    chat_url = normalized

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": source},
        ],
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        chat_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body["choices"][0]["message"]["content"].strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ordinal-lo", type=int, default=int(os.environ.get("ORDINAL_LO", "0")))
    ap.add_argument("--ordinal-hi", type=int, default=int(os.environ.get("ORDINAL_HI", "10000")))
    ap.add_argument(
        "--chapter-id",
        default=os.environ.get("CHAPTER_ID", "de30483c-ec5f-5d3d-a728-69de943db663"),
    )
    ap.add_argument("--model", default=os.environ.get("BOOK_AGENT_TRANSLATION_OPENAI_MODEL", "deepseek-chat"))
    args = ap.parse_args()

    settings = get_settings()
    api_key = settings.translation_openai_api_key
    base_url = settings.translation_openai_base_url
    if not api_key:
        print("[orphan-translate] missing translation_openai_api_key", file=sys.stderr)
        return 2

    engine = build_engine(database_url=settings.database_url)
    factory = build_session_factory(engine=engine)

    translated = 0
    skipped = 0
    failed: list[tuple[str, str]] = []

    with session_scope(factory) as session:
        # Reuse an existing successful TranslationRun's id so the
        # NOT NULL FK on TargetSegment.translation_run_id is satisfied. The
        # exporter doesn't render through TranslationRun, so this attribution
        # is harmless cosmetic noise.
        run = session.execute(
            select(TranslationRun)
            .where(TranslationRun.status == "succeeded")
            .order_by(TranslationRun.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if run is None:
            print("[orphan-translate] no succeeded TranslationRun to attribute to", file=sys.stderr)
            return 3
        attribution_run_id = run.id
        print(f"[orphan-translate] attributing TargetSegments to run id={attribution_run_id}")

        # Pull blocks in range, filter to those whose sentences need translation.
        blocks = session.execute(
            select(Block)
            .where(Block.chapter_id == args.chapter_id)
            .where(Block.ordinal >= args.ordinal_lo)
            .where(Block.ordinal <= args.ordinal_hi)
            .order_by(Block.ordinal)
        ).scalars().all()
        for b in blocks:
            sents = session.execute(
                select(Sentence)
                .where(Sentence.block_id == b.id)
                .where(Sentence.translatable.is_(True))
                .order_by(Sentence.ordinal_in_block)
            ).scalars().all()
            for s in sents:
                # Skip if already translated.
                exists = session.execute(
                    select(AlignmentEdge.id)
                    .join(TargetSegment, AlignmentEdge.target_segment_id == TargetSegment.id)
                    .where(AlignmentEdge.sentence_id == s.id)
                    .where(TargetSegment.final_status != TargetSegmentStatus.SUPERSEDED)
                    .limit(1)
                ).first()
                if exists:
                    skipped += 1
                    continue
                src = (s.source_text or "").strip()
                if not src:
                    skipped += 1
                    continue
                try:
                    zh = call_openai_chat(api_key, base_url, args.model, src)
                except Exception as exc:  # noqa: BLE001
                    failed.append((str(s.id), repr(exc)))
                    continue
                if not zh:
                    failed.append((str(s.id), "empty-response"))
                    continue
                tgt = TargetSegment(
                    id=uuid.uuid4(),
                    chapter_id=args.chapter_id,
                    translation_run_id=attribution_run_id,
                    ordinal=int(b.ordinal * 100 + (s.ordinal_in_block or 0)),
                    text_zh=zh,
                    segment_type="sentence",
                    confidence=0.9,
                    final_status=TargetSegmentStatus.DRAFT,
                )
                session.add(tgt)
                session.flush()
                edge = AlignmentEdge(
                    id=uuid.uuid4(),
                    sentence_id=s.id,
                    target_segment_id=tgt.id,
                    relation_type="1:1",
                    confidence=0.9,
                    created_by="model",
                )
                session.add(edge)
                translated += 1
                print(
                    f"  ord={b.ordinal:3} sent.{s.ordinal_in_block} -> "
                    f"{zh[:80]!r}"
                )

    print(
        f"\n[orphan-translate] translated={translated} skipped={skipped} "
        f"failed={len(failed)}"
    )
    for sid, reason in failed:
        print(f"  FAIL {sid}: {reason}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
