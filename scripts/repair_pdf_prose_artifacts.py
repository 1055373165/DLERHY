from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from book_agent.core.config import get_settings
from book_agent.domain.enums import ExportType
from book_agent.infra.db.session import build_engine, build_session_factory, session_scope
from book_agent.services.pdf_prose_artifact_repair import PdfProseArtifactRepairService
from book_agent.services.workflows import DocumentWorkflowService
from book_agent.workers.factory import build_translation_worker


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Repair persisted PDF prose continuations misclassified as artifacts.")
    parser.add_argument("--input-db", required=True)
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--report-path", required=True)
    parser.add_argument("--candidate-start", type=int, default=0)
    parser.add_argument("--candidate-stop", type=int, default=None)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    input_db = Path(args.input_db).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_db = output_dir / "book-agent.db"
    shutil.copy2(input_db, output_db)

    database_url = f"sqlite+pysqlite:///{output_db}"
    engine = build_engine(database_url)
    session_factory = build_session_factory(engine)
    settings = get_settings()

    with session_scope(session_factory) as session:
        repair_service = PdfProseArtifactRepairService(
            session,
            worker=build_translation_worker(settings),
        )
        all_candidates = repair_service.scan_document(args.document_id)
        selected_candidates = all_candidates[args.candidate_start:args.candidate_stop]
        print(
            json.dumps(
                {
                    "candidate_total": len(all_candidates),
                    "candidate_selected": len(selected_candidates),
                    "candidate_start": args.candidate_start,
                    "candidate_stop": args.candidate_stop,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        def _progress(index: int, total: int, candidate, status: str, detail: str | None) -> None:
            label = f"[{index}/{total}] {candidate.mode} ch{candidate.chapter_ordinal}"
            if candidate.chapter_title:
                label += f" {candidate.chapter_title}"
            if detail:
                print(f"{label} -> {status}: {detail}", flush=True)
            else:
                print(f"{label} -> {status}", flush=True)

        repair_result = repair_service.apply(
            args.document_id,
            candidates=selected_candidates,
            progress_callback=_progress,
        )
        workflow = DocumentWorkflowService(
            session,
            export_root=str(output_dir / "exports"),
            translation_worker=build_translation_worker(settings),
        )
        markdown_export = workflow.export_document(args.document_id, ExportType.MERGED_MARKDOWN)
        html_export = workflow.export_document(args.document_id, ExportType.MERGED_HTML)
        report = {
            "document_id": args.document_id,
            "input_db": str(input_db),
            "output_db": str(output_db),
            "repair_result": {
                "candidate_total": len(all_candidates),
                "candidate_selected": len(selected_candidates),
                "candidate_start": args.candidate_start,
                "candidate_stop": args.candidate_stop,
                "candidate_count": repair_result.candidate_count,
                "repaired_chain_count": repair_result.repaired_chain_count,
                "repaired_block_count": len(repair_result.repaired_block_ids),
                "repaired_block_ids": repair_result.repaired_block_ids,
                "skipped_block_ids": repair_result.skipped_block_ids,
                "token_in": repair_result.token_in,
                "token_out": repair_result.token_out,
                "total_cost_usd": repair_result.total_cost_usd,
                "failed_candidates": repair_result.failed_candidates,
            },
            "exports": {
                "merged_markdown": markdown_export.file_path,
                "merged_markdown_manifest": markdown_export.manifest_path,
                "merged_html": html_export.file_path,
                "merged_html_manifest": html_export.manifest_path,
            },
        }
        Path(args.report_path).expanduser().resolve().write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
