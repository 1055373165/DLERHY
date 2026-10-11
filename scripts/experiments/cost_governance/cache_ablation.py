"""Prompt-cache ablation: real calls, nothing stored. Arms per chapter: seq | par | warm.
Usage: cache_ablation.py <doc> <arm:chapter_ordinal> ... -> JSON lines with hit/miss per call."""
import json, sys, time
from concurrent.futures import ThreadPoolExecutor
from book_agent.app.main import create_app, _ensure_database_state
from book_agent.core.config import get_settings
from book_agent.services.workflows import DocumentWorkflowService
from book_agent.workers.translator import build_translation_prompt_request
from sqlalchemy import text

doc = sys.argv[1]; arms = [a.split(":") for a in sys.argv[2:]]
app = create_app(); _ensure_database_state(app, settings=get_settings())
worker = app.state.resolve_translation_worker("00000000-0000-4000-8000-000000000001")
meta = worker.metadata(); profile = (meta.runtime_config or {}).get("prompt_profile", "tech-column-meta-v1")
requests = {}
with app.state.session_factory() as session:
    wf = DocumentWorkflowService(session, export_root="artifacts/exports", translation_worker=worker)
    for arm, ordinal in arms:
        ids = session.execute(text("""select p.id from translation_packets p join chapters c on c.id=p.chapter_id
            join blocks b on b.id=p.block_start_id where c.document_id=:d and c.ordinal=:o and p.status='translated'
            order by b.ordinal limit 8"""), {"d": doc, "o": int(ordinal)}).scalars().all()
        requests[(arm, ordinal)] = [build_translation_prompt_request(wf.translation_service.prepare_packet(i).task,
            model_name=meta.model_name, prompt_version="ablation", prompt_profile=profile) for i in ids]
    session.rollback()

def call(arm, ordinal, n, req):
    t = time.time()
    try:
        res = worker.client.generate_translation(req)
        u = res.usage; raw = u.raw_usage or {}
        row = {"arm": arm, "chapter": ordinal, "n": n, "t": round(t, 2), "in": u.token_in, "out": u.token_out,
               "hit": raw.get("prompt_cache_hit_tokens"), "miss": raw.get("prompt_cache_miss_tokens"), "cost": u.cost_usd}
    except Exception as exc:
        row = {"arm": arm, "chapter": ordinal, "n": n, "error": str(exc)[:200]}
    print(json.dumps(row), flush=True); return row

for (arm, ordinal), reqs in requests.items():
    if arm == "seq":
        for n, r in enumerate(reqs): call(arm, ordinal, n, r)
    elif arm == "par":
        with ThreadPoolExecutor(4) as pool: list(pool.map(lambda x: call(arm, ordinal, *x), enumerate(reqs)))
    elif arm == "warm":
        call(arm, ordinal, 0, reqs[0]); time.sleep(5)
        with ThreadPoolExecutor(4) as pool: list(pool.map(lambda x: call(arm, ordinal, x[0] + 1, x[1]), enumerate(reqs[1:])))
