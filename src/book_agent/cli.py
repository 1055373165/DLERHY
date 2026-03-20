from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from book_agent.core.config import get_settings
from book_agent.domain.enums import ExportType
from book_agent.infra.db.session import build_session_factory, session_scope
from book_agent.services.workflows import DocumentWorkflowService
from book_agent.workers.factory import build_translation_worker


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
    translation_worker = build_translation_worker(settings)

    with session_scope(session_factory) as session:
        service = DocumentWorkflowService(
            session,
            export_root=export_root,
            translation_worker=translation_worker,
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
