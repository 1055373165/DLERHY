"""Translate the 67 Chapter-1 packets in parallel via the translation service.

Each worker thread owns its own DB session. The serial path was bound by
NVIDIA NIM streaming latency (~30-90 s per packet); 4-way concurrency
should bring wall time to ~15-25 minutes for the chapter.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# urllib reads proxy env at request time. The user's local proxy adds
# ~3 s per call and rate-limits long stretches; bypass it.
for _proxy_var in (
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
):
    os.environ.pop(_proxy_var, None)
os.environ["NO_PROXY"] = "*"

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sqlalchemy import select

from book_agent.core.config import get_settings
from book_agent.domain.enums import PacketStatus
from book_agent.domain.models.translation import TranslationPacket
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory, session_scope
from book_agent.services.workflows import DocumentWorkflowService
from book_agent.workers.factory import build_translation_worker


PACKET_IDS_FILE = Path("/tmp/ch1-packet-ids.json")
EXPORT_ROOT = ROOT / ".test-tmp" / "ch1-export"
WORKER_COUNT = int(os.environ.get("CH1_PARALLEL", "4"))


_print_lock = threading.Lock()


def _logp(message: str) -> None:
    with _print_lock:
        print(message, flush=True)


def _load_packet_ids() -> list[str]:
    raw = json.loads(PACKET_IDS_FILE.read_text(encoding="utf-8"))
    return [str(x) for x in (raw if isinstance(raw, list) else raw["packet_ids"])]


def _packet_status(session, packet_id: str) -> str | None:
    row = session.execute(
        select(TranslationPacket.status).where(TranslationPacket.id == packet_id)
    ).first()
    if row is None:
        return None
    status = row[0]
    return status.value if hasattr(status, "value") else str(status)


def _translate_one(
    *,
    factory,
    worker,
    packet_id: str,
    index: int,
    total: int,
) -> tuple[str, str, float, int, str | None]:
    """Returns (packet_id, outcome, elapsed_seconds, segment_count, first_zh|error)."""
    with session_scope(factory) as session:
        current_status = _packet_status(session, packet_id)
        if current_status != PacketStatus.BUILT.value:
            _logp(
                f"[ch1-smoke] {index}/{total} skip {packet_id[:8]} already={current_status}"
            )
            return packet_id, "skipped", 0.0, 0, current_status
        service = DocumentWorkflowService(
            session,
            export_root=str(EXPORT_ROOT),
            translation_worker=worker,
        )
        t0 = time.time()
        try:
            artifacts = service.translation_service.execute_packet(packet_id)
        except Exception as exc:
            elapsed = time.time() - t0
            error_summary = f"{type(exc).__name__}: {str(exc)[:160]}"
            _logp(
                f"[ch1-smoke] {index}/{total} FAIL {packet_id[:8]} {elapsed:5.1f}s "
                f"{error_summary}"
            )
            return packet_id, "failed", elapsed, 0, error_summary
        elapsed = time.time() - t0
        first_zh = (
            artifacts.target_segments[0].text_zh if artifacts.target_segments else "(empty)"
        )
        _logp(
            f"[ch1-smoke] {index}/{total} ok   {packet_id[:8]} {elapsed:5.1f}s "
            f"segs={len(artifacts.target_segments)} first={first_zh[:60]}"
        )
        return packet_id, "ok", elapsed, len(artifacts.target_segments), first_zh


def main() -> int:
    EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
    packet_ids = _load_packet_ids()
    _logp(
        f"[ch1-smoke] loaded {len(packet_ids)} packet ids; workers={WORKER_COUNT}"
    )

    settings = get_settings()
    engine = build_engine(database_url=settings.database_url)
    Base.metadata.create_all(engine)
    factory = build_session_factory(engine=engine)
    worker = build_translation_worker(settings)

    overall_t0 = time.time()
    success = 0
    skipped = 0
    failed: list[tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=WORKER_COUNT) as pool:
        future_to_index = {
            pool.submit(
                _translate_one,
                factory=factory,
                worker=worker,
                packet_id=pid,
                index=i + 1,
                total=len(packet_ids),
            ): (i + 1, pid)
            for i, pid in enumerate(packet_ids)
        }
        for future in as_completed(future_to_index):
            _, packet_id = future_to_index[future]
            try:
                _, outcome, _elapsed, _segs, info = future.result()
            except Exception as exc:  # pragma: no cover - defensive
                failed.append((packet_id, f"{type(exc).__name__}: {str(exc)[:160]}"))
                continue
            if outcome == "ok":
                success += 1
            elif outcome == "skipped":
                skipped += 1
            else:
                failed.append((packet_id, info or ""))
    overall_elapsed = time.time() - overall_t0
    _logp(
        json.dumps(
            {
                "stage": "translation_done",
                "elapsed_seconds": round(overall_elapsed, 1),
                "success_count": success,
                "skipped_count": skipped,
                "failed_count": len(failed),
                "failed_summary": [
                    {"packet_id": pid, "error": err} for pid, err in failed[:10]
                ],
            },
            ensure_ascii=False,
        )
    )
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
