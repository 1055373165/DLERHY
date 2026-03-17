from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.core.config import get_settings
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.services.chapter_concept_lock import ChapterConceptLockService


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Lock a chapter concept translation directly into chapter memory without rerunning translation."
    )
    parser.add_argument("--chapter-id", required=True)
    parser.add_argument("--source-term", required=True)
    parser.add_argument("--canonical-zh", required=True)
    parser.add_argument("--status", default="locked")
    parser.add_argument(
        "--database-url",
        default=settings.database_url,
        help="SQLAlchemy database URL. Defaults to current settings.database_url.",
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        required=False,
        help="Optional JSON output path for the lock result.",
    )
    args = parser.parse_args()

    engine = build_engine(args.database_url)
    session_factory = build_session_factory(engine=engine)
    try:
        with session_factory() as session:
            result = ChapterConceptLockService(session).lock_concept(
                chapter_id=args.chapter_id,
                source_term=args.source_term,
                canonical_zh=args.canonical_zh,
                status=args.status,
            )
            session.commit()
        payload = {
            "generated_at": _utcnow_iso(),
            "chapter_id": result.chapter_id,
            "source_term": result.source_term,
            "canonical_zh": result.canonical_zh,
            "snapshot_version": result.snapshot_version,
            "created_new_concept": result.created_new_concept,
            "term_entry_id": result.term_entry_id,
            "term_entry_version": result.term_entry_version,
            "created_new_term_entry": result.created_new_term_entry,
            "database_url": args.database_url,
        }
        if args.output_path is not None:
            output_path = args.output_path.resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(output_path)
        else:
            print(json.dumps(payload, ensure_ascii=False))
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
