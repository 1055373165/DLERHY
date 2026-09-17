"""Document export: gate checks with optional auto followups, rendering and blob stamping."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.application.issue_actions import IssueActionWorkflow
from book_agent.application.read_models import (
    ChapterExportResult,
    DocumentExportResult,
    ExportAutoFollowupExecution,
)
from book_agent.domain.enums import (
    ActorType,
    ExportStatus,
    ExportType,
)
from book_agent.domain.models.ops import AuditEvent
from book_agent.domain.models.review import ReviewIssue
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.infra.repositories.export import ExportRepository
from book_agent.infra.repositories.ops import OpsRepository
from book_agent.infra.storage.blobs import blob_root_for_export_root, stamp_export_records
from book_agent.services.export import ExportGateError, ExportService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Whole-document exports; every other type is written per chapter.
_DOCUMENT_LEVEL_EXPORTERS: dict[ExportType, str] = {
    ExportType.MERGED_HTML: "export_document_merged_html",
    ExportType.MERGED_MARKDOWN: "export_document_merged_markdown",
    ExportType.REBUILT_EPUB: "export_document_rebuilt_epub",
    ExportType.ZH_EPUB: "export_document_zh_epub",
    ExportType.REBUILT_PDF: "export_document_rebuilt_pdf",
}


def _optional_path(path: Any) -> str | None:
    return str(path) if path is not None else None


class DocumentExportUseCase:
    """Exports a document, auto-executing gate followups when asked, and stamps artifact blobs."""

    def __init__(
        self,
        session: Session,
        bootstrap_repository: BootstrapRepository,
        export_repository: ExportRepository,
        export_service: ExportService,
        ops_repository: OpsRepository,
        issue_actions: IssueActionWorkflow,
    ) -> None:
        self.session = session
        self.bootstrap_repository = bootstrap_repository
        self.export_repository = export_repository
        self.export_service = export_service
        self.ops_repository = ops_repository
        self.issue_actions = issue_actions

    def export_document(
        self,
        document_id: str,
        export_type: ExportType,
        *,
        auto_execute_followup_on_gate: bool = False,
        max_auto_followup_attempts: int = 3,
    ) -> DocumentExportResult:
        result = self._write_document_export(
            document_id,
            export_type,
            auto_execute_followup_on_gate=auto_execute_followup_on_gate,
            max_auto_followup_attempts=max_auto_followup_attempts,
        )
        self._stamp_export_blobs(document_id, export_type)
        return result

    def _stamp_export_blobs(self, document_id: str, export_type: ExportType) -> None:
        # Every export writer path (API, run executor, CLI) goes through
        # export_document, so content-address the fresh files here.
        self.session.flush()
        records = self.export_repository.list_document_exports_filtered(
            document_id,
            export_type=export_type,
            status=ExportStatus.SUCCEEDED,
        )
        stamp_export_records(
            self.session,
            records,
            blob_root=blob_root_for_export_root(self.export_service.output_root),
        )

    def _write_document_export(
        self,
        document_id: str,
        export_type: ExportType,
        *,
        auto_execute_followup_on_gate: bool,
        max_auto_followup_attempts: int,
    ) -> DocumentExportResult:
        bundle, executions = self._pass_export_gate(
            document_id,
            export_type,
            auto_execute_followup_on_gate=auto_execute_followup_on_gate,
            max_auto_followup_attempts=max_auto_followup_attempts,
        )

        chapter_results: list[ChapterExportResult] = []
        file_path: str | None = None
        manifest_path: str | None = None
        document_exporter = _DOCUMENT_LEVEL_EXPORTERS.get(export_type)
        if document_exporter is not None:
            # The gate just passed for every chapter in _pass_export_gate.
            artifacts = getattr(self.export_service, document_exporter)(document_id, enforce_gate=False)
            file_path = str(artifacts.file_path)
            manifest_path = _optional_path(artifacts.manifest_path)
        else:
            for chapter_bundle in bundle.chapters:
                artifacts = self.export_service.export_chapter(chapter_bundle.chapter.id, export_type, enforce_gate=False)
                chapter_results.append(
                    ChapterExportResult(
                        chapter_id=chapter_bundle.chapter.id,
                        export_id=artifacts.export_record.id,
                        export_type=artifacts.export_record.export_type.value,
                        status=artifacts.export_record.status.value,
                        file_path=str(artifacts.file_path),
                        manifest_path=_optional_path(artifacts.manifest_path),
                    )
                )

        document = self.session.get(type(bundle.document), document_id) or bundle.document
        return DocumentExportResult(
            document_id=document_id,
            export_type=export_type.value,
            document_status=document.status.value,
            file_path=file_path,
            manifest_path=manifest_path,
            chapter_results=chapter_results,
            auto_followup_requested=auto_execute_followup_on_gate,
            auto_followup_applied=bool(executions),
            auto_followup_attempt_count=len(executions),
            auto_followup_attempt_limit=(max_auto_followup_attempts if auto_execute_followup_on_gate else None),
            auto_followup_executions=executions,
        )

    def _pass_export_gate(
        self,
        document_id: str,
        export_type: ExportType,
        *,
        auto_execute_followup_on_gate: bool,
        max_auto_followup_attempts: int,
    ) -> tuple[Any, list[ExportAutoFollowupExecution]]:
        """Check every chapter against the export gate, auto-executing followups if asked.

        Returns the document bundle that passed and the followups executed on
        the way; raises ExportGateError (with auto followup telemetry) when the
        gate still blocks.
        """
        executions: list[ExportAutoFollowupExecution] = []
        attempted_action_ids: set[str] = set()

        def stop(exc: ExportGateError, stop_reason: str, followup_action_ids: list[str]) -> ExportGateError:
            self._record_export_auto_followup_stop(
                chapter_id=exc.chapter_id,
                document_id=document_id,
                export_type=export_type,
                executions=executions,
                attempt_limit=max_auto_followup_attempts,
                stop_reason=stop_reason,
                issue_ids=exc.issue_ids,
                followup_action_ids=followup_action_ids,
            )
            return self._with_auto_followup_telemetry(
                exc,
                executions,
                requested=True,
                attempt_limit=max_auto_followup_attempts,
                stop_reason=stop_reason,
            )

        while True:
            bundle = self.bootstrap_repository.load_document_bundle(document_id)
            try:
                for chapter_bundle in bundle.chapters:
                    self.export_service.assert_chapter_exportable(chapter_bundle.chapter.id, export_type)
                return bundle, executions
            except ExportGateError as exc:
                if not auto_execute_followup_on_gate:
                    raise
                if not exc.followup_actions:
                    raise stop(exc, "no_followup_actions", []) from exc
                candidate_actions = [
                    action for action in exc.followup_actions if action.action_id not in attempted_action_ids
                ]
                if not candidate_actions:
                    raise stop(
                        exc, "no_new_actions", [action.action_id for action in exc.followup_actions]
                    ) from exc
                issue_by_id = {
                    issue.id: issue
                    for issue in self.session.scalars(
                        select(ReviewIssue).where(ReviewIssue.id.in_(exc.issue_ids))
                    ).all()
                }
                candidate_actions, blocked_actions = self.issue_actions.split_actions_by_manual_hold(
                    issue_by_id=issue_by_id,
                    actions=candidate_actions,
                )
                if not candidate_actions:
                    raise stop(
                        exc, "manual_hold_required", [action.action_id for action in blocked_actions]
                    ) from exc
                remaining_attempt_budget = max(max_auto_followup_attempts - len(executions), 0)
                if remaining_attempt_budget <= 0:
                    raise stop(
                        exc, "max_attempts_reached", [action.action_id for action in candidate_actions]
                    ) from exc
                for followup_action in candidate_actions[:remaining_attempt_budget]:
                    attempted_action_ids.add(followup_action.action_id)
                    try:
                        result = self.issue_actions.execute_action(
                            followup_action.action_id,
                            run_followup=followup_action.suggested_run_followup,
                        )
                    except ValueError:
                        # Skip actions that are not applicable to this document type
                        # (e.g. PDF structure refresh on EPUB documents)
                        continue
                    rerun = result.rerun_execution
                    executions.append(
                        ExportAutoFollowupExecution(
                            action_id=followup_action.action_id,
                            issue_id=followup_action.issue_id,
                            action_type=followup_action.action_type,
                            rerun_scope_type=result.action_execution.rerun_plan.scope_type.value,
                            rerun_scope_ids=result.action_execution.rerun_plan.scope_ids,
                            followup_executed=rerun is not None,
                            rerun_packet_ids=(rerun.translated_packet_ids if rerun else []),
                            rerun_translation_run_ids=(rerun.translation_run_ids if rerun else []),
                            issue_resolved=(rerun.issue_resolved if rerun else None),
                        )
                    )
                    self._record_export_auto_followup_execution(
                        chapter_id=exc.chapter_id,
                        document_id=document_id,
                        export_type=export_type,
                        execution=executions[-1],
                        attempt_index=len(executions),
                        attempt_limit=max_auto_followup_attempts,
                    )
                if len(candidate_actions) > remaining_attempt_budget:
                    raise stop(
                        exc,
                        "max_attempts_reached",
                        [action.action_id for action in candidate_actions[remaining_attempt_budget:]],
                    ) from exc

    def _with_auto_followup_telemetry(
        self,
        exc: ExportGateError,
        executions: list[ExportAutoFollowupExecution],
        *,
        requested: bool,
        attempt_limit: int,
        stop_reason: str,
    ) -> ExportGateError:
        return ExportGateError(
            str(exc),
            chapter_id=exc.chapter_id,
            issue_ids=exc.issue_ids,
            followup_actions=exc.followup_actions,
            auto_followup_requested=requested,
            auto_followup_attempt_count=len(executions),
            auto_followup_attempt_limit=attempt_limit,
            auto_followup_stop_reason=stop_reason,
            auto_followup_executions=[execution.to_export_gate_payload() for execution in executions],
        )

    def _record_export_auto_followup_execution(
        self,
        *,
        chapter_id: str | None,
        document_id: str,
        export_type: ExportType,
        execution: ExportAutoFollowupExecution,
        attempt_index: int,
        attempt_limit: int,
    ) -> None:
        if chapter_id is None:
            return
        audit = AuditEvent(
            object_type="chapter",
            object_id=chapter_id,
            action="export.auto_followup.executed",
            actor_type=ActorType.SYSTEM,
            actor_id="document-export-workflow",
            payload_json={
                "document_id": document_id,
                "export_type": export_type.value,
                "attempt_index": attempt_index,
                "attempt_limit": attempt_limit,
                "issue_id": execution.issue_id,
                "action_id": execution.action_id,
                "action_type": execution.action_type,
                "rerun_scope_type": execution.rerun_scope_type,
                "rerun_scope_ids": execution.rerun_scope_ids,
                "followup_executed": execution.followup_executed,
                "rerun_packet_ids": execution.rerun_packet_ids,
                "rerun_translation_run_ids": execution.rerun_translation_run_ids,
                "issue_resolved": execution.issue_resolved,
            },
            created_at=_utcnow(),
        )
        self.ops_repository.save_audits([audit])
        self.session.flush()

    def _record_export_auto_followup_stop(
        self,
        *,
        chapter_id: str | None,
        document_id: str,
        export_type: ExportType,
        executions: list[ExportAutoFollowupExecution],
        attempt_limit: int,
        stop_reason: str,
        issue_ids: list[str],
        followup_action_ids: list[str],
    ) -> None:
        if chapter_id is None:
            return
        audit = AuditEvent(
            object_type="chapter",
            object_id=chapter_id,
            action="export.auto_followup.stopped",
            actor_type=ActorType.SYSTEM,
            actor_id="document-export-workflow",
            payload_json={
                "document_id": document_id,
                "export_type": export_type.value,
                "stop_reason": stop_reason,
                "attempt_count": len(executions),
                "attempt_limit": attempt_limit,
                "issue_ids": issue_ids,
                "followup_action_ids": followup_action_ids,
                "executions": [execution.to_export_gate_payload() for execution in executions],
            },
            created_at=_utcnow(),
        )
        self.ops_repository.save_audits([audit])
        self.session.flush()
