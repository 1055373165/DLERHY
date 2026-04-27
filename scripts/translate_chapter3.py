"""Translate the Chapter-3 packet range for the lumped llm-book chapter.

The recovery service merges the entire book body into one DB chapter,
so ``translate_document`` would translate ~850 packets. We only need
the Chapter-3 slice (Transformers: pages ~51-70) to verify the
Figure 3.2/3.3 fix end-to-end. This script computes the packet IDs
whose first sentence falls in the Chapter-3 ordinal range and feeds
them to ``translate_document`` via the ``packet_ids`` filter.
"""
# ruff: noqa: E402
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

for _v in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
    os.environ.pop(_v, None)

from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.core.config import get_settings
from book_agent.domain.enums import PacketStatus
from book_agent.domain.models import Block, Sentence
from book_agent.domain.models.translation import PacketSentenceMap, TranslationPacket
from book_agent.infra.db.session import build_engine, build_session_factory, session_scope
from book_agent.services.workflows import DocumentWorkflowService
from book_agent.workers.factory import build_translation_worker


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--document-id", default="d71027f0-6537-58d1-8e47-42ef2834fca4")
    parser.add_argument("--chapter-id", default="de30483c-ec5f-5d3d-a728-69de943db663")
    parser.add_argument("--ordinal-lo", type=int, default=219)
    parser.add_argument("--ordinal-hi", type=int, default=575)
    parser.add_argument("--limit", type=int, default=0,
                        help="Stop after this many packets (0 = no limit). Lets us "
                             "throttle so a long run can be cancelled cleanly.")
    args = parser.parse_args()

    settings = get_settings()
    engine = build_engine(database_url=settings.database_url)
    factory = build_session_factory(engine=engine)

    with session_scope(factory) as session:
        # Resolve packets whose ANY sentence sits inside the ordinal range.
        block_ids = session.execute(
            select(Block.id)
            .where(Block.chapter_id == args.chapter_id)
            .where(Block.ordinal >= args.ordinal_lo)
            .where(Block.ordinal <= args.ordinal_hi)
        ).scalars().all()
        if not block_ids:
            print("no blocks in range", file=sys.stderr)
            return 1
        sentence_ids = session.execute(
            select(Sentence.id).where(Sentence.block_id.in_(block_ids))
        ).scalars().all()
        if not sentence_ids:
            print("no sentences in range", file=sys.stderr)
            return 1
        packet_ids = sorted(set(session.execute(
            select(PacketSentenceMap.packet_id)
            .where(PacketSentenceMap.sentence_id.in_(sentence_ids))
        ).scalars().all()))
        # Only run BUILT packets — skip TRANSLATED/FAILED to keep this re-runnable.
        built = session.execute(
            select(TranslationPacket.id).where(TranslationPacket.id.in_(packet_ids))
            .where(TranslationPacket.status == PacketStatus.BUILT)
        ).scalars().all()
        print(f"[translate] resolved {len(packet_ids)} packets in ordinal range; "
              f"{len(built)} are still BUILT")
        if args.limit and args.limit > 0:
            built = built[:args.limit]
            print(f"[translate] limited to first {len(built)}")

    # Translate one packet at a time so a kill mid-run leaves the DB in a
    # consistent state (each packet is its own transaction). We don't use
    # translate_document because it would attempt every BUILT packet in
    # the document — including chapters outside this range.
    translated = 0
    failed = 0
    started = time.monotonic()
    for idx, packet_id in enumerate(built, start=1):
        with session_scope(factory) as session:
            service = DocumentWorkflowService(
                session,
                export_root=str(ROOT / "artifacts/exports"),
                translation_worker=build_translation_worker(settings),
            )
            try:
                artifacts = service.translation_service.execute_packet(packet_id)
                translated += 1
                if idx % 5 == 0 or idx == 1 or idx == len(built):
                    elapsed = time.monotonic() - started
                    rate = idx / max(elapsed, 1.0)
                    eta = (len(built) - idx) / max(rate, 0.001)
                    print(
                        f"[translate] {idx}/{len(built)} packet={packet_id} "
                        f"elapsed={elapsed:.0f}s eta={eta:.0f}s "
                        f"sentences_updated={len(artifacts.updated_sentences)}",
                        flush=True,
                    )
            except Exception as exc:
                failed += 1
                print(f"[translate] FAILED {packet_id}: {exc}", file=sys.stderr, flush=True)

    print(json.dumps({
        "packets_translated": translated,
        "packets_failed": failed,
        "elapsed_s": round(time.monotonic() - started, 1),
    }))
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
