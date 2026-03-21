from __future__ import annotations

import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from book_agent.core.ids import stable_id
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
from book_agent.services.run_control import RunControlService
from book_agent.services.run_execution import ClaimedRunWorkItem, RunExecutionService
from book_agent.services.workflows import DocumentWorkflowService
from book_agent.workers.translator import TranslationWorker


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_retryable_exception(exc: Exception) -> bool:
    message = str(exc).lower()
    non_retryable_markers = [
        "http 400",
        "http 401",
        "http 402",
        "http 403",
        "http 404",
        "insufficient balance",
        "invalid api key",
        "authentication failed",
    ]
    if any(marker in message for marker in non_retryable_markers):
        return False
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
        "structured json output payload",
        "translationworkeroutput schema",
    ]
    return any(marker in message for marker in retryable_markers)


def _pause_reason_for_exception(exc: Exception) -> str | None:
    message = str(exc).lower()
    if "http 402" in message and "insufficient balance" in message:
        return "provider.insufficient_balance"
    return None


def ensure_document_run_executor(app) -> "DocumentRunExecutor":
    executor = getattr(app.state, "document_run_executor", None)
    if executor is not None:
        return executor
    executor = DocumentRunExecutor(
        session_factory=app.state.session_factory,
        export_root=app.state.export_root,
        translation_worker=app.state.translation_worker,
    )
    executor.start()
    app.state.document_run_executor = executor
    return executor


