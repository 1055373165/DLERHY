"""Ablation: the same packets with and without alignment_suggestions + packet_id in the reply.
Real calls, nothing stored; replies are captured raw (no parsing, no retries)."""
import json, sys, copy
from book_agent.app.main import create_app, _ensure_database_state
from book_agent.core.config import get_settings
from book_agent.services.workflows import DocumentWorkflowService
from book_agent.workers.translator import build_translation_prompt_request
from sqlalchemy import text

class Captured(Exception):
    pass

doc, ordinal = sys.argv[1], int(sys.argv[2])
app = create_app(); _ensure_database_state(app, settings=get_settings())
worker = app.state.resolve_translation_worker("00000000-0000-4000-8000-000000000001")
client = worker.client
meta = worker.metadata(); profile = (meta.runtime_config or {}).get("prompt_profile", "tech-column-meta-v1")
with app.state.session_factory() as session:
    wf = DocumentWorkflowService(session, export_root="artifacts/exports", translation_worker=worker)
    ids = session.execute(text("""select p.id from translation_packets p join chapters c on c.id=p.chapter_id join blocks b on b.id=p.block_start_id
        where c.document_id=:d and c.ordinal=:o and p.status='translated' order by b.ordinal offset 40 limit 10"""), {"d": doc, "o": ordinal}).scalars().all()
    reqs = [build_translation_prompt_request(wf.translation_service.prepare_packet(i).task, model_name=meta.model_name, prompt_version="x", prompt_profile=profile) for i in ids]
    session.rollback()

def slim_schema(schema):
    s = copy.deepcopy(schema)
    for key in ("packet_id", "alignment_suggestions"):
        s.get("properties", {}).pop(key, None)
    s["required"] = [k for k in s.get("required", []) if k not in ("packet_id", "alignment_suggestions")]
    return s

orig_build = client._build_payload
mode = {"slim": False}
def build(request, *, api_mode):
    payload = orig_build(request, api_mode=api_mode)
    if not mode["slim"]:
        return payload
    full = json.dumps(request.response_schema, ensure_ascii=False, separators=(",", ":"))
    slim = json.dumps(slim_schema(request.response_schema), ensure_ascii=False, separators=(",", ":"))
    for m in payload["messages"]:
        c = m["content"]
        c = c.replace(full, slim)
        c = c.replace("packet_id, target_segments, alignment_suggestions, low_confidence_flags, notes", "target_segments, low_confidence_flags, notes")
        c = c.replace("packet_id must equal the value given at the end of the user message.\n", "")
        c = c.replace("Every current source sentence must be covered through target_segments and alignment_suggestions.",
                      "Every current source sentence must be covered by exactly one target segment's source_sentence_ids.")
        c = c.replace(" in source_sentence_ids and low_confidence_flags.sentence_id", " in target_segments.source_sentence_ids and low_confidence_flags.sentence_id")
        c = "\n".join(line for line in c.split("\n") if not line.startswith("packet_id must equal:"))
        m["content"] = c
    rf = payload.get("response_format")
    if isinstance(rf, dict) and isinstance(rf.get("json_schema"), dict) and "schema" in rf["json_schema"]:
        rf["json_schema"]["schema"] = slim_schema(rf["json_schema"]["schema"])
    return payload
client._build_payload = build
rows = []
def spy(response, *, api_mode):
    raise Captured(json.dumps({"content": response["choices"][0]["message"]["content"], "usage": response.get("usage")}))
client._extract_output_payload = spy

for slim in (False, True):
    mode["slim"] = slim
    for n, r in enumerate(reqs):
        try:
            client.generate_translation(r)
        except Captured as c:
            d = json.loads(str(c)); rows.append({"slim": slim, "n": n, **d})
        except Exception as exc:
            rows.append({"slim": slim, "n": n, "error": f"{type(exc).__name__}: {exc}"[:200]})
json.dump(rows, open(sys.argv[0] + ".out.json", "w"), ensure_ascii=False)
for slim in (False, True):
    r = [x for x in rows if x["slim"] == slim and "usage" in x]
    out = sum(x["usage"]["completion_tokens"] for x in r); inp = sum(x["usage"]["prompt_tokens"] for x in r)
    print(("slim" if slim else "full"), "calls", len(r), "output tokens", out, "input tokens", inp, "errors", sum(1 for x in rows if x["slim"] == slim and "error" in x))
