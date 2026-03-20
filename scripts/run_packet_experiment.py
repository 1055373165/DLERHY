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
from book_agent.workers.contracts import ConceptCandidate
from book_agent.workers.factory import build_translation_worker


def _parse_concept_override(raw: str) -> ConceptCandidate:
    if "=" not in raw:
        raise argparse.ArgumentTypeError("Concept override must use source_term=canonical_zh format.")
    source_term, canonical_zh = raw.split("=", 1)
    source_term = source_term.strip()
    canonical_zh = canonical_zh.strip()
    if not source_term or not canonical_zh:
        raise argparse.ArgumentTypeError("Concept override must include both source_term and canonical_zh.")
    return ConceptCandidate(
        source_term=source_term,
        canonical_zh=canonical_zh,
        status="locked",
        confidence=1.0,
    )


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Run a single-packet translation experiment without touching the main translation pipeline."
    )
    parser.add_argument("--packet-id", required=True)
    parser.add_argument(
        "--output-path",
        type=Path,
        required=True,
        help="Where to write the experiment JSON artifact.",
    )
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
    parser.add_argument(
        "--prompt-profile",
        choices=(
            "current",
            "role-style-v2",
            "role-style-memory-v2",
            "role-style-brief-v3",
            "material-aware-v1",
            "material-aware-minimal-v1",
        ),
        default="role-style-v2",
    )
    parser.add_argument(
        "--material-profile",
        choices=(
            "general_nonfiction",
            "technical_book",
            "academic_paper",
            "technical_blog",
            "business_document",
        ),
        default=None,
        help="Override packet translation_material for prompt A/B experiments without mutating the database.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually call the configured translation worker. Defaults to dry-run prompt/context export only.",
    )
    parser.add_argument(
        "--disable-memory-blocks",
        action="store_true",
        help="Disable recent accepted translations when compiling chapter context.",
    )
    parser.add_argument(
        "--disable-chapter-concepts",
        action="store_true",
        help="Disable chapter concept memory when compiling chapter context.",
    )
    parser.add_argument(
        "--disable-memory-brief",
        action="store_true",
        help="Use packet brief instead of the latest chapter memory brief.",
    )
    parser.add_argument(
        "--disable-prev-translation-primary",
        action="store_true",
        help="Do not let previous accepted translations suppress raw prev/next source context and chapter brief.",
    )
    parser.add_argument(
        "--disable-paragraph-intent",
        action="store_true",
        help="Disable paragraph intent signal inference when compiling packet context.",
    )
    parser.add_argument(
        "--disable-literalism-guardrails",
        action="store_true",
        help="Disable source-aware literalism guardrails when compiling packet context.",
    )
    parser.add_argument(
        "--concept-override",
        action="append",
        type=_parse_concept_override,
        default=[],
        help="Temporary concept override in source_term=canonical_zh form. Can be repeated.",
    )
    parser.add_argument(
        "--rerun-hint",
        action="append",
        default=[],
        help="Temporary rerun hint injected into packet open_questions. Can be repeated.",
    )
    args = parser.parse_args()
    args.output_path = args.output_path.resolve()

    engine = build_engine(args.database_url)
    session_factory = build_session_factory(engine=engine)
    worker = build_translation_worker(settings) if args.execute else None
    try:
        with session_factory() as session:
            service = PacketExperimentService(
                TranslationRepository(session),
                settings=settings,
                worker=worker,
            )
            artifacts = service.run(
                args.packet_id,
                PacketExperimentOptions(
                    include_memory_blocks=not args.disable_memory_blocks,
                    include_chapter_concepts=not args.disable_chapter_concepts,
                    prefer_memory_chapter_brief=not args.disable_memory_brief,
                    prefer_previous_translations_over_source_context=(
                        not args.disable_prev_translation_primary
                    ),
                    include_paragraph_intent=not args.disable_paragraph_intent,
                    include_literalism_guardrails=not args.disable_literalism_guardrails,
                    prompt_layout=args.prompt_layout,
                    prompt_profile=args.prompt_profile,
                    material_profile_override=args.material_profile,
                    execute=args.execute,
                    concept_overrides=tuple(args.concept_override),
                    rerun_hints=tuple(str(item) for item in args.rerun_hint),
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
