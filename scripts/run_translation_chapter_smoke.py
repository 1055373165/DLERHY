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
from book_agent.services.packet_experiment import PacketExperimentService
from book_agent.services.packet_experiment_scan import PacketExperimentScanService
from book_agent.services.translation_chapter_smoke import (
    TranslationChapterSmokeOptions,
    TranslationChapterSmokeService,
)
from book_agent.workers.factory import build_translation_worker


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Run a constrained chapter-level translation smoke using packet experiments."
    )
    parser.add_argument("--chapter-id", required=True)
    parser.add_argument("--output-path", type=Path, required=True)
    parser.add_argument(
        "--database-url",
        default=settings.database_url,
        help="SQLAlchemy database URL. Defaults to current settings.database_url.",
    )
    parser.add_argument("--selected-packet-limit", type=int, default=3)
    parser.add_argument("--no-execute", action="store_true")
    parser.add_argument(
        "--prompt-layout",
        choices=("paragraph-led", "sentence-led"),
        default="paragraph-led",
    )
    parser.add_argument(
        "--prompt-profile",
        choices=("current", "role-style-v2", "role-style-memory-v2", "role-style-brief-v3"),
        default="role-style-v2",
    )
    parser.add_argument("--disable-memory-blocks", action="store_true")
    parser.add_argument("--disable-chapter-concepts", action="store_true")
    parser.add_argument("--disable-memory-brief", action="store_true")
    parser.add_argument("--disable-paragraph-intent", action="store_true")
    parser.add_argument("--disable-literalism-guardrails", action="store_true")
    parser.add_argument(
        "--packet-id",
        dest="packet_ids",
        action="append",
        default=[],
        help="Explicit packet ids to execute in order. If omitted, select top packets by chapter scan.",
    )
    args = parser.parse_args()
    args.output_path = args.output_path.resolve()

    engine = build_engine(args.database_url)
    session_factory = build_session_factory(engine=engine)
    try:
        with session_factory() as session:
            repository = TranslationRepository(session)
            experiment_service = PacketExperimentService(
                repository,
                settings=settings,
                worker=build_translation_worker(settings),
            )
            smoke_service = TranslationChapterSmokeService(
                experiment_service=experiment_service,
                scan_service=PacketExperimentScanService(
                    repository,
                    experiment_service=PacketExperimentService(
                        repository,
                        settings=settings,
                    ),
                ),
            )
            artifacts = smoke_service.run_chapter(
                args.chapter_id,
                options=TranslationChapterSmokeOptions(
                    selected_packet_limit=args.selected_packet_limit,
                    execute_selected=not args.no_execute,
                    include_memory_blocks=not args.disable_memory_blocks,
                    include_chapter_concepts=not args.disable_chapter_concepts,
                    prefer_memory_chapter_brief=not args.disable_memory_brief,
                    include_paragraph_intent=not args.disable_paragraph_intent,
                    include_literalism_guardrails=not args.disable_literalism_guardrails,
                    prompt_layout=args.prompt_layout,
                    prompt_profile=args.prompt_profile,
                    explicit_packet_ids=tuple(args.packet_ids),
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
