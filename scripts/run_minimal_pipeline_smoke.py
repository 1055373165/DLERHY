from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

os.environ.setdefault("BOOK_AGENT_TRANSLATION_BACKEND", "echo")
os.environ.setdefault("BOOK_AGENT_TRANSLATION_MODEL", "echo-worker")

from book_agent.domain.enums import ExportType
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory, session_scope
from book_agent.services.workflows import DocumentWorkflowService


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the minimal end-to-end translation smoke for EPUB and/or PDF."
    )
    parser.add_argument(
        "--case",
        choices=("all", "epub", "pdf"),
        default="all",
        help="Which minimal pipeline case to run.",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="PostgreSQL database URL. Defaults to BOOK_AGENT_DATABASE_URL from .env.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "artifacts" / "minimal_pipeline_smoke"),
        help="Directory where exports and the JSON report will be written.",
    )
    parser.add_argument(
        "--report-path",
        default=None,
        help="Optional explicit report JSON path. Defaults to <output-dir>/<timestamp>/report.json.",
    )
    return parser


def _run_case(
    case_name: str,
    source_path: Path,
    case_dir: Path,
    *,
    database_url: str | None,
) -> dict[str, Any]:
    engine = build_engine(database_url)
    Base.metadata.create_all(engine)
    session_factory = build_session_factory(engine=engine)
    export_root = case_dir / "exports"
    export_root.mkdir(parents=True, exist_ok=True)
    try:
        with session_scope(session_factory) as session:
            workflow = DocumentWorkflowService(session, export_root=export_root)

            # Stage 1: Bootstrap (Parse + Segment)
            summary = workflow.bootstrap_document(source_path)
            print(f"  ✓ Stage 1 — Parse: {summary.chapter_count} chapters, "
                  f"{summary.sentence_count} sentences, {summary.packet_count} packets")

            # Stage 2: Translate
            translate = workflow.translate_document(summary.document_id)
            print(f"  ✓ Stage 2 — Translate: {translate.translated_packet_count} packets translated")

            # Stage 3: Review
            review = workflow.review_document(summary.document_id)
            print(f"  ✓ Stage 3 — Review: {review.total_issue_count} issues found")

            # Stage 4: Bilingual Export
            export = workflow.export_document(summary.document_id, ExportType.BILINGUAL_HTML)
            chapter_result = export.chapter_results[0]
            print(f"  ✓ Stage 4 — Bilingual Export: {export.document_status}")

            # Stage 5: Merged HTML (中文阅读稿)
            merged = workflow.export_document(summary.document_id, ExportType.MERGED_HTML)
            print(f"  ✓ Stage 5 — Merged HTML: {merged.document_status}")

            return {
                "case": case_name,
                "sample_path": str(source_path),
                "sample_size_bytes": source_path.stat().st_size,
                "document_id": summary.document_id,
                "source_type": summary.source_type,
                "chapter_count": summary.chapter_count,
                "sentence_count": summary.sentence_count,
                "packet_count": summary.packet_count,
                "translated_packet_count": translate.translated_packet_count,
                "review_issue_count": review.total_issue_count,
                "bilingual_status": export.document_status,
                "merged_status": merged.document_status,
                "chapter_export_path": chapter_result.file_path,
                "chapter_manifest_path": chapter_result.manifest_path,
            }
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_root = output_dir / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_root.mkdir(parents=True, exist_ok=True)

    sample_dir = ROOT / "artifacts" / "smoke_samples"
    selected_cases = ["epub", "pdf"] if args.case == "all" else [args.case]
    results: list[dict[str, Any]] = []

    for case in selected_cases:
        source_path = sample_dir / f"minimal_pipeline.{case}"
        if not source_path.exists():
            print(f"⚠ Sample not found: {source_path}")
            continue
        print(f"\n{'='*60}")
        print(f"  Running {case.upper()} smoke test: {source_path.name}")
        print(f"{'='*60}")
        try:
            result = _run_case(
                case, source_path, run_root / case,
                database_url=args.database_url,
            )
            results.append(result)
            print(f"  ✅ {case.upper()} — ALL 5 STAGES PASSED")
        except Exception as exc:
            print(f"  ❌ {case.upper()} — FAILED: {exc}")
            import traceback
            traceback.print_exc()
            results.append({"case": case, "error": str(exc)})

    report_path = Path(args.report_path).resolve() if args.report_path else run_root / "report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'='*60}")
    print(f"  Report: {report_path}")
    print(f"{'='*60}")
    print(json.dumps({"report_path": str(report_path), "results": results}, ensure_ascii=False, indent=2))
    return 0 if all("error" not in r for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
