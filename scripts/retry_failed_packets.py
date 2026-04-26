"""Retry packets still in BUILT (i.e. failed in main run) for Chapter 1.

After the main translate_chapter1_smoke.py run finishes, some packets
remain in BUILT due to streaming timeouts or transient HTTP 502s. This
script walks the same 67-packet list, retries any still-BUILT one with
a longer per-call timeout (NVIDIA NIM occasionally produces slow streams
for these specific prompts).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

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
MAX_PASSES = int(os.environ.get("CH1_RETRY_PASSES", "3"))


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


def _still_built_packet_ids(factory, packet_ids: list[str]) -> list[str]:
    with session_scope(factory) as session:
        rows = session.execute(
            select(TranslationPacket.id, TranslationPacket.status).where(
                TranslationPacket.id.in_(packet_ids)
            )
        ).all()
    return [
        str(packet_id)
        for packet_id, status in rows
        if (status.value if hasattr(status, "value") else str(status))
        == PacketStatus.BUILT.value
    ]


def main() -> int:
    EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
    all_packet_ids = _load_packet_ids()

    settings = get_settings()
    engine = build_engine(database_url=settings.database_url)
    Base.metadata.create_all(engine)
    factory = build_session_factory(engine=engine)
    worker = build_translation_worker(settings)

    print(
        f"[ch1-retry] timeout={settings.translation_timeout_seconds}s "
        f"passes={MAX_PASSES}",
        flush=True,
    )

    for pass_num in range(1, MAX_PASSES + 1):
        pending = _still_built_packet_ids(factory, all_packet_ids)
        if not pending:
            print(f"[ch1-retry] pass {pass_num}: nothing to retry", flush=True)
            return 0
        print(
            f"[ch1-retry] pass {pass_num}/{MAX_PASSES}: {len(pending)} BUILT packets",
            flush=True,
        )
        success = 0
        failed: list[tuple[str, str]] = []
        for index, packet_id in enumerate(pending, start=1):
            with session_scope(factory) as session:
                current_status = _packet_status(session, packet_id)
                if current_status != PacketStatus.BUILT.value:
                    continue
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
                    error_summary = f"{type(exc).__name__}: {str(exc)[:120]}"
                    failed.append((packet_id, error_summary))
                    print(
                        f"[ch1-retry] pass{pass_num} {index}/{len(pending)} FAIL "
                        f"{packet_id[:8]} {elapsed:5.1f}s {error_summary}",
                        flush=True,
                    )
                    continue
                elapsed = time.time() - t0
                success += 1
                first_zh = (
                    artifacts.target_segments[0].text_zh
                    if artifacts.target_segments
                    else "(empty)"
                )
                print(
                    f"[ch1-retry] pass{pass_num} {index}/{len(pending)} ok   "
                    f"{packet_id[:8]} {elapsed:5.1f}s "
                    f"first={first_zh[:60]}",
                    flush=True,
                )
        print(
            f"[ch1-retry] pass {pass_num} done: success={success} failed={len(failed)}",
            flush=True,
        )
        if not failed:
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
