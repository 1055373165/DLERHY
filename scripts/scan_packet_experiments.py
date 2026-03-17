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
from book_agent.services.packet_experiment import PacketExperimentOptions, PacketExperimentService
from book_agent.services.packet_experiment_scan import PacketExperimentScanService


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Scan a chapter's packets and rank which ones benefit most from chapter-local memory."
    )
    parser.add_argument("--chapter-id", required=True)
    parser.add_argument("--output-path", type=Path, required=True)
    parser.add_argument(
        "--database-url",
        default=settings.database_url,
        help="SQLAlchemy database URL. Defaults to current settings.database_url.",
    )
    parser.add_argument(
        "--prompt-layout",
        choices=("paragraph-led", "sentence-led"),
        default="paragraph-led",
    )
    parser.add_argument("--disable-memory-blocks", action="store_true")
    parser.add_argument("--disable-chapter-concepts", action="store_true")
    parser.add_argument("--disable-memory-brief", action="store_true")
    args = parser.parse_args()
    args.output_path = args.output_path.resolve()

    engine = build_engine(args.database_url)
    session_factory = build_session_factory(engine=engine)
    try:
        with session_factory() as session:
            experiment_service = PacketExperimentService(
                TranslationRepository(session),
                settings=settings,
            )
            scan_service = PacketExperimentScanService(
                TranslationRepository(session),
                experiment_service=experiment_service,
            )
            artifacts = scan_service.scan_chapter(
                args.chapter_id,
                options=PacketExperimentOptions(
                    include_memory_blocks=not args.disable_memory_blocks,
                    include_chapter_concepts=not args.disable_chapter_concepts,
                    prefer_memory_chapter_brief=not args.disable_memory_brief,
                    prompt_layout=args.prompt_layout,
                    execute=False,
                ),
            )
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
