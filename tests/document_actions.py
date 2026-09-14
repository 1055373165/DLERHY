"""Run document translate / review / export synchronously for API tests.

``POST /v1/documents/{id}/translate|review|export`` enqueue runs and return
202. Most API workflow tests exercise what those actions do, not how they are
scheduled, so ``SyncDocumentActionClient`` answers those three requests by
calling the workflow service in-process and replying with the synchronous
result shape the endpoints used to return. Everything else goes over HTTP.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi.testclient import TestClient
from pydantic import ValidationError

from book_agent.domain.enums import ExportType
from book_agent.infra.db.session import session_scope
from book_agent.schemas.workflow import (
    ExportDocumentRequest,
    ExportDocumentResponse,
    ReviewDocumentResponse,
    TranslateDocumentRequest,
    TranslateDocumentResponse,
)
from book_agent.services.export import ExportGateError
from book_agent.services.workflows import (
    DocumentExportResult,
    DocumentReviewResult,
    DocumentTranslationResult,
    DocumentWorkflowService,
)

_ACTION_PATH = re.compile(r"^/v1/documents/(?P<document_id>[^/?]+)/(?P<action>translate|review|export)$")


class SyncActionResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class SyncDocumentActionClient(TestClient):
    def post(self, url, *args, **kwargs):  # type: ignore[override]
        match = _ACTION_PATH.match(str(url))
        if match is None:
            return super().post(url, *args, **kwargs)
        return run_document_action(
            self.app,
            match.group("document_id"),
            match.group("action"),
            kwargs.get("json"),
        )


def run_document_action(app, document_id: str, action: str, body: dict[str, Any] | None) -> SyncActionResponse:
    try:
        if action == "translate":
            translate_request = TranslateDocumentRequest(**(body or {}))
        elif action == "export":
            export_request = ExportDocumentRequest(**(body or {}))
    except ValidationError as exc:
        return SyncActionResponse(422, {"detail": exc.errors()})

    with session_scope(app.state.session_factory) as session:
        workflow = DocumentWorkflowService(
            session,
            export_root=getattr(app.state, "export_root", "artifacts/exports"),
            translation_worker=app.state.resolve_translation_worker(),
        )
        try:
            if action == "translate":
                result = workflow.translate_document(document_id, translate_request.packet_ids)
                return SyncActionResponse(200, _translate_payload(result))
            if action == "review":
                return SyncActionResponse(200, _review_payload(workflow.review_document(document_id)))
            result = workflow.export_document(
                document_id,
                ExportType(export_request.export_type),
                auto_execute_followup_on_gate=export_request.auto_execute_followup_on_gate,
                max_auto_followup_attempts=export_request.max_auto_followup_attempts,
            )
            return SyncActionResponse(200, _export_payload(result))
        except ExportGateError as exc:
            # The gate's review issues and followup attempts are kept.
            session.commit()
            return SyncActionResponse(409, {"detail": exc.to_http_detail()})
        except ValueError as exc:
            session.rollback()
            return SyncActionResponse(404, {"detail": str(exc)})


def _translate_payload(result: DocumentTranslationResult) -> dict[str, Any]:
    return TranslateDocumentResponse(
        document_id=result.document_id,
        translated_packet_count=result.translated_packet_count,
        skipped_packet_ids=result.skipped_packet_ids,
        translation_run_ids=result.translation_run_ids,
        review_required_sentence_ids=result.review_required_sentence_ids,
        memory_commit_mode=result.memory_commit_mode,
        recorded_memory_proposal_count=result.recorded_memory_proposal_count,
    ).model_dump(mode="json")


def _review_payload(result: DocumentReviewResult) -> dict[str, Any]:
    return ReviewDocumentResponse(
        document_id=result.document_id,
        total_issue_count=result.total_issue_count,
        total_action_count=result.total_action_count,
        chapter_results=[
            {
                "chapter_id": chapter.chapter_id,
                "status": chapter.status,
                "issue_count": chapter.issue_count,
                "action_count": chapter.action_count,
                "blocking_issue_count": chapter.blocking_issue_count,
                "coverage_ok": chapter.coverage_ok,
                "alignment_ok": chapter.alignment_ok,
                "term_ok": chapter.term_ok,
                "format_ok": chapter.format_ok,
                "low_confidence_count": chapter.low_confidence_count,
                "format_pollution_count": chapter.format_pollution_count,
                "resolved_issue_count": chapter.resolved_issue_count,
                "naturalness_summary": (
                    {
                        "advisory_only": chapter.naturalness_summary.advisory_only,
                        "style_drift_issue_count": chapter.naturalness_summary.style_drift_issue_count,
                        "affected_packet_count": chapter.naturalness_summary.affected_packet_count,
                        "dominant_style_rules": list(chapter.naturalness_summary.dominant_style_rules),
                        "preferred_hints": list(chapter.naturalness_summary.preferred_hints),
                    }
                    if chapter.naturalness_summary is not None
                    else None
                ),
            }
            for chapter in result.chapter_results
        ],
    ).model_dump(mode="json")


def _export_payload(result: DocumentExportResult) -> dict[str, Any]:
    return ExportDocumentResponse(
        document_id=result.document_id,
        export_type=result.export_type,
        document_status=result.document_status,
        file_path=result.file_path,
        manifest_path=result.manifest_path,
        chapter_results=[
            {
                "chapter_id": chapter.chapter_id,
                "export_id": chapter.export_id,
                "export_type": chapter.export_type,
                "status": chapter.status,
                "file_path": chapter.file_path,
                "manifest_path": chapter.manifest_path,
            }
            for chapter in result.chapter_results
        ],
        auto_followup_requested=result.auto_followup_requested,
        auto_followup_applied=result.auto_followup_applied,
        auto_followup_attempt_count=result.auto_followup_attempt_count,
        auto_followup_attempt_limit=result.auto_followup_attempt_limit,
        auto_followup_executions=[
            {
                "action_id": execution.action_id,
                "issue_id": execution.issue_id,
                "action_type": execution.action_type,
                "rerun_scope_type": execution.rerun_scope_type,
                "rerun_scope_ids": execution.rerun_scope_ids,
                "followup_executed": execution.followup_executed,
                "rerun_packet_ids": execution.rerun_packet_ids,
                "rerun_translation_run_ids": execution.rerun_translation_run_ids,
                "issue_resolved": execution.issue_resolved,
            }
            for execution in (result.auto_followup_executions or [])
        ],
    ).model_dump(mode="json")
