"""Raw model output of translate calls: share of output characters per field (real calls, nothing stored)."""
import json, sys, collections
from book_agent.app.main import create_app, _ensure_database_state
from book_agent.core.config import get_settings
from book_agent.services.workflows import DocumentWorkflowService
from book_agent.workers.translator import build_translation_prompt_request
from sqlalchemy import text
doc, ordinal = sys.argv[1], int(sys.argv[2])
app = create_app(); _ensure_database_state(app, settings=get_settings())
worker = app.state.resolve_translation_worker("00000000-0000-4000-8000-000000000001")
meta = worker.metadata(); profile = (meta.runtime_config or {}).get("prompt_profile", "tech-column-meta-v1")
captured = []
orig = worker.client._extract_output_payload
def spy(response, *, api_mode):
    captured.append(response["choices"][0]["message"]["content"]); return orig(response, api_mode=api_mode)
worker.client._extract_output_payload = spy
with app.state.session_factory() as session:
    wf = DocumentWorkflowService(session, export_root="artifacts/exports", translation_worker=worker)
    ids = session.execute(text("""select p.id from translation_packets p join chapters c on c.id=p.chapter_id join blocks b on b.id=p.block_start_id
        where c.document_id=:d and c.ordinal=:o and p.status='translated' order by b.ordinal offset 20 limit 8"""), {"d": doc, "o": ordinal}).scalars().all()
    reqs = [build_translation_prompt_request(wf.translation_service.prepare_packet(i).task, model_name=meta.model_name, prompt_version="x", prompt_profile=profile) for i in ids]
    session.rollback()
size = collections.Counter(); out_tokens = 0
for r in reqs:
    res = worker.client.generate_translation(r); out_tokens += res.usage.token_out
for raw in captured:
    d = json.loads(raw)
    for k, v in d.items(): size[k] += len(json.dumps(v, ensure_ascii=False, separators=(",", ":"))) + len(k) + 3
total = sum(size.values())
print("calls", len(captured), "output tokens", out_tokens)
for k, v in size.most_common(): print(f"  {k}: {100*v/total:.1f}% of output chars")
open(sys.argv[0] + ".sample.txt", "w").write(captured[0])
