from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from book_agent.core.config import get_settings
from book_agent.domain.enums import ExportType
from book_agent.infra.db.session import build_session_factory, session_scope
from book_agent.services.glossary_extraction import (
    GlossaryExtractionService,
    read_glossary_csv,
    write_glossary_csv,
)
from book_agent.services.glossary_service import GlossaryService
from book_agent.services.term_consistency import (
    TermConsistencyService,
    render_consistency_report_markdown,
)
from book_agent.services.workflows import DocumentWorkflowService
from book_agent.workers.factory import resolve_translation_worker


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "value"):
        return value.value
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"Unsupported JSON value: {type(value)!r}")


def _dump(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="book-agent admin CLI")
    parser.add_argument("--database-url", dest="database_url", default=None)
    parser.add_argument("--export-root", dest="export_root", default=None)

    subparsers = parser.add_subparsers(dest="command", required=True)

    bootstrap = subparsers.add_parser("bootstrap", help="Ingest and bootstrap an EPUB or text PDF")
    bootstrap.add_argument("--source-path", required=True)

    summary = subparsers.add_parser("summary", help="Show document summary")
    summary.add_argument("--document-id", required=True)

    translate = subparsers.add_parser("translate", help="Translate packets for a document")
    translate.add_argument("--document-id", required=True)
    translate.add_argument("--packet-id", action="append", default=[])

    review = subparsers.add_parser("review", help="Run chapter review for a document")
    review.add_argument("--document-id", required=True)

    export = subparsers.add_parser("export", help="Export a document")
    export.add_argument("--document-id", required=True)
    export.add_argument("--export-type", required=True, choices=[item.value for item in ExportType])
    export.add_argument("--auto-followup-on-gate", action="store_true")
    export.add_argument("--max-auto-followup-attempts", type=int, default=3)

    refresh_pdf_structure = subparsers.add_parser(
        "refresh-pdf-structure",
        help="Refresh persisted PDF structure metadata in place without rerunning translation",
    )
    refresh_pdf_structure.add_argument("--document-id", required=True)
    refresh_pdf_structure.add_argument("--chapter-id", action="append", default=[])

    glossary_extract = subparsers.add_parser(
        "glossary-extract",
        help="Propose a book-wide glossary with the translation provider and write it as a CSV for review",
    )
    glossary_extract.add_argument("--document-id", required=True)
    glossary_extract.add_argument("--output", required=True, help="CSV path to write")
    glossary_extract.add_argument("--max-chunk-chars", type=int, default=12000)

    glossary_lock = subparsers.add_parser(
        "glossary-lock",
        help="Lock the glossary rows marked in a reviewed CSV (review then flags and reruns conflicts)",
    )
    glossary_lock.add_argument("--document-id", required=True)
    glossary_lock.add_argument("--input", required=True, help="Reviewed CSV from glossary-extract")

    term_consistency = subparsers.add_parser(
        "term-consistency",
        help="Unify terminology across a translated document (extract, decide, minimally edit, lock, report)",
    )
    term_consistency.add_argument("--document-id", required=True)
    term_consistency.add_argument("--report", required=True, help="Markdown report path")
    term_consistency.add_argument("--dry-run", action="store_true", help="Measure and propose without editing or locking")

    api_key = subparsers.add_parser(
        "create-api-key",
        help="Create an API key (and its organisation if missing); prints the key once",
    )
    api_key.add_argument("--name", required=True)
    api_key.add_argument("--role", choices=["viewer", "editor", "admin"], default="admin")
    api_key.add_argument("--org", default="default", help="Organisation name; created if missing")

    subparsers.add_parser(
        "mcp",
        help="Serve book-agent's read and reversible tools over MCP (stdio) for Claude Code, Codex and other clients",
    )

    evaluate = subparsers.add_parser("eval", help="Run the release evals (evals/README.md) and write a report")
    evaluate.add_argument("--suite", action="append", default=[], help="terminology, review, structure or export; repeatable")
    evaluate.add_argument("--output", default=None, help="Report directory (default evals/reports/<timestamp>)")

    action = subparsers.add_parser("execute-action", help="Execute a planned issue action")
    action.add_argument("--action-id", required=True)
    action.add_argument("--run-followup", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = get_settings()
    session_factory = build_session_factory(database_url=args.database_url or settings.database_url)
    export_root = args.export_root or str(settings.export_root)

    if args.command == "eval":
        from datetime import datetime, timezone

        from book_agent.evals.runner import REPO_ROOT, run_evals

        output = Path(args.output) if args.output else REPO_ROOT / "evals" / "reports" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        try:
            report = run_evals(args.suite, output_dir=output, settings=settings)
        except ValueError as exc:
            parser.error(str(exc))
        _dump(
            {
                "output": str(output),
                "passed": report["passed"],
                "suites": {suite["suite"]: {"passed": suite["passed"], "metrics": suite["metrics"]} for suite in report["suites"]},
            }
        )
        return 0 if report["passed"] else 1

    if args.command == "mcp":
        from book_agent.mcp.server import McpServer

        McpServer(session_factory).serve()
        return 0

    with session_scope(session_factory) as session:
        if args.command == "create-api-key":
            from book_agent.services.api_keys import ApiKeyService

            service = ApiKeyService(session)
            org = service.ensure_org(args.org)
            created = service.create(org_id=org.id, name=args.name, role=args.role)
            _dump({"org": org.name, "org_id": org.id, "name": created.key.name, "role": created.key.role, "key": created.plaintext})
            return 0
        service = DocumentWorkflowService(
            session,
            export_root=export_root,
            # Same resolution as the API: the active stored credential, else settings.
            translation_worker=resolve_translation_worker(session, settings),
        )

        if args.command == "bootstrap":
            _dump(asdict(service.bootstrap_document(args.source_path)))
            return 0
        if args.command == "summary":
            _dump(asdict(service.get_document_summary(args.document_id)))
            return 0
        if args.command == "translate":
            _dump(asdict(service.translate_document(args.document_id, args.packet_id)))
            return 0
        if args.command == "review":
            _dump(asdict(service.review_document(args.document_id)))
            return 0
        if args.command == "export":
            _dump(
                asdict(
                    service.export_document(
                        args.document_id,
                        ExportType(args.export_type),
                        auto_execute_followup_on_gate=args.auto_followup_on_gate,
                        max_auto_followup_attempts=args.max_auto_followup_attempts,
                    )
                )
            )
            return 0
        if args.command == "refresh-pdf-structure":
            _dump(
                asdict(
                    service.refresh_pdf_structure(
                        args.document_id,
                        chapter_ids=(args.chapter_id or None),
                    )
                )
            )
            return 0
        if args.command == "glossary-extract":
            worker = service.translation_service.worker
            client = getattr(worker, "client", None)
            if client is None or not hasattr(client, "generate_structured_object"):
                parser.error("glossary-extract needs an LLM translation provider; the echo worker cannot propose terms.")
            result = GlossaryExtractionService(
                session, client, model_name=worker.metadata().model_name
            ).extract(args.document_id, max_chunk_chars=args.max_chunk_chars)
            write_glossary_csv(args.output, result.suggestions)
            _dump(
                {
                    "output": args.output,
                    "term_count": len(result.suggestions),
                    "chunk_count": result.chunk_count,
                    "proposed_term_count": result.proposed_term_count,
                    "dropped_absent_terms": result.dropped_absent_terms,
                    "token_in": result.token_in,
                    "token_out": result.token_out,
                }
            )
            return 0
        if args.command == "glossary-lock":
            glossary = GlossaryService(session)
            rows = read_glossary_csv(args.input)
            for row in rows:
                glossary.lock_term(args.document_id, row.source_term, row.target_term, term_type=row.term_type)
            _dump({"locked_term_count": len(rows)})
            return 0
        if args.command == "term-consistency":
            worker = service.translation_service.worker
            client = getattr(worker, "client", None)
            if client is None or not hasattr(client, "generate_structured_object"):
                parser.error("term-consistency needs an LLM translation provider; the echo worker cannot edit terms.")
            report = TermConsistencyService(session, client, model_name=worker.metadata().model_name).run(
                args.document_id, apply=not args.dry_run
            )
            Path(args.report).write_text(render_consistency_report_markdown(report), encoding="utf-8")
            _dump(
                {
                    "report": args.report,
                    "consistency_before": report.consistency(after=False),
                    "consistency_after": report.consistency(after=True),
                    "edited_segments": report.edited_segments,
                    "harmonized_terms": len(report.harmonized_decisions),
                    "skipped_terms": len(report.decisions) - len(report.harmonized_decisions),
                    "locked_terms": report.locked_term_count,
                    "token_in": report.token_in,
                    "token_out": report.token_out,
                }
            )
            return 0
        if args.command == "execute-action":
            result = service.execute_action(args.action_id, run_followup=args.run_followup)
            _dump(
                {
                    "action_id": args.action_id,
                    "rerun_plan": asdict(result.action_execution.rerun_plan),
                    "invalidation_count": len(result.action_execution.invalidations),
                    "audit_count": len(result.action_execution.audits),
                    "followup_executed": result.rerun_execution is not None,
                    "rebuild_applied": (
                        result.rerun_execution.rebuild_artifacts is not None if result.rerun_execution else False
                    ),
                    "rebuilt_packet_ids": (
                        result.rerun_execution.rebuild_artifacts.rebuilt_packet_ids
                        if result.rerun_execution and result.rerun_execution.rebuild_artifacts
                        else []
                    ),
                    "rebuilt_snapshot_ids": (
                        result.rerun_execution.rebuild_artifacts.rebuilt_snapshot_ids
                        if result.rerun_execution and result.rerun_execution.rebuild_artifacts
                        else []
                    ),
                    "rebuilt_snapshots": (
                        [
                            {
                                "snapshot_id": snapshot.snapshot_id,
                                "snapshot_type": snapshot.snapshot_type,
                                "version": snapshot.version,
                            }
                            for snapshot in result.rerun_execution.rebuild_artifacts.rebuilt_snapshots
                        ]
                        if result.rerun_execution and result.rerun_execution.rebuild_artifacts
                        else []
                    ),
                    "chapter_brief_version": (
                        result.rerun_execution.rebuild_artifacts.chapter_brief_version
                        if result.rerun_execution and result.rerun_execution.rebuild_artifacts
                        else None
                    ),
                    "termbase_version": (
                        result.rerun_execution.rebuild_artifacts.termbase_version
                        if result.rerun_execution and result.rerun_execution.rebuild_artifacts
                        else None
                    ),
                    "entity_snapshot_version": (
                        result.rerun_execution.rebuild_artifacts.entity_snapshot_version
                        if result.rerun_execution and result.rerun_execution.rebuild_artifacts
                        else None
                    ),
                    "rerun_packet_ids": (
                        result.rerun_execution.translated_packet_ids if result.rerun_execution else []
                    ),
                    "rerun_translation_run_ids": (
                        result.rerun_execution.translation_run_ids if result.rerun_execution else []
                    ),
                    "issue_resolved": (
                        result.rerun_execution.issue_resolved if result.rerun_execution else None
                    ),
                }
            )
            return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
