from __future__ import annotations

import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from book_agent.domain.enums import (
    DocumentRunStatus,
    DocumentRunType,
    ExportType,
    PacketStatus,
    WorkItemScopeType,
    WorkItemStage,
    WorkItemStatus,
)
from book_agent.domain.models import Chapter
from book_agent.domain.models.ops import DocumentRun, WorkItem
from book_agent.domain.models.translation import TranslationPacket
from book_agent.infra.db.session import session_scope
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.services.export import ExportGateError
from book_agent.services.run_control import RunControlService
from book_agent.services.run_execution import ClaimedRunWorkItem, RunExecutionService
from book_agent.services.workflows import DocumentWorkflowService
from book_agent.workers.translator import TranslationWorker


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_retryable_exception(exc: Exception) -> bool:
    message = str(exc).lower()
    retryable_markers = [
        "http 408",
        "http 409",
        "http 429",
        "http 500",
        "http 502",
        "http 503",
        "http 504",
        "request failed",
        "timed out",
        "timeout",
        "temporarily unavailable",
        "connection reset",
        "connection aborted",
        "connection refused",
        "database is locked",
    ]
    if any(marker in message for marker in retryable_markers):
        return True
    return isinstance(exc, RuntimeError)


class DocumentRunExecutor:
    def __init__(
        self,
        *,
        session_factory: sessionmaker,
        export_root: str | Path,
        translation_worker: TranslationWorker | None,
        poll_interval_seconds: float = 1.0,
        lease_seconds: int = 120,
        heartbeat_interval_seconds: int = 15,
        default_max_auto_followup_attempts: int = 2,
    ) -> None:
        self.session_factory = session_factory
        self.export_root = str(Path(export_root).resolve())
        self.translation_worker = translation_worker
        self.poll_interval_seconds = poll_interval_seconds
        self.lease_seconds = lease_seconds
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.default_max_auto_followup_attempts = default_max_auto_followup_attempts
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._supervisor_thread: threading.Thread | None = None
        self._active_run_threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._supervisor_thread is not None and self._supervisor_thread.is_alive():
                return
            self._stop_event.clear()
            self._wake_event.set()
            self._supervisor_thread = threading.Thread(
                target=self._supervisor_loop,
                name="book-agent-run-supervisor",
                daemon=True,
            )
            self._supervisor_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        with self._lock:
            supervisor = self._supervisor_thread
            run_threads = list(self._active_run_threads.values())
        if supervisor is not None:
            supervisor.join(timeout=5)
        for thread in run_threads:
            thread.join(timeout=5)
        with self._lock:
            self._active_run_threads = {}
            self._supervisor_thread = None

    def wake(self, run_id: str | None = None) -> None:
        self._wake_event.set()
        if run_id is None:
            return
        with self._lock:
            thread = self._active_run_threads.get(run_id)
        if thread is not None and not thread.is_alive():
            with self._lock:
                self._active_run_threads.pop(run_id, None)

    def _workflow_service(self, session) -> DocumentWorkflowService:
        return DocumentWorkflowService(
            session,
            export_root=self.export_root,
            translation_worker=self.translation_worker,
        )

    def _run_control_service(self, session) -> RunControlService:
        return RunControlService(RunControlRepository(session))

    def _run_execution_service(self, session) -> RunExecutionService:
        repository = RunControlRepository(session)
        return RunExecutionService(repository, RunControlService(repository))

    def _supervisor_loop(self) -> None:
        while not self._stop_event.is_set():
            self._reap_finished_threads()
            runnable_run_ids = self._list_runnable_run_ids()
            for run_id in runnable_run_ids:
                self._ensure_run_thread(run_id)
            self._wake_event.wait(timeout=self.poll_interval_seconds)
            self._wake_event.clear()

    def _reap_finished_threads(self) -> None:
        with self._lock:
            finished = [
                run_id
                for run_id, thread in self._active_run_threads.items()
                if not thread.is_alive()
            ]
            for run_id in finished:
                self._active_run_threads.pop(run_id, None)

    def _ensure_run_thread(self, run_id: str) -> None:
        with self._lock:
            existing = self._active_run_threads.get(run_id)
            if existing is not None and existing.is_alive():
                return
            thread = threading.Thread(
                target=self._run_loop,
                args=(run_id,),
                name=f"book-agent-run-{run_id}",
                daemon=True,
            )
            self._active_run_threads[run_id] = thread
            thread.start()

    def _list_runnable_run_ids(self) -> list[str]:
        with session_scope(self.session_factory) as session:
            return list(
                session.scalars(
                    select(DocumentRun.id)
                    .where(
                        DocumentRun.run_type == DocumentRunType.TRANSLATE_FULL,
                        DocumentRun.status.in_(
                            [DocumentRunStatus.RUNNING, DocumentRunStatus.DRAINING]
                        ),
                    )
                    .order_by(DocumentRun.created_at.asc(), DocumentRun.id.asc())
                ).all()
            )

    def _run_loop(self, run_id: str) -> None:
        while not self._stop_event.is_set():
            with session_scope(self.session_factory) as session:
                run_control = self._run_control_service(session)
                run_summary = run_control.get_run_summary(run_id)
            if run_summary.status not in {"running", "draining"}:
                return

            try:
                if self._process_translate_stage(run_id):
                    continue
                if self._process_review_stage(run_id):
                    continue
                if self._process_export_stage(run_id, export_type=ExportType.BILINGUAL_HTML):
                    continue
                if self._process_export_stage(run_id, export_type=ExportType.MERGED_HTML):
                    continue
                with session_scope(self.session_factory) as session:
                    execution = self._run_execution_service(session)
                    summary = execution.reconcile_run_terminal_state(run_id=run_id)
                    self._sync_pipeline_status(run_id, summary.status)
                if summary.status in {"succeeded", "failed", "paused", "cancelled"}:
                    return
            except Exception as exc:  # pragma: no cover - defensive safety net
                self._fail_run(run_id, stop_reason="runner.unhandled_exception", exc=exc)
                return

            self._wake_event.wait(timeout=self.poll_interval_seconds)
            self._wake_event.clear()

    def _process_translate_stage(self, run_id: str) -> bool:
        with session_scope(self.session_factory) as session:
            repository = RunControlRepository(session)
            execution = self._run_execution_service(session)
            run = repository.get_run(run_id)
            document_id = run.document_id
            translate_items = self._list_stage_items(session, run_id, WorkItemStage.TRANSLATE)
            if not translate_items:
                packet_ids = self._list_pending_packet_ids(session, document_id)
                execution.seed_translate_work_items(run_id=run_id, packet_ids=packet_ids)
                self._update_pipeline_stage(
                    run_id,
                    "translate",
                    status=("pending" if packet_ids else "succeeded"),
                    extra={
                        "total_packet_count": len(self._list_all_packet_ids(session, document_id)),
                        "pending_packet_count": len(packet_ids),
                    },
                    current_stage=("translate" if packet_ids else "review"),
                )
                return bool(packet_ids)

            if any(item.status == WorkItemStatus.TERMINAL_FAILED for item in translate_items):
                return False

            if any(item.status in {WorkItemStatus.PENDING, WorkItemStatus.RETRYABLE_FAILED} for item in translate_items):
                self._update_pipeline_stage(run_id, "translate", status="running", current_stage="translate")
                claimed = execution.claim_next_work_item(
                    run_id=run_id,
                    stage=WorkItemStage.TRANSLATE,
                    worker_name="app.run.translate",
                    worker_instance_id=f"app.translate:{uuid4()}",
                    lease_seconds=self.lease_seconds,
                )
            else:
                claimed = None

        if claimed is not None:
            self._execute_translate_work_item(run_id, claimed)
            return True

        if translate_items and all(item.status == WorkItemStatus.SUCCEEDED for item in translate_items):
            self._update_pipeline_stage(run_id, "translate", status="succeeded", current_stage="review")
        return False

    def _process_review_stage(self, run_id: str) -> bool:
        with session_scope(self.session_factory) as session:
            repository = RunControlRepository(session)
            execution = self._run_execution_service(session)
            run = repository.get_run(run_id)
            translate_items = self._list_stage_items(session, run_id, WorkItemStage.TRANSLATE)
            if any(item.status != WorkItemStatus.SUCCEEDED for item in translate_items):
                return False
            review_items = self._list_stage_items(session, run_id, WorkItemStage.REVIEW)
            if not review_items:
                execution.seed_work_items(
                    run_id=run_id,
                    stage=WorkItemStage.REVIEW,
                    scope_type=WorkItemScopeType.DOCUMENT,
                    scope_ids=[run.document_id],
                    input_version_bundle_by_scope_id={
                        run.document_id: {"document_id": run.document_id},
                    },
                )
                self._update_pipeline_stage(run_id, "review", status="pending", current_stage="review")
                return True
            if any(item.status == WorkItemStatus.TERMINAL_FAILED for item in review_items):
                return False
            if any(item.status in {WorkItemStatus.PENDING, WorkItemStatus.RETRYABLE_FAILED} for item in review_items):
                self._update_pipeline_stage(run_id, "review", status="running", current_stage="review")
                claimed = execution.claim_next_work_item(
                    run_id=run_id,
                    stage=WorkItemStage.REVIEW,
                    worker_name="app.run.review",
                    worker_instance_id=f"app.review:{uuid4()}",
                    lease_seconds=self.lease_seconds,
                )
            else:
                claimed = None

        if claimed is not None:
            self._execute_review_work_item(run_id, claimed)
            return True

        if review_items and all(item.status == WorkItemStatus.SUCCEEDED for item in review_items):
            self._update_pipeline_stage(run_id, "review", status="succeeded", current_stage="bilingual_html")
        return False

    def _process_export_stage(self, run_id: str, *, export_type: ExportType) -> bool:
        pipeline_key = export_type.value
        with session_scope(self.session_factory) as session:
            repository = RunControlRepository(session)
            execution = self._run_execution_service(session)
            run = repository.get_run(run_id)
            review_items = self._list_stage_items(session, run_id, WorkItemStage.REVIEW)
            if review_items and any(item.status != WorkItemStatus.SUCCEEDED for item in review_items):
                return False
            if export_type == ExportType.MERGED_HTML:
                bilingual_items = self._list_export_items(session, run_id, ExportType.BILINGUAL_HTML)
                if not bilingual_items or any(item.status != WorkItemStatus.SUCCEEDED for item in bilingual_items):
                    return False

            export_items = self._list_export_items(session, run_id, export_type)
            if not export_items:
                execution.seed_work_items(
                    run_id=run_id,
                    stage=WorkItemStage.EXPORT,
                    scope_type=WorkItemScopeType.DOCUMENT,
                    scope_ids=[run.document_id],
                    input_version_bundle_by_scope_id={
                        run.document_id: {
                            "document_id": run.document_id,
                            "export_type": export_type.value,
                        }
                    },
                )
                self._update_pipeline_stage(run_id, pipeline_key, status="pending", current_stage=pipeline_key)
                return True
            if any(item.status == WorkItemStatus.TERMINAL_FAILED for item in export_items):
                return False
            if any(item.status in {WorkItemStatus.PENDING, WorkItemStatus.RETRYABLE_FAILED} for item in export_items):
                self._update_pipeline_stage(run_id, pipeline_key, status="running", current_stage=pipeline_key)
                claimed = execution.claim_next_work_item(
                    run_id=run_id,
                    stage=WorkItemStage.EXPORT,
                    worker_name=f"app.run.export.{export_type.value}",
                    worker_instance_id=f"app.export.{export_type.value}:{uuid4()}",
                    lease_seconds=self.lease_seconds,
                )
            else:
                claimed = None

        if claimed is not None:
            self._execute_export_work_item(run_id, claimed, export_type=export_type)
            return True

        if export_items and all(item.status == WorkItemStatus.SUCCEEDED for item in export_items):
            next_stage = "merged_html" if export_type == ExportType.BILINGUAL_HTML else "completed"
            self._update_pipeline_stage(run_id, pipeline_key, status="succeeded", current_stage=next_stage)
        return False

    def _execute_translate_work_item(self, run_id: str, claimed: ClaimedRunWorkItem) -> None:
        self._execute_claimed_work_item(
            run_id=run_id,
            claimed=claimed,
            worker_fn=lambda: self._translate_single_packet(claimed.scope_id),
            on_success=self._complete_translate_success,
        )

    def _execute_review_work_item(self, run_id: str, claimed: ClaimedRunWorkItem) -> None:
        def _run_review() -> dict[str, Any]:
            with session_scope(self.session_factory) as session:
                workflow = self._workflow_service(session)
                result = workflow.review_document(claimed.scope_id)
            return {
                "document_id": claimed.scope_id,
                "total_issue_count": result.total_issue_count,
                "total_action_count": result.total_action_count,
                "chapter_count": len(result.chapter_results),
            }

        def _on_success(payload: dict[str, Any], lease_token: str) -> None:
            with session_scope(self.session_factory) as session:
                execution = self._run_execution_service(session)
                execution.complete_work_item_success(
                    lease_token=lease_token,
                    output_artifact_refs_json={"document_id": claimed.scope_id},
                    payload_json=payload,
                )
            self._update_pipeline_stage(
                run_id,
                "review",
                status="succeeded",
                extra=payload,
                current_stage="bilingual_html",
            )

        self._execute_claimed_work_item(
            run_id=run_id,
            claimed=claimed,
            worker_fn=_run_review,
            on_success=_on_success,
            stage_key="review",
        )

    def _execute_export_work_item(
        self,
        run_id: str,
        claimed: ClaimedRunWorkItem,
        *,
        export_type: ExportType,
    ) -> None:
        pipeline_key = export_type.value

        def _run_export() -> dict[str, Any]:
            with session_scope(self.session_factory) as session:
                workflow = self._workflow_service(session)
                result = workflow.export_document(
                    claimed.scope_id,
                    export_type,
                    auto_execute_followup_on_gate=True,
                    max_auto_followup_attempts=self._max_auto_followup_attempts(session, run_id),
                )
            return {
                "document_id": claimed.scope_id,
                "export_type": export_type.value,
                "file_path": result.file_path,
                "manifest_path": result.manifest_path,
                "chapter_export_count": len(result.chapter_results),
                "chapter_export_ids": [chapter.export_id for chapter in result.chapter_results],
            }

        def _on_success(payload: dict[str, Any], lease_token: str) -> None:
            with session_scope(self.session_factory) as session:
                execution = self._run_execution_service(session)
                execution.complete_work_item_success(
                    lease_token=lease_token,
                    output_artifact_refs_json=payload,
                    payload_json=payload,
                )
            self._update_pipeline_stage(
                run_id,
                pipeline_key,
                status="succeeded",
                extra=payload,
                current_stage=("merged_html" if export_type == ExportType.BILINGUAL_HTML else "completed"),
            )

        self._execute_claimed_work_item(
            run_id=run_id,
            claimed=claimed,
            worker_fn=_run_export,
            on_success=_on_success,
            stage_key=pipeline_key,
        )

    def _execute_claimed_work_item(
        self,
        *,
        run_id: str,
        claimed: ClaimedRunWorkItem,
        worker_fn,
        on_success,
        stage_key: str | None = None,
    ) -> None:
        stop_event = threading.Event()
        heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            kwargs={
                "lease_token": claimed.lease_token,
                "stop_event": stop_event,
            },
            daemon=True,
        )
        try:
            with session_scope(self.session_factory) as session:
                execution = self._run_execution_service(session)
                execution.start_work_item(
                    lease_token=claimed.lease_token,
                    lease_seconds=self.lease_seconds,
                )
            heartbeat_thread.start()
            payload = worker_fn()
            stop_event.set()
            heartbeat_thread.join(timeout=max(1, self.heartbeat_interval_seconds))
            on_success(payload, claimed.lease_token)
            self.wake(run_id)
        except Exception as exc:
            stop_event.set()
            heartbeat_thread.join(timeout=max(1, self.heartbeat_interval_seconds))
            self._complete_failure(
                run_id=run_id,
                claimed=claimed,
                exc=exc,
                stage_key=stage_key or claimed.stage,
            )

    def _translate_single_packet(self, packet_id: str) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            workflow = self._workflow_service(session)
            packet = session.get(TranslationPacket, packet_id)
            if packet is None:
                raise RuntimeError(f"Packet {packet_id} was not found.")
            if packet.status != PacketStatus.BUILT:
                return {
                    "packet_id": packet_id,
                    "translation_run_id": "already-translated",
                    "token_in": 0,
                    "token_out": 0,
                    "cost_usd": 0.0,
                    "latency_ms": 0,
                }
            artifacts = workflow.translation_service.execute_packet(packet_id)
            translation_run = artifacts.translation_run
            return {
                "packet_id": packet_id,
                "translation_run_id": translation_run.id,
                "token_in": translation_run.token_in or 0,
                "token_out": translation_run.token_out or 0,
                "cost_usd": float(translation_run.cost_usd or 0.0),
                "latency_ms": translation_run.latency_ms or 0,
            }

    def _complete_translate_success(self, payload: dict[str, Any], lease_token: str) -> None:
        with session_scope(self.session_factory) as session:
            execution = self._run_execution_service(session)
            execution.complete_translate_success(
                lease_token=lease_token,
                packet_id=str(payload["packet_id"]),
                translation_run_id=str(payload["translation_run_id"]),
                token_in=int(payload["token_in"]),
                token_out=int(payload["token_out"]),
                cost_usd=float(payload["cost_usd"]),
                latency_ms=int(payload["latency_ms"]),
            )

    def _complete_failure(
        self,
        *,
        run_id: str,
        claimed: ClaimedRunWorkItem,
        exc: Exception,
        stage_key: str,
    ) -> None:
        error_detail = {
            "message": str(exc),
            "traceback": traceback.format_exc(limit=8),
        }
        with session_scope(self.session_factory) as session:
            execution = self._run_execution_service(session)
            execution.complete_work_item_failure(
                lease_token=claimed.lease_token,
                error_class=exc.__class__.__name__,
                error_detail_json=error_detail,
                retryable=_is_retryable_exception(exc),
            )
            summary = execution.reconcile_run_terminal_state(run_id=run_id)
        self._update_pipeline_stage(
            run_id,
            stage_key,
            status=("retryable_failed" if _is_retryable_exception(exc) else "failed"),
            extra={"error_class": exc.__class__.__name__, "error_message": str(exc)},
            current_stage=stage_key,
        )
        if summary.status in {"failed", "paused", "cancelled"}:
            self._sync_pipeline_status(run_id, summary.status)
        self.wake(run_id)

    def _heartbeat_loop(self, *, lease_token: str, stop_event: threading.Event) -> None:
        while not stop_event.wait(timeout=max(1, self.heartbeat_interval_seconds)):
            try:
                with session_scope(self.session_factory) as session:
                    execution = self._run_execution_service(session)
                    alive = execution.heartbeat_work_item(
                        lease_token=lease_token,
                        lease_seconds=self.lease_seconds,
                    )
                if not alive:
                    return
            except Exception:
                continue

    def _list_all_packet_ids(self, session, document_id: str) -> list[str]:
        return list(
            session.scalars(
                select(TranslationPacket.id)
                .join(Chapter, Chapter.id == TranslationPacket.chapter_id)
                .where(Chapter.document_id == document_id)
                .order_by(TranslationPacket.created_at.asc(), TranslationPacket.id.asc())
            ).all()
        )

    def _list_pending_packet_ids(self, session, document_id: str) -> list[str]:
        return list(
            session.scalars(
                select(TranslationPacket.id)
                .join(Chapter, Chapter.id == TranslationPacket.chapter_id)
                .where(
                    Chapter.document_id == document_id,
                    TranslationPacket.status == PacketStatus.BUILT,
                )
                .order_by(TranslationPacket.created_at.asc(), TranslationPacket.id.asc())
            ).all()
        )

    def _list_stage_items(self, session, run_id: str, stage: WorkItemStage) -> list[WorkItem]:
        return list(
            session.scalars(
                select(WorkItem)
                .where(WorkItem.run_id == run_id, WorkItem.stage == stage)
                .order_by(WorkItem.created_at.asc(), WorkItem.id.asc())
            ).all()
        )

    def _list_export_items(self, session, run_id: str, export_type: ExportType) -> list[WorkItem]:
        items = self._list_stage_items(session, run_id, WorkItemStage.EXPORT)
        return [
            item
            for item in items
            if (item.input_version_bundle_json or {}).get("export_type") == export_type.value
        ]

    def _max_auto_followup_attempts(self, session, run_id: str) -> int:
        budget = RunControlRepository(session).get_budget_for_run(run_id)
        if budget is not None and budget.max_auto_followup_attempts is not None:
            return max(1, int(budget.max_auto_followup_attempts))
        return self.default_max_auto_followup_attempts

    def _update_pipeline_stage(
        self,
        run_id: str,
        stage_key: str,
        *,
        status: str,
        extra: dict[str, Any] | None = None,
        current_stage: str | None = None,
    ) -> None:
        with session_scope(self.session_factory) as session:
            repository = RunControlRepository(session)
            run = repository.get_run(run_id)
            detail = dict(run.status_detail_json or {})
            pipeline = dict(detail.get("pipeline") or {})
            stages = dict(pipeline.get("stages") or {})
            stage_detail = dict(stages.get(stage_key) or {})
            stage_detail["status"] = status
            stage_detail["updated_at"] = _utcnow().isoformat()
            if extra:
                stage_detail.update(extra)
            stages[stage_key] = stage_detail
            pipeline["stages"] = stages
            if current_stage is not None:
                pipeline["current_stage"] = current_stage
            detail["pipeline"] = pipeline
            run.status_detail_json = detail
            repository.save_run(run)

    def _sync_pipeline_status(self, run_id: str, run_status: str) -> None:
        if run_status == "succeeded":
            self._update_pipeline_stage(run_id, "pipeline", status="succeeded", current_stage="completed")
            return
        if run_status in {"failed", "paused", "cancelled"}:
            self._update_pipeline_stage(run_id, "pipeline", status=run_status, current_stage=run_status)

    def _fail_run(self, run_id: str, *, stop_reason: str, exc: Exception) -> None:
        with session_scope(self.session_factory) as session:
            control = self._run_control_service(session)
            control.fail_run_system(
                run_id,
                stop_reason=stop_reason,
                detail_json={
                    "error_class": exc.__class__.__name__,
                    "error_message": str(exc),
                    "traceback": traceback.format_exc(limit=8),
                },
            )
        self._sync_pipeline_status(run_id, "failed")