class DocumentRunExecutor:
    def __init__(
        self,
        *,
        session_factory: sessionmaker,
        export_root: str | Path,
        translation_worker: TranslationWorker | None,
        poll_interval_seconds: float = 1.0,
        lease_seconds: int = 120,
        review_lease_seconds: int = 1800,
        heartbeat_interval_seconds: int = 15,
        default_max_auto_followup_attempts: int = 2,
        default_max_blocker_repair_rounds: int = 4,
        default_max_parallel_workers: int = 8,
    ) -> None:
        self.session_factory = session_factory
        self.export_root = str(Path(export_root).resolve())
        self.translation_worker = translation_worker
        self.poll_interval_seconds = poll_interval_seconds
        self.lease_seconds = lease_seconds
        self.review_lease_seconds = max(self.lease_seconds, int(review_lease_seconds))
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.default_max_auto_followup_attempts = default_max_auto_followup_attempts
        self.default_max_blocker_repair_rounds = max(1, int(default_max_blocker_repair_rounds))
        self.default_max_parallel_workers = max(1, int(default_max_parallel_workers))
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._supervisor_thread: threading.Thread | None = None
        self._active_run_threads: dict[str, threading.Thread] = {}
        self._active_work_threads: dict[str, dict[str, threading.Thread]] = {}
        self._lock = threading.Lock()

    def _is_sqlite_session(self, session: Session) -> bool:
        bind = session.get_bind()
        return bind is not None and bind.dialect.name == "sqlite"

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
            work_threads = [
                thread
                for thread_map in self._active_work_threads.values()
                for thread in thread_map.values()
            ]
        if supervisor is not None:
            supervisor.join(timeout=5)
        for thread in run_threads:
            thread.join(timeout=5)
        for thread in work_threads:
            thread.join(timeout=5)
        with self._lock:
            self._active_run_threads = {}
            self._active_work_threads = {}
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
            try:
                self._reap_finished_threads()
                runnable_run_ids = self._list_runnable_run_ids()
                for run_id in runnable_run_ids:
                    self._ensure_run_thread(run_id)
            except Exception:
                if self._stop_event.is_set():
                    return
                time.sleep(min(self.poll_interval_seconds, 1.0))
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
            empty_runs: list[str] = []
            for run_id, thread_map in self._active_work_threads.items():
                finished_work_items = [
                    work_item_id
                    for work_item_id, thread in thread_map.items()
                    if not thread.is_alive()
                ]
                for work_item_id in finished_work_items:
                    thread_map.pop(work_item_id, None)
                if not thread_map:
                    empty_runs.append(run_id)
            for run_id in empty_runs:
                self._active_work_threads.pop(run_id, None)

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

    def _ensure_work_thread(
        self,
        *,
        run_id: str,
        work_item_id: str,
        thread_name: str,
        target,
    ) -> None:
        with self._lock:
            thread_map = self._active_work_threads.setdefault(run_id, {})
            existing = thread_map.get(work_item_id)
            if existing is not None and existing.is_alive():
                return
            thread = threading.Thread(
                target=target,
                name=thread_name,
                daemon=True,
            )
            thread_map[work_item_id] = thread
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
                self._reclaim_expired_leases(run_id)
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

    def _reclaim_expired_leases(self, run_id: str) -> bool:
        with session_scope(self.session_factory) as session:
            execution = self._run_execution_service(session)
            reclaimed = execution.reclaim_expired_leases(run_id=run_id)
        return reclaimed.expired_lease_count > 0

    def _process_translate_stage(self, run_id: str) -> bool:
        with session_scope(self.session_factory) as session:
            repository = RunControlRepository(session)
            execution = self._run_execution_service(session)
            run = repository.get_run(run_id)
            document_id = run.document_id
            translate_items = self._list_stage_items(session, run_id, WorkItemStage.TRANSLATE)
            if not translate_items:
                packet_ids = self._list_pending_packet_ids(session, document_id)
                execution.seed_translate_work_items(
                    run_id=run_id,
                    packet_ids=packet_ids,
                    input_version_bundle_by_packet_id=self._translate_input_versions(session, packet_ids),
                )
                self._update_pipeline_stage(
                    run_id,
                    "translate",
                    status=("pending" if packet_ids else "succeeded"),
                    extra={
                        "total_packet_count": len(self._list_all_packet_ids(session, document_id)),
                        "pending_packet_count": len(packet_ids),
                    },
                    current_stage=("translate" if packet_ids else "review"),
                    session=session,
                )
                return bool(packet_ids)

            if any(item.status == WorkItemStatus.TERMINAL_FAILED for item in translate_items):
                return False

            claimed_items: list[ClaimedRunWorkItem] = []
            if any(item.status in {WorkItemStatus.PENDING, WorkItemStatus.RETRYABLE_FAILED} for item in translate_items):
                self._update_pipeline_stage(run_id, "translate", status="running", current_stage="translate", session=session)
                claimed_items = self._claim_translate_work_items(
                    session=session,
                    execution=execution,
                    run_id=run_id,
                    translate_items=translate_items,
                )

        if claimed_items:
            for claimed in claimed_items:
                self._ensure_work_thread(
                    run_id=run_id,
                    work_item_id=claimed.work_item_id,
                    thread_name=f"book-agent-translate-{claimed.work_item_id}",
                    target=lambda claimed=claimed: self._execute_translate_work_item(run_id, claimed),
                )
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
                self._update_pipeline_stage(run_id, "review", status="pending", current_stage="review", session=session)
                return True
            if any(item.status == WorkItemStatus.TERMINAL_FAILED for item in review_items):
                return False
            if any(item.status in {WorkItemStatus.PENDING, WorkItemStatus.RETRYABLE_FAILED} for item in review_items):
                self._update_pipeline_stage(run_id, "review", status="running", current_stage="review", session=session)
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
            if not review_items:
                return False
            if any(item.status != WorkItemStatus.SUCCEEDED for item in review_items):
                return False
            if export_type == ExportType.MERGED_HTML:
                bilingual_items = self._list_export_items(session, run_id, ExportType.BILINGUAL_HTML)
                if not bilingual_items or any(item.status != WorkItemStatus.SUCCEEDED for item in bilingual_items):
                    return False

            export_items = self._list_export_items(session, run_id, export_type)
            if not export_items:
                export_scope_id = stable_id("document-run-export", run_id, export_type.value)
                execution.seed_work_items(
                    run_id=run_id,
                    stage=WorkItemStage.EXPORT,
                    scope_type=WorkItemScopeType.EXPORT,
                    scope_ids=[export_scope_id],
                    input_version_bundle_by_scope_id={
                        export_scope_id: {
                            "document_id": run.document_id,
                            "export_type": export_type.value,
                        }
                    },
                )
                self._update_pipeline_stage(run_id, pipeline_key, status="pending", current_stage=pipeline_key, session=session)
                return True
            if any(item.status == WorkItemStatus.TERMINAL_FAILED for item in export_items):
                return False
            if any(item.status in {WorkItemStatus.PENDING, WorkItemStatus.RETRYABLE_FAILED} for item in export_items):
                self._update_pipeline_stage(run_id, pipeline_key, status="running", current_stage=pipeline_key, session=session)
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
            lease_seconds=self.lease_seconds,
        )

    def _execute_review_work_item(self, run_id: str, claimed: ClaimedRunWorkItem) -> None:
        def _run_review() -> dict[str, Any]:
            payload: dict[str, Any]
            remaining_blocking_issue_count = 0
            stop_reason = "unknown"
            with session_scope(self.session_factory) as session:
                workflow = self._workflow_service(session)
                initial_result = workflow.review_document(
                    claimed.scope_id,
                    auto_execute_packet_followups=True,
                    max_auto_followup_attempts=self._max_auto_followup_attempts(session, run_id),
                )
                repair_result = workflow.repair_document_blockers_until_exportable(
                    claimed.scope_id,
                    max_rounds=self._max_blocker_repair_rounds(session, run_id),
                )
                result = initial_result
                if repair_result.applied:
                    result = workflow.review_document(
                        claimed.scope_id,
                        auto_execute_packet_followups=False,
                    )
                remaining_blocking_issue_count = repair_result.blocking_issue_count_after
                stop_reason = repair_result.stop_reason or "unknown"
                payload = {
                    "document_id": claimed.scope_id,
                    "total_issue_count": result.total_issue_count,
                    "total_action_count": result.total_action_count,
                    "chapter_count": len(result.chapter_results),
                    "auto_followup_requested": initial_result.auto_followup_requested,
                    "auto_followup_applied": initial_result.auto_followup_applied,
                    "auto_followup_attempt_count": initial_result.auto_followup_attempt_count,
                    "blocker_repair_requested": repair_result.requested,
                    "blocker_repair_applied": repair_result.applied,
                    "blocker_repair_round_count": repair_result.round_count,
                    "blocker_repair_round_limit": repair_result.round_limit,
                    "blocker_repair_execution_count": len(repair_result.executions),
                    "remaining_blocking_issue_count": remaining_blocking_issue_count,
                }
            if remaining_blocking_issue_count > 0:
                self._update_pipeline_stage(
                    run_id,
                    "review",
                    status="running",
                    extra=payload,
                    current_stage="review",
                )
                raise RuntimeError(
                    "Document still has unresolved blocking review issues after repair: "
                    f"{remaining_blocking_issue_count} remaining "
                    f"(stop_reason={stop_reason})."
                )
            return payload

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
            lease_seconds=self.review_lease_seconds,
        )

    def _execute_export_work_item(
        self,
        run_id: str,
        claimed: ClaimedRunWorkItem,
        *,
        export_type: ExportType,
    ) -> None:
        pipeline_key = export_type.value
        input_bundle = self._load_work_item_input_bundle(claimed.work_item_id)
        document_id = str(input_bundle.get("document_id") or "")

        def _run_export() -> dict[str, Any]:
            with session_scope(self.session_factory) as session:
                workflow = self._workflow_service(session)
                result = workflow.export_document(
                    document_id,
                    export_type,
                    auto_execute_followup_on_gate=True,
                    max_auto_followup_attempts=self._max_auto_followup_attempts(session, run_id),
                )
            return {
                "document_id": document_id,
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
            lease_seconds=self.lease_seconds,
        )

    def _execute_claimed_work_item(
        self,
        *,
        run_id: str,
        claimed: ClaimedRunWorkItem,
        worker_fn,
        on_success,
        stage_key: str | None = None,
        lease_seconds: int | None = None,
    ) -> None:
        lease_window_seconds = max(1, int(lease_seconds or self.lease_seconds))
        stop_event = threading.Event()
        heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            kwargs={
                "lease_token": claimed.lease_token,
                "lease_seconds": lease_window_seconds,
                "stop_event": stop_event,
            },
            daemon=True,
        )
        try:
            with session_scope(self.session_factory) as session:
                execution = self._run_execution_service(session)
                execution.start_work_item(
                    lease_token=claimed.lease_token,
                    lease_seconds=lease_window_seconds,
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
        retryable = _is_retryable_exception(exc)
        pause_reason = _pause_reason_for_exception(exc)
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
                retryable=retryable,
            )
            if pause_reason is not None:
                control = self._run_control_service(session)
                summary = control.pause_run_system(
                    run_id,
                    stop_reason=pause_reason,
                    detail_json={
                        "error_class": exc.__class__.__name__,
                        "error_message": str(exc),
                        "work_item_id": claimed.work_item_id,
                        "scope_type": claimed.scope_type,
                        "scope_id": claimed.scope_id,
                    },
                )
            else:
                summary = execution.reconcile_run_terminal_state(run_id=run_id)
        self._update_pipeline_stage(
            run_id,
            stage_key,
            status=("paused" if pause_reason is not None else ("retryable_failed" if retryable else "failed")),
            extra={
                "error_class": exc.__class__.__name__,
                "error_message": str(exc),
                **({"stop_reason": pause_reason} if pause_reason is not None else {}),
            },
            current_stage=stage_key,
        )
        if summary.status in {"failed", "paused", "cancelled"}:
            self._sync_pipeline_status(run_id, summary.status)
        self.wake(run_id)

    def _claim_translate_work_items(
        self,
        *,
        session: Session,
        execution: RunExecutionService,
        run_id: str,
        translate_items: list[WorkItem],
    ) -> list[ClaimedRunWorkItem]:
        parallelism_limit = self._translate_parallelism_limit(session, run_id)
        active_items = [
            item
            for item in translate_items
            if item.status in {WorkItemStatus.LEASED, WorkItemStatus.RUNNING}
        ]
        available_slots = max(parallelism_limit - len(active_items), 0)
        if available_slots <= 0:
            return []

        active_chapter_ids = set(
            self._translate_item_chapter_id_map(session, active_items).values()
        )
        candidate_items = sorted(
            (
                item
                for item in translate_items
                if item.status in {WorkItemStatus.PENDING, WorkItemStatus.RETRYABLE_FAILED}
            ),
            key=lambda item: (item.priority, item.created_at, item.id),
        )
        candidate_chapter_map = self._translate_item_chapter_id_map(session, candidate_items)
        reserved_chapter_ids = set(active_chapter_ids)
        claimed_items: list[ClaimedRunWorkItem] = []
        for item in candidate_items:
            chapter_id = candidate_chapter_map.get(item.scope_id)
            if not chapter_id or chapter_id in reserved_chapter_ids:
                continue
            claimed = execution.claim_work_item_by_id(
                work_item_id=item.id,
                worker_name="app.run.translate",
                worker_instance_id=f"app.translate:{uuid4()}",
                lease_seconds=self.lease_seconds,
            )
            if claimed is None:
                continue
            claimed_items.append(claimed)
            reserved_chapter_ids.add(chapter_id)
            if len(claimed_items) >= available_slots:
                break
        return claimed_items

    def _translate_parallelism_limit(self, session: Session, run_id: str) -> int:
        budget = RunControlRepository(session).get_budget_for_run(run_id)
        if budget is not None and budget.max_parallel_workers is not None:
            try:
                return max(1, int(budget.max_parallel_workers))
            except (TypeError, ValueError):
                return self.default_max_parallel_workers
        if self._is_sqlite_session(session):
            return 1
        return self.default_max_parallel_workers

    def _translate_input_versions(
        self,
        session: Session,
        packet_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        chapter_map = self._packet_chapter_id_map(session, packet_ids)
        return {
            packet_id: {
                "packet_id": packet_id,
                **({"chapter_id": chapter_map[packet_id]} if packet_id in chapter_map else {}),
            }
            for packet_id in packet_ids
        }

    def _translate_item_chapter_id_map(
        self,
        session: Session,
        items: list[WorkItem],
    ) -> dict[str, str]:
        packet_ids_to_query: list[str] = []
        chapter_map: dict[str, str] = {}
        for item in items:
            bundle = dict(item.input_version_bundle_json or {})
            chapter_id = bundle.get("chapter_id")
            if chapter_id:
                chapter_map[str(item.scope_id)] = str(chapter_id)
            else:
                packet_ids_to_query.append(str(item.scope_id))
        if packet_ids_to_query:
            chapter_map.update(self._packet_chapter_id_map(session, packet_ids_to_query))
        return chapter_map

    def _packet_chapter_id_map(
        self,
        session: Session,
        packet_ids: list[str],
    ) -> dict[str, str]:
        normalized_ids = [packet_id for packet_id in packet_ids if packet_id]
        if not normalized_ids:
            return {}
        rows = session.execute(
            select(TranslationPacket.id, TranslationPacket.chapter_id).where(
                TranslationPacket.id.in_(normalized_ids)
            )
        ).all()
        return {str(packet_id): str(chapter_id) for packet_id, chapter_id in rows}

    def _heartbeat_loop(self, *, lease_token: str, lease_seconds: int, stop_event: threading.Event) -> None:
        while not stop_event.wait(timeout=max(1, self.heartbeat_interval_seconds)):
            try:
                with session_scope(self.session_factory) as session:
                    execution = self._run_execution_service(session)
                    alive = execution.heartbeat_work_item(
                        lease_token=lease_token,
                        lease_seconds=lease_seconds,
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

    def _load_work_item_input_bundle(self, work_item_id: str) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            repository = RunControlRepository(session)
            work_item = repository.get_work_item(work_item_id)
            return dict(work_item.input_version_bundle_json or {})

    def _max_auto_followup_attempts(self, session, run_id: str) -> int:
        budget = RunControlRepository(session).get_budget_for_run(run_id)
        if budget is not None and budget.max_auto_followup_attempts is not None:
            return max(1, int(budget.max_auto_followup_attempts))
        return self.default_max_auto_followup_attempts

    def _max_blocker_repair_rounds(self, session, run_id: str) -> int:
        return max(
            self.default_max_blocker_repair_rounds,
            self._max_auto_followup_attempts(session, run_id),
        )

    def _update_pipeline_stage(
        self,
        run_id: str,
        stage_key: str,
        *,
        status: str,
        extra: dict[str, Any] | None = None,
        current_stage: str | None = None,
        session: Session | None = None,
    ) -> None:
        if session is not None:
            self._do_update_pipeline_stage(session, run_id, stage_key, status, extra, current_stage)
            return

        attempts = 3 if session is None else 1
        for attempt in range(attempts):
            try:
                with session_scope(self.session_factory) as s:
                    self._do_update_pipeline_stage(s, run_id, stage_key, status, extra, current_stage)
                return
            except OperationalError as exc:
                if "database is locked" not in str(exc).lower():
                    raise
                if attempt == attempts - 1:
                    return
            time.sleep(0.1 * (attempt + 1))

    def _do_update_pipeline_stage(
        self,
        session: Session,
        run_id: str,
        stage_key: str,
        status: str,
        extra: dict[str, Any] | None,
        current_stage: str | None,
    ) -> None:
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
