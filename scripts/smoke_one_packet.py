"""One-packet smoke test for the streaming translation client.

Translates exactly one of the Chapter-1 packets and prints the resulting
target segments. Confirms the SSE / reassembly path before we kick off
the full 67-packet run.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.core.config import get_settings
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory, session_scope
from book_agent.services.workflows import DocumentWorkflowService
from book_agent.workers.factory import build_translation_worker


PACKET_IDS_FILE = Path("/tmp/ch1-packet-ids.json")


def main() -> int:
    packet_ids = json.loads(PACKET_IDS_FILE.read_text())
    target_packet = packet_ids[0]
    print(f"[smoke-one] target packet: {target_packet}", flush=True)

    settings = get_settings()
    print(
        f"[smoke-one] streaming={settings.translation_openai_streaming} "
        f"backend={settings.translation_backend} "
        f"model={settings.translation_model} "
        f"base_url={settings.translation_openai_base_url}",
        flush=True,
    )

    engine = build_engine(database_url=settings.database_url)
    Base.metadata.create_all(engine)
    factory = build_session_factory(engine=engine)

    with session_scope(factory) as session:
        service = DocumentWorkflowService(
            session,
            export_root=str(ROOT / ".test-tmp" / "smoke-one"),
            translation_worker=build_translation_worker(settings),
        )
        artifacts = service.translation_service.execute_packet(target_packet)
        print(
            json.dumps(
                {
                    "stage": "smoke_one_done",
                    "packet_id": target_packet,
                    "translation_run_id": artifacts.translation_run.id,
                    "target_segment_count": len(artifacts.target_segments),
                    "first_target_text": (
                        artifacts.target_segments[0].text_zh
                        if artifacts.target_segments
                        else None
                    ),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
