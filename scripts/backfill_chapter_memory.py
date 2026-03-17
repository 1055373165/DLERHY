from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.core.config import get_settings
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.translation import TranslationRepository
from book_agent.services.chapter_memory_backfill import ChapterMemoryBackfillService


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Backfill chapter-local translation memory from existing translated packets without rerunning the model."
    )
    parser.add_argument("--chapter-id", required=True)
    parser.add_argument("--output-path", type=Path, required=True)
    parser.add_argument(
        "--database-url",
        default=settings.database_url,
        help="SQLAlchemy database URL. Defaults to current settings.database_url.",
    )
    parser.add_argument(
        "--reset-existing",
        action="store_true",
        help="Supersede the latest chapter translation memory snapshot and rebuild it from translated packets.",
    )
    args = parser.parse_args()
    args.output_path = args.output_path.resolve()

    engine = build_engine(args.database_url)
    session_factory = build_session_factory(engine=engine)
    try:
        with session_factory() as session:
            service = ChapterMemoryBackfillService(TranslationRepository(session))
            artifacts = service.backfill_chapter_with_options(
                args.chapter_id,
                reset_existing=args.reset_existing,
            )
            session.commit()

        args.output_path.parent.mkdir(parents=True, exist_ok=True)
        args.output_path.write_text(
            json.dumps(artifacts.payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(args.output_path)
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
