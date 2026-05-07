"""Drive the official ExportService for bilingual markdown.

Replaces the throwaway scripts/export_chapter_md.py I wrote: this one
calls ``workflow.export_service.export_chapter(chapter_id,
ExportType.BILINGUAL_MARKDOWN)`` so the output goes through the same
``_render_block_markdown`` + ``_markdown_details_source`` path that
the project has been carefully tuning (figure cropping, <details>
folding, etc.).

Usage:
    bash scripts/export_chapter_md_official.sh ch1
    bash scripts/export_chapter_md_official.sh ch2

Reads chapters_config.json for chapter_id; writes through the
service's canonical export root (``.test-tmp/export-out/<doc_id>/``).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.core.config import get_settings  # noqa: E402
from book_agent.domain.enums import ExportType  # noqa: E402
from book_agent.infra.db.session import (  # noqa: E402
    build_engine,
    build_session_factory,
    session_scope,
)
from book_agent.services.workflows import DocumentWorkflowService  # noqa: E402
from book_agent.workers.factory import build_translation_worker  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "chapter_key", help='Chapter key in chapters_config.json (e.g. "ch1")'
    )
    p.add_argument(
        "--export-root",
        type=Path,
        default=ROOT / ".test-tmp" / "export-out",
    )
    p.add_argument(
        "--type",
        choices=["bilingual_markdown", "bilingual_html", "merged_markdown"],
        default="bilingual_markdown",
    )
    args = p.parse_args()

    cfg_path = ROOT / "scripts" / "chapters_config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    if args.chapter_key not in cfg["chapters"]:
        print(f"unknown chapter: {args.chapter_key}", file=sys.stderr)
        return 2
    ch = cfg["chapters"][args.chapter_key]
    chapter_id = ch["chapter_id"]
    document_id = cfg["document_id"]

    settings = get_settings()
    engine = build_engine(database_url=settings.database_url)
    factory = build_session_factory(engine=engine)
    args.export_root.mkdir(parents=True, exist_ok=True)

    type_map = {
        "bilingual_markdown": ExportType.BILINGUAL_MARKDOWN,
        "bilingual_html": ExportType.BILINGUAL_HTML,
        "merged_markdown": None,  # special: use export_document_merged_markdown
    }
    et = type_map[args.type]

    with session_scope(factory) as session:
        workflow = DocumentWorkflowService(
            session,
            export_root=str(args.export_root),
            translation_worker=build_translation_worker(settings),
        )
        if args.type == "merged_markdown":
            artifact = workflow.export_service.export_document_merged_markdown(
                document_id
            )
        else:
            artifact = workflow.export_service.export_chapter(chapter_id, et)

        print(f"[md-official] export_type={args.type}")
        print(f"[md-official] document_id={document_id}")
        print(f"[md-official] chapter_id={chapter_id}")
        print(f"[md-official] artifact={artifact}")
        # ExportArtifacts dataclass exposes file paths; print whatever's there.
        for k in dir(artifact):
            if k.startswith("_"):
                continue
            v = getattr(artifact, k, None)
            if isinstance(v, (str, Path)) and v:
                print(f"  {k} = {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
