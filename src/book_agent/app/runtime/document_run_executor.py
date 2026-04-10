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

from book_agent.app.runtime.controller_runner import ControllerRunner
from book_agent.app.runtime.controllers.export_controller import ExportController
from book_agent.app.runtime.controllers.incident_controller import IncidentController
from book_agent.core.ids import stable_id
from book_agent.domain.enums import (
    JobScopeType,
    DocumentRunStatus,
    DocumentRunType,
    ExportType,
    PacketStatus,
    RuntimeIncidentKind,
    WorkItemScopeType,
    WorkItemStage,
    WorkItemStatus,
)
from book_agent.domain.models import Block, Chapter
from book_agent.domain.models.ops import DocumentRun, RuntimeIncident, RuntimePatchProposal, WorkItem
from book_agent.infra.repositories.runtime_resources import RuntimeResourcesRepository
from book_agent.domain.models.translation import TranslationPacket
from book_agent.infra.db.session import session_scope
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.orchestrator.state_machine import (
    PACKET_RUNTIME_SUBSTATE_LEASED,
    PACKET_RUNTIME_SUBSTATE_RETRYABLE_FAILED,
    PACKET_RUNTIME_SUBSTATE_RUNNING,
    PACKET_RUNTIME_SUBSTATE_TERMINAL_FAILED,
    PACKET_RUNTIME_SUBSTATE_TRANSLATED,
    build_packet_runtime_state,
    packet_runtime_state,
)
from book_agent.services.run_control import RunControlService
from book_agent.services.run_execution import ClaimedRunWorkItem, RunExecutionService
from book_agent.services.runtime_repair_executor import RuntimeRepairExecutorRegistry
from book_agent.services.runtime_repair_registry import RuntimeRepairWorkerRegistry
from book_agent.services.runtime_repair_worker import RuntimeRepairDecisionError
from book_agent.services.export_routing import ExportRoutingError
from book_agent.services.workflows import DocumentWorkflowService
from book_agent.workers.translator import TranslationWorker


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _append_recovered_lineage(
    runtime_v2: dict[str, Any],
    *,
    lineage_entry: dict[str, Any],
) -> None:
    existing_entries = [
        dict(entry)
        for entry in (runtime_v2.get("recovered_lineage") or [])
        if isinstance(entry, dict)
    ]
    proposal_id = lineage_entry.get("proposal_id")
    if proposal_id:
        existing_entries = [
            entry
            for entry in existing_entries
            if entry.get("proposal_id") != proposal_id
        ]
    existing_entries.append(lineage_entry)
    runtime_v2["recovered_lineage"] = existing_entries


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
        "export misrouting",
        "selected route",
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
    ensure_database_state = getattr(app.state, "ensure_database_state", None)
    if callable(ensure_database_state):
        ensure_database_state()
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
        controller_reconcile_interval_seconds: float = 10.0,
        enable_controller_runner: bool = True,
        lease_seconds: int = 120,
        review_lease_seconds: int = 1800,
        heartbeat_interval_seconds: int = 15,
        default_max_auto_followup_attempts: int = 2,
        default_max_blocker_repair_rounds: int = 10,
        default_max_parallel_workers: int = 8,
    ) -> None:
        self.session_factory = session_factory
        self.export_root = str(Path(export_root).resolve())
        self.translation_worker = translation_worker
        self.poll_interval_seconds = poll_interval_seconds
        self.controller_reconcile_interval_seconds = max(0.0, float(controller_reconcile_interval_seconds))
        self._controller_runner = ControllerRunner(session_factory) if enable_controller_runner else None
        self._controller_last_reconcile_at_by_run: dict[str, float] = {}
        self.lease_seconds = lease_seconds
        self.review_lease_seconds = max(self.lease_seconds, int(review_lease_seconds))
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.default_max_auto_followup_attempts = default_max_auto_followup_attempts
        self.default_max_blocker_repair_rounds = max(1, int(default_max_blocker_repair_rounds))
        self.default_max_parallel_workers = max(1, int(default_max_parallel_workers))
        self._runtime_repair_registry = RuntimeRepairWorkerRegistry(session_factory=session_factory)
        self._runtime_repair_executor_registry = RuntimeRepairExecutorRegistry(session_factory=session_factory)
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._supervisor_thread: threading.Thread | None = None
        self._active_run_threads: dict[str, threading.Thread] = {}
        self._active_work_threads: dict[str, dict[str, threading.Thread]] = {}
        self._controller_runner = ControllerRunner(session_factory)
        self._lock = threading.Lock()

    def _maybe_reconcile_controllers(self, run_id: str) -> None:
        """
        Phase A: best-effort controller reconcile integration.

        Contract (for now):
        - Mirror-only: controllers may only create/update Runtime V2 resources/checkpoints.
        - Must not block or fail the existing V1 run loop.
        """

        runner = self._controller_runner
        if runner is None:
            return
        now = time.monotonic()
        last = self._controller_last_reconcile_at_by_run.get(run_id)
        if last is not None and (now - last) < self.controller_reconcile_interval_seconds:
            return
        self._controller_last_reconcile_at_by_run[run_id] = now

        try:
            runner.reconcile_run(run_id=run_id)
        except OperationalError:
            return
        except Exception:
            # Keep Phase A wiring strictly non-invasive (no behavior change to V1 runner).
            return

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
                self._maybe_reconcile_controllers(run_id)
                self._reclaim_expired_leases(run_id)
                if self._process_repair_stage(run_id):
                    continue
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

    def _reconcile_runtime_resources(self, run_id: str) -> None:
        try:
            self._controller_runner.reconcile_run(run_id=run_id)
        except Exception:
            # Phase A is mirror-only; control-plane scaffolding must not interrupt the V1 run loop.
            return

    def _reclaim_expired_leases(self, run_id: str) -> bool:
        with session_scope(self.session_factory) as session:
            execution = self._run_execution_service(session)
            reclaimed = execution.reclaim_expired_leases(run_id=run_id)
            if reclaimed.reclaimed_work_item_ids:
                reclaimed_items = session.scalars(
                    select(WorkItem).where(WorkItem.id.in_(reclaimed.reclaimed_work_item_ids))
                ).all()
                for item in reclaimed_items:
                    if item.stage != WorkItemStage.TRANSLATE or item.scope_type != WorkItemScopeType.PACKET:
                        continue
                    self._update_translate_packet_runtime_state(
                        session,
                        packet_id=str(item.scope_id),
                        substate=PACKET_RUNTIME_SUBSTATE_RETRYABLE_FAILED,
                        run_id=run_id,
                        work_item_id=item.id,
                        attempt=item.attempt,
                    )
        return reclaimed.expired_lease_count > 0

    def _process_translate_stage(self, run_id: str) -> bool:
        with session_scope(self.session_factory) as session:
            repository = RunControlRepository(session)
            execution = self._run_execution_service(session)
            run = repository.get_run(run_id)
            document_id = run.document_id
            translate_items = self._list_stage_items(session, run_id, WorkItemStage.TRANSLATE)
            if self._reconcile_translate_work_items(
                session=session,
                run_id=run_id,
                document_id=document_id,
                translate_items=translate_items,
            ):
                translate_items = self._list_stage_items(session, run_id, WorkItemStage.TRANSLATE)
            active_translate_items = [
                item for item in translate_items if item.status != WorkItemStatus.CANCELLED
            ]
            seeded_packet_ids = self._seed_translate_frontier_work_items(
                session=session,
                execution=execution,
                run_id=run_id,
                document_id=document_id,
                translate_items=active_translate_items,
            )
            if seeded_packet_ids:
                translate_items = self._list_stage_items(session, run_id, WorkItemStage.TRANSLATE)
                active_translate_items = [
                    item for item in translate_items if item.status != WorkItemStatus.CANCELLED
                ]
                self._update_pipeline_stage(
                    run_id,
                    "translate",
                    status="pending",
                    extra={
                        "total_packet_count": len(self._list_all_packet_ids(session, document_id)),
                        "pending_packet_count": len(self._list_pending_packet_ids(session, document_id)),
                    },
                    current_stage="translate",
                    session=session,
                )
            if not active_translate_items:
                packet_ids = self._list_pending_packet_ids(session, document_id)
                current_stage = "translate" if packet_ids else "review"
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
                    current_stage=current_stage,
                    session=session,
                )
                return bool(packet_ids)

            if any(item.status == WorkItemStatus.TERMINAL_FAILED for item in active_translate_items):
                return False

            claimed_items: list[ClaimedRunWorkItem] = []
            if any(
                item.status in {WorkItemStatus.PENDING, WorkItemStatus.RETRYABLE_FAILED}
                for item in active_translate_items
            ):
                self._update_pipeline_stage(run_id, "translate", status="running", current_stage="translate", session=session)
                claimed_items = self._claim_translate_work_items(
                    session=session,
                    execution=execution,
                    run_id=run_id,
                    translate_items=active_translate_items,
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

        if active_translate_items and all(item.status == WorkItemStatus.SUCCEEDED for item in active_translate_items):
            self._update_pipeline_stage(run_id, "translate", status="succeeded", current_stage="review")
        return False

    def _process_repair_stage(self, run_id: str) -> bool:
        with session_scope(self.session_factory) as session:
            execution = self._run_execution_service(session)
            repair_items = self._list_stage_items(session, run_id, WorkItemStage.REPAIR)
            if not repair_items:
                return False
            if any(item.status == WorkItemStatus.TERMINAL_FAILED for item in repair_items):
                return False

            claimed_items: list[ClaimedRunWorkItem] = []
            if any(item.status in {WorkItemStatus.PENDING, WorkItemStatus.RETRYABLE_FAILED} for item in repair_items):
                claimed = execution.claim_next_work_item(
                    run_id=run_id,
                    stage=WorkItemStage.REPAIR,
                    worker_name="app.run.repair",
                    worker_instance_id=f"app.repair:{uuid4()}",
                    lease_seconds=self.lease_seconds,
                )
                if claimed is not None:
                    claimed_items.append(claimed)

        if claimed_items:
            for claimed in claimed_items:
                self._ensure_work_thread(
                    run_id=run_id,
                    work_item_id=claimed.work_item_id,
                    thread_name=f"book-agent-repair-{claimed.work_item_id}",
                    target=lambda claimed=claimed: self._execute_repair_work_item(run_id, claimed),
                )
            return True
        return False

    def _seed_translate_frontier_work_items(
        self,
        *,
        session: Session,
        execution: RunExecutionService,
        run_id: str,
        document_id: str,
        translate_items: list[WorkItem],
    ) -> list[str]:
        packet_ids = self._list_seedable_translate_packet_ids(
            session=session,
            run_id=run_id,
            document_id=document_id,
            translate_items=translate_items,
        )
        if not packet_ids:
            return []
        execution.seed_translate_work_items(
            run_id=run_id,
            packet_ids=packet_ids,
            input_version_bundle_by_packet_id=self._translate_input_versions(session, packet_ids),
        )
        return packet_ids

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
        input_bundle = self._load_work_item_input_bundle(claimed.work_item_id)

        def _run_review() -> dict[str, Any]:
            payload: dict[str, Any]
            remaining_blocking_issue_count = 0
            stop_reason = "unknown"
            with session_scope(self.session_factory) as session:
                workflow = self._workflow_service(session)
                if claimed.scope_type == WorkItemScopeType.CHAPTER.value:
                    chapter_id = str(input_bundle.get("chapter_id") or claimed.scope_id)
                    document_id = str(input_bundle.get("document_id") or "")
                    artifacts = workflow.review_service.review_chapter(chapter_id)
                    remaining_blocking_issue_count = int(artifacts.summary.blocking_issue_count or 0)
                    payload = {
                        "document_id": document_id,
                        "chapter_id": chapter_id,
                        "total_issue_count": len(artifacts.issues),
                        "total_action_count": len(artifacts.actions),
                        "chapter_count": 1,
                        "auto_followup_requested": False,
                        "auto_followup_applied": False,
                        "auto_followup_attempt_count": 0,
                        "blocker_repair_requested": False,
                        "blocker_repair_applied": False,
                        "blocker_repair_round_count": 0,
                        "blocker_repair_round_limit": 0,
                        "blocker_repair_execution_count": 0,
                        "remaining_blocking_issue_count": remaining_blocking_issue_count,
                    }
                else:
                    document_id = str(input_bundle.get("document_id") or claimed.scope_id)
                    initial_result = workflow.review_document(
                        document_id,
                        auto_execute_packet_followups=True,
                        max_auto_followup_attempts=self._max_auto_followup_attempts(session, run_id),
                    )
                    repair_result = workflow.repair_document_blockers_until_exportable(
                        document_id,
                        max_rounds=self._max_blocker_repair_rounds(session, run_id),
                    )
                    result = initial_result
                    if repair_result.applied:
                        result = workflow.review_document(
                            document_id,
                            auto_execute_packet_followups=False,
                        )
                    remaining_blocking_issue_count = repair_result.blocking_issue_count_after
                    stop_reason = repair_result.stop_reason or "unknown"
                    payload = {
                        "document_id": document_id,
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
                    output_artifact_refs_json={
                        "document_id": str(payload.get("document_id") or ""),
                        **({"chapter_id": str(payload["chapter_id"])} if payload.get("chapter_id") else {}),
                    },
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

    def _execute_repair_work_item(self, run_id: str, claimed: ClaimedRunWorkItem) -> None:
        input_bundle = self._load_work_item_input_bundle(claimed.work_item_id)
        repair_agent = None
        repair_executor = None

        def _prepare_repair_execution() -> dict[str, Any]:
            nonlocal repair_agent, repair_executor
            repair_agent = self._runtime_repair_registry.resolve_for_input_bundle(input_bundle)
            repair_executor = self._runtime_repair_executor_registry.resolve_for_input_bundle(
                input_bundle=input_bundle,
                repair_agent=repair_agent,
            )
            return repair_executor.prepare_execution(
                claimed=claimed,
                input_bundle=input_bundle,
            )

        def _complete_repair_execution(payload: dict[str, Any], lease_token: str) -> None:
            if repair_executor is None:
                raise RuntimeError("Repair executor was not resolved before completion.")
            repair_executor.complete_execution(
                run_id=run_id,
                payload=payload,
                lease_token=lease_token,
            )

        self._execute_claimed_work_item(
            run_id=run_id,
            claimed=claimed,
            worker_fn=_prepare_repair_execution,
            on_success=_complete_repair_execution,
            stage_key="repair",
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
                if (
                    claimed.stage == WorkItemStage.TRANSLATE.value
                    and claimed.scope_type == WorkItemScopeType.PACKET.value
                ):
                    self._update_translate_packet_runtime_state(
                        session,
                        packet_id=claimed.scope_id,
                        substate=PACKET_RUNTIME_SUBSTATE_RUNNING,
                        run_id=run_id,
                        work_item_id=claimed.work_item_id,
                        attempt=claimed.attempt,
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
            artifacts = workflow.translation_service.execute_packet(
                packet_id,
                auto_commit_memory=False,
            )
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
            self._update_translate_packet_runtime_state(
                session,
                packet_id=str(payload["packet_id"]),
                substate=PACKET_RUNTIME_SUBSTATE_TRANSLATED,
            )

    def _complete_failure(
        self,
        *,
        run_id: str,
        claimed: ClaimedRunWorkItem,
        exc: Exception,
        stage_key: str,
    ) -> None:
        export_misrouting = isinstance(exc, ExportRoutingError)
        retryable = _is_retryable_exception(exc) or export_misrouting
        pause_reason = _pause_reason_for_exception(exc)
        error_class = exc.__class__.__name__
        error_detail = {
            "message": str(exc),
            "traceback": traceback.format_exc(limit=8),
        }
        repair_result_json: dict[str, Any] | None = None
        if isinstance(exc, RuntimeRepairDecisionError):
            retryable = exc.retryable
            repair_result_json = dict(exc.result_json or {})
            error_class = exc.__class__.__name__
            if exc.decision:
                error_detail["repair_agent_decision"] = exc.decision
            if exc.decision_reason:
                error_detail["repair_agent_decision_reason"] = exc.decision_reason
            if repair_result_json:
                error_detail["repair_result_json"] = dict(repair_result_json)
        with session_scope(self.session_factory) as session:
            execution = self._run_execution_service(session)
            execution.complete_work_item_failure(
                lease_token=claimed.lease_token,
                error_class=error_class,
                error_detail_json=error_detail,
                retryable=retryable,
            )
            if claimed.stage == WorkItemStage.REPAIR.value:
                input_bundle = self._load_work_item_input_bundle(claimed.work_item_id)
                proposal_id = str(input_bundle.get("proposal_id") or claimed.scope_id)
                try:
                    result_json = dict(repair_result_json or {})
                    result_json.setdefault("error_class", error_class)
                    result_json.setdefault("error_message", str(exc))
                    IncidentController(session=session).record_repair_dispatch_execution(
                        proposal_id=proposal_id,
                        succeeded=False,
                        result_json=result_json,
                        manage_work_item_lifecycle=False,
                    )
                except Exception:
                    pass
            if (
                claimed.stage == WorkItemStage.TRANSLATE.value
                and claimed.scope_type == WorkItemScopeType.PACKET.value
            ):
                self._update_translate_packet_runtime_state(
                    session,
                    packet_id=claimed.scope_id,
                    substate=(
                        PACKET_RUNTIME_SUBSTATE_RETRYABLE_FAILED
                        if retryable and pause_reason is None
                        else PACKET_RUNTIME_SUBSTATE_TERMINAL_FAILED
                    ),
                    run_id=run_id,
                    work_item_id=claimed.work_item_id,
                    attempt=claimed.attempt,
                )
            if export_misrouting and claimed.stage == WorkItemStage.EXPORT.value:
                self._recover_export_misrouting(
                    session=session,
                    run_id=run_id,
                    claimed=claimed,
                    exc=exc,
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
                "error_class": error_class,
                "error_message": str(exc),
                **({"stop_reason": pause_reason} if pause_reason is not None else {}),
            },
            current_stage=stage_key,
        )
        if summary.status in {"failed", "paused", "cancelled"}:
            self._sync_pipeline_status(run_id, summary.status)
        self.wake(run_id)

    def _recover_export_misrouting(
        self,
        *,
        session: Session,
        run_id: str,
        claimed: ClaimedRunWorkItem,
        exc: Exception,
    ) -> None:
        export_error = exc if isinstance(exc, ExportRoutingError) else None
        route_evidence_json = dict(getattr(export_error, "route_evidence_json", {}) or {})
        route_candidates = list(getattr(export_error, "expected_route_candidates", []) or [])
        selected_route = str(
            getattr(export_error, "selected_route", "")
            or route_evidence_json.get("selected_route")
            or ""
        )
        source_type = str(route_evidence_json.get("source_type") or "epub")
        runtime_bundle_revision_id = route_evidence_json.get("runtime_bundle_revision_id")
        export_type = str(route_evidence_json.get("export_type") or "rebuilt_pdf")
        run = session.get(DocumentRun, run_id)
        if run is not None:
            status_detail = dict(run.status_detail_json or {})
            runtime_v2 = dict(status_detail.get("runtime_v2") or {})
            runtime_v2["last_export_route_evidence"] = route_evidence_json
            status_detail["runtime_v2"] = runtime_v2
            run.status_detail_json = status_detail
            run.updated_at = _utcnow()
            session.add(run)
            session.flush()
        try:
            controller = ExportController(session=session)
            recovery = controller.recover_export_misrouting(
                run_id=run_id,
                work_item_id=claimed.work_item_id,
                scope_id=claimed.scope_id,
                source_type=source_type,
                selected_route=selected_route,
                runtime_bundle_revision_id=(
                    str(runtime_bundle_revision_id) if runtime_bundle_revision_id is not None else None
                ),
                route_candidates=route_candidates,
                route_evidence_json=route_evidence_json,
                error_message=str(exc),
                export_type=export_type,
            )
        except Exception as recovery_exc:  # pragma: no cover - defensive recovery path
            if run is not None:
                status_detail = dict(run.status_detail_json or {})
                runtime_v2 = dict(status_detail.get("runtime_v2") or {})
                runtime_v2["last_export_route_recovery_error"] = {
                    "error_class": recovery_exc.__class__.__name__,
                    "error_message": str(recovery_exc),
                }
                status_detail["runtime_v2"] = runtime_v2
                run.status_detail_json = status_detail
                run.updated_at = _utcnow()
                session.add(run)
                session.flush()
            return

        if run is not None:
            status_detail = dict(run.status_detail_json or {})
            runtime_v2 = dict(status_detail.get("runtime_v2") or {})
            runtime_v2["pending_export_route_repair"] = {
                "incident_id": recovery.incident_id,
                "proposal_id": recovery.proposal_id,
                "repair_work_item_id": recovery.repair_work_item_id,
                "selected_route": selected_route,
                "corrected_route": recovery.corrected_route,
                "route_candidates": route_candidates,
                "replay_scope_id": claimed.scope_id,
            }
            status_detail["runtime_v2"] = runtime_v2
            run.status_detail_json = status_detail
            run.updated_at = _utcnow()
            session.add(run)
            session.flush()
        self.wake(run_id)

    def _finalize_export_route_repair(
        self,
        *,
        session: Session,
        run_id: str,
        incident: RuntimeIncident,
        proposal: RuntimePatchProposal,
        bundle_revision_id: str,
        corrected_route: str | None = None,
    ) -> None:
        run = session.get(DocumentRun, run_id)
        if run is None:
            return
        proposal_detail = dict(proposal.status_detail_json or {})
        bundle_guard = dict(proposal_detail.get("bundle_guard") or {})
        route_candidates = list((incident.bundle_json or {}).get("route_candidates") or [])
        export_type = (incident.bundle_json or {}).get("export_type")
        route_evidence_json = dict(incident.route_evidence_json or {})
        published_bundle_revision_id = proposal.published_bundle_revision_id or bundle_revision_id
        active_bundle_revision_id = str(
            bundle_guard.get("effective_revision_id")
            or run.runtime_bundle_revision_id
            or published_bundle_revision_id
        )
        rollback_target_revision_id = bundle_guard.get("rollback_target_revision_id")
        rollback_performed = bool(bundle_guard.get("rollback_performed"))
        bound_work_item_ids = [
            str(work_item_id)
            for work_item_id in list(proposal_detail.get("bound_work_item_ids") or [])
            if str(work_item_id).strip()
        ]
        repair_dispatch = dict(proposal_detail.get("repair_dispatch") or {})
        replay_scope_id = str((repair_dispatch.get("replay") or {}).get("scope_id") or incident.scope_id)
        replay_work_item_id = bound_work_item_ids[0] if bound_work_item_ids else ""
        corrected_route = str(
            corrected_route
            or (repair_dispatch.get("last_result") or {}).get("result_json", {}).get("corrected_route")
            or (route_evidence_json.get("corrected_route"))
            or ""
        )
        lineage_entry = {
            "incident_id": incident.id,
            "proposal_id": proposal.id,
            "published_bundle_revision_id": published_bundle_revision_id,
            "active_bundle_revision_id": active_bundle_revision_id,
            "rollback_performed": rollback_performed,
            "rollback_target_revision_id": rollback_target_revision_id,
            "replay_scope_id": replay_scope_id,
            "replay_work_item_id": replay_work_item_id,
            "bound_work_item_ids": bound_work_item_ids,
            "recorded_at": _utcnow().isoformat(),
        }
        status_detail = dict(run.status_detail_json or {})
        runtime_v2 = dict(status_detail.get("runtime_v2") or {})
        runtime_v2["last_export_route_recovery"] = {
            "incident_id": incident.id,
            "proposal_id": proposal.id,
            "bundle_revision_id": published_bundle_revision_id,
            "published_bundle_revision_id": published_bundle_revision_id,
            "active_bundle_revision_id": active_bundle_revision_id,
            "selected_route": incident.selected_route,
            "rollback_performed": rollback_performed,
            "rollback_target_revision_id": rollback_target_revision_id,
            "corrected_route": corrected_route,
            "route_candidates": route_candidates,
            "export_type": export_type,
            "replay_scope_id": replay_scope_id,
            "replay_work_item_id": replay_work_item_id,
            "bound_work_item_ids": bound_work_item_ids,
        }
        runtime_v2["active_runtime_bundle_revision_id"] = active_bundle_revision_id
        runtime_v2["runtime_bundle_revision_id"] = active_bundle_revision_id
        runtime_v2.pop("pending_export_route_repair", None)
        runtime_v2["last_export_route_evidence"] = route_evidence_json
        _append_recovered_lineage(runtime_v2, lineage_entry=lineage_entry)
        status_detail["runtime_v2"] = runtime_v2
        run.status_detail_json = status_detail
        run.runtime_bundle_revision_id = active_bundle_revision_id
        run.updated_at = _utcnow()
        session.add(run)
        session.flush()

    def _finalize_review_deadlock_repair(
        self,
        *,
        session: Session,
        run_id: str,
        incident: RuntimeIncident,
        proposal: RuntimePatchProposal,
        bundle_revision_id: str,
        validation_report_json: dict[str, Any],
    ) -> None:
        runtime_repo = RuntimeResourcesRepository(session)
        route_evidence = dict(incident.route_evidence_json or {})
        chapter_run_id = str(route_evidence.get("chapter_run_id") or (incident.bundle_json or {}).get("chapter_run_id") or "")
        review_session_id = str(route_evidence.get("review_session_id") or (incident.bundle_json or {}).get("review_session_id") or "")
        chapter_id = str((proposal.status_detail_json or {}).get("repair_plan", {}).get("replay", {}).get("scope_id") or incident.scope_id)
        if not chapter_run_id or not review_session_id or not chapter_id:
            return
        review_session = runtime_repo.get_review_session(review_session_id)
        chapter_run = runtime_repo.get_chapter_run(chapter_run_id)
        replay_work_item_ids = RunExecutionService(RunControlRepository(session)).ensure_scope_replay_work_items(
            run_id=run_id,
            stage=WorkItemStage.REVIEW,
            scope_type=WorkItemScopeType.CHAPTER,
            scope_ids=[chapter_id],
            input_version_bundle_by_scope_id={
                chapter_id: {
                    "document_id": chapter_run.document_id,
                    "chapter_id": chapter_id,
                    "chapter_run_id": chapter_run.id,
                    "review_session_id": review_session.id,
                }
            },
        )
        proposal_detail = dict(proposal.status_detail_json or {})
        bound_work_item_ids = [
            str(work_item_id)
            for work_item_id in list(proposal_detail.get("bound_work_item_ids") or [])
            if str(work_item_id).strip()
        ]
        recovery_payload = {
            "incident_id": incident.id,
            "proposal_id": proposal.id,
            "bundle_revision_id": bundle_revision_id,
            "repair_work_item_id": str((proposal_detail.get("repair_dispatch") or {}).get("repair_work_item_id") or ""),
            "replay_scope_id": chapter_id,
            "replay_work_item_ids": replay_work_item_ids,
            "bound_work_item_ids": bound_work_item_ids,
            "reason_code": route_evidence.get("reason_code"),
            "lane_health_state": route_evidence.get("lane_health_state"),
            "status": "published",
        }
        runtime_repo.merge_review_session_status_detail(
            review_session.id,
            {
                "runtime_v2": {
                    "last_deadlock_recovery": recovery_payload,
                }
            },
        )
        runtime_repo.append_chapter_recovered_lineage(
            chapter_run_id=chapter_run.id,
            lineage_event={
                "source": "runtime.review_deadlock",
                "incident_id": incident.id,
                "proposal_id": proposal.id,
                "bundle_revision_id": bundle_revision_id,
                "replay_scope_id": chapter_id,
                "repair_work_item_id": str((proposal_detail.get("repair_dispatch") or {}).get("repair_work_item_id") or ""),
                "status": "published",
            },
        )
        runtime_repo.upsert_checkpoint(
            run_id=run_id,
            scope_type=JobScopeType.CHAPTER,
            scope_id=chapter_id,
            checkpoint_key="review_controller.deadlock_recovery",
            checkpoint_json={
                "chapter_run_id": chapter_run.id,
                "review_session_id": review_session.id,
                "recovery": recovery_payload,
                "validation_report": validation_report_json,
            },
            generation=int(chapter_run.generation or 1),
        )

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
        candidate_metadata = self._translate_item_lane_metadata_map(session, candidate_items)
        chapter_frontier: dict[str, WorkItem] = {}
        for item in candidate_items:
            metadata = candidate_metadata.get(str(item.scope_id), {})
            chapter_id = str(metadata.get("chapter_id") or "").strip()
            if not chapter_id or chapter_id in active_chapter_ids:
                continue
            existing = chapter_frontier.get(chapter_id)
            if existing is None:
                chapter_frontier[chapter_id] = item
                continue
            if self._translate_item_lane_sort_key(item, metadata) < self._translate_item_lane_sort_key(
                existing,
                candidate_metadata.get(str(existing.scope_id), {}),
            ):
                chapter_frontier[chapter_id] = item
        chapter_frontier_items = sorted(
            chapter_frontier.values(),
            key=lambda item: (
                item.priority,
                item.created_at,
                self._translate_item_lane_sort_key(
                    item,
                    candidate_metadata.get(str(item.scope_id), {}),
                ),
                item.id,
            ),
        )
        reserved_chapter_ids = set(active_chapter_ids)
        claimed_items: list[ClaimedRunWorkItem] = []
        for item in chapter_frontier_items:
            chapter_id = str(candidate_metadata.get(str(item.scope_id), {}).get("chapter_id") or "").strip()
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
            self._update_translate_packet_runtime_state(
                session,
                packet_id=str(item.scope_id),
                substate=PACKET_RUNTIME_SUBSTATE_LEASED,
                run_id=run_id,
                work_item_id=item.id,
                attempt=claimed.attempt,
            )
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
        return self.default_max_parallel_workers

    def _reconcile_translate_work_items(
        self,
        *,
        session: Session,
        run_id: str,
        document_id: str,
        translate_items: list[WorkItem],
    ) -> bool:
        if not translate_items:
            return False

        document_packet_ids = set(self._list_all_packet_ids(session, document_id))
        if not document_packet_ids:
            return False

        packet_map = {
            str(packet.id): packet
            for packet in session.scalars(
                select(TranslationPacket).where(TranslationPacket.id.in_(list(document_packet_ids)))
            ).all()
        }
        packet_metadata = self._packet_lane_metadata_map(session, list(document_packet_ids))
        updated = False

        for item in translate_items:
            if item.scope_type != WorkItemScopeType.PACKET:
                continue

            resolved_packet_id = self._resolve_translate_item_packet_id(
                item=item,
                document_packet_ids=document_packet_ids,
            )
            if not resolved_packet_id:
                if item.status in {WorkItemStatus.PENDING, WorkItemStatus.RETRYABLE_FAILED}:
                    self._cancel_translate_item(
                        item,
                        reason="stale_translate_packet_reference",
                        detail={
                            "scope_id": str(item.scope_id),
                            "input_packet_id": str((item.input_version_bundle_json or {}).get("packet_id") or ""),
                            "document_id": document_id,
                            "run_id": run_id,
                        },
                    )
                    updated = True
                continue

            if str(item.scope_id) != resolved_packet_id:
                item.scope_id = resolved_packet_id
                updated = True

            bundle = dict(item.input_version_bundle_json or {})
            metadata = packet_metadata.get(resolved_packet_id, {})
            normalized_bundle = dict(bundle)
            normalized_bundle["packet_id"] = resolved_packet_id
            if metadata.get("chapter_id") is not None:
                normalized_bundle["chapter_id"] = str(metadata["chapter_id"])
            if metadata.get("packet_ordinal") is not None:
                normalized_bundle["packet_ordinal"] = int(metadata["packet_ordinal"])
            runtime_substate = metadata.get("runtime_substate")
            if runtime_substate:
                normalized_bundle["packet_runtime_substate"] = str(runtime_substate)
            if normalized_bundle != bundle:
                item.input_version_bundle_json = normalized_bundle
                updated = True

            packet = packet_map.get(resolved_packet_id)
            if (
                packet is not None
                and item.status in {WorkItemStatus.PENDING, WorkItemStatus.RETRYABLE_FAILED}
                and packet.status == PacketStatus.TRANSLATED
            ):
                self._cancel_translate_item(
                    item,
                    reason="obsolete_translate_work_item_for_translated_packet",
                    detail={
                        "packet_id": resolved_packet_id,
                        "document_id": document_id,
                        "run_id": run_id,
                    },
                )
                updated = True

        if updated:
            session.flush()
        return updated

    def _resolve_translate_item_packet_id(
        self,
        *,
        item: WorkItem,
        document_packet_ids: set[str],
    ) -> str | None:
        scope_packet_id = str(item.scope_id or "").strip()
        if scope_packet_id in document_packet_ids:
            return scope_packet_id

        bundle_packet_id = str((item.input_version_bundle_json or {}).get("packet_id") or "").strip()
        if bundle_packet_id in document_packet_ids:
            return bundle_packet_id
        return None

    def _cancel_translate_item(
        self,
        item: WorkItem,
        *,
        reason: str,
        detail: dict[str, Any],
    ) -> None:
        item.status = WorkItemStatus.CANCELLED
        item.lease_owner = None
        item.lease_expires_at = None
        item.last_heartbeat_at = None
        item.started_at = None
        item.finished_at = _utcnow()
        item.updated_at = _utcnow()
        item.error_class = reason
        item.error_detail_json = detail
        item.output_artifact_refs_json = dict(item.output_artifact_refs_json or {})

    def _translate_input_versions(
        self,
        session: Session,
        packet_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        packet_metadata = self._packet_lane_metadata_map(session, packet_ids)
        return {
            packet_id: {
                "packet_id": packet_id,
                **(
                    {
                        "chapter_id": packet_metadata[packet_id]["chapter_id"],
                        "packet_ordinal": packet_metadata[packet_id]["packet_ordinal"],
                        "packet_runtime_substate": packet_metadata[packet_id].get("runtime_substate"),
                    }
                    if packet_id in packet_metadata
                    else {}
                ),
            }
            for packet_id in packet_ids
        }

    def _translate_item_chapter_id_map(
        self,
        session: Session,
        items: list[WorkItem],
    ) -> dict[str, str]:
        return {
            packet_id: str(metadata["chapter_id"])
            for packet_id, metadata in self._translate_item_lane_metadata_map(session, items).items()
            if metadata.get("chapter_id")
        }

    def _translate_item_lane_metadata_map(
        self,
        session: Session,
        items: list[WorkItem],
    ) -> dict[str, dict[str, Any]]:
        packet_ids_to_query: list[str] = []
        metadata_map: dict[str, dict[str, Any]] = {}
        for item in items:
            bundle = dict(item.input_version_bundle_json or {})
            chapter_id = bundle.get("chapter_id")
            packet_ordinal = self._coerce_packet_ordinal(bundle.get("packet_ordinal"))
            runtime_substate = str(bundle.get("packet_runtime_substate") or "").strip() or None
            if chapter_id and packet_ordinal is not None:
                metadata_map[str(item.scope_id)] = {
                    "chapter_id": str(chapter_id),
                    "packet_ordinal": packet_ordinal,
                    "runtime_substate": runtime_substate,
                }
            else:
                packet_ids_to_query.append(str(item.scope_id))
        if packet_ids_to_query:
            metadata_map.update(self._packet_lane_metadata_map(session, packet_ids_to_query))
        return metadata_map

    def _packet_chapter_id_map(
        self,
        session: Session,
        packet_ids: list[str],
    ) -> dict[str, str]:
        return {
            packet_id: str(metadata["chapter_id"])
            for packet_id, metadata in self._packet_lane_metadata_map(session, packet_ids).items()
            if metadata.get("chapter_id")
        }

    def _packet_lane_metadata_map(
        self,
        session: Session,
        packet_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        normalized_ids = [packet_id for packet_id in packet_ids if packet_id]
        if not normalized_ids:
            return {}
        rows = session.execute(
            select(
                TranslationPacket.id,
                TranslationPacket.chapter_id,
                TranslationPacket.packet_json,
                Block.ordinal,
            )
            .outerjoin(Block, Block.id == TranslationPacket.block_start_id)
            .where(TranslationPacket.id.in_(normalized_ids))
        ).all()
        metadata: dict[str, dict[str, Any]] = {}
        for packet_id, chapter_id, packet_json, block_ordinal in rows:
            packet_payload = dict(packet_json or {})
            input_bundle = packet_payload.get("input_version_bundle")
            if not isinstance(input_bundle, dict):
                input_bundle = {}
            runtime = packet_runtime_state(packet_payload)
            packet_ordinal = self._coerce_packet_ordinal(
                input_bundle.get("packet_ordinal")
                or packet_payload.get("packet_ordinal")
                or runtime.get("packet_ordinal")
                or block_ordinal
            )
            metadata[str(packet_id)] = {
                "chapter_id": str(chapter_id),
                "packet_ordinal": packet_ordinal if packet_ordinal is not None else 10**9,
                "runtime_substate": str(runtime.get("substate") or "").strip() or None,
            }
        return metadata

    def _list_seedable_translate_packet_ids(
        self,
        *,
        session: Session,
        run_id: str,
        document_id: str,
        translate_items: list[WorkItem] | None = None,
    ) -> list[str]:
        stage_items = translate_items if translate_items is not None else self._list_stage_items(
            session,
            run_id,
            WorkItemStage.TRANSLATE,
        )
        chapter_blocking_items = [
            item
            for item in stage_items
            if item.status in {
                WorkItemStatus.PENDING,
                WorkItemStatus.RETRYABLE_FAILED,
                WorkItemStatus.LEASED,
                WorkItemStatus.RUNNING,
            }
        ]
        blocked_chapter_ids = set(self._translate_item_chapter_id_map(session, chapter_blocking_items).values())
        represented_packet_ids = {str(item.scope_id) for item in stage_items}
        candidate_packet_ids = self._list_pending_packet_ids(session, document_id)
        if not candidate_packet_ids:
            return []

        candidate_metadata = self._packet_lane_metadata_map(session, candidate_packet_ids)
        frontier_by_chapter: dict[str, str] = {}
        for packet_id in candidate_packet_ids:
            metadata = candidate_metadata.get(packet_id, {})
            chapter_id = str(metadata.get("chapter_id") or "").strip()
            if not chapter_id or chapter_id in blocked_chapter_ids or packet_id in represented_packet_ids:
                continue
            existing_packet_id = frontier_by_chapter.get(chapter_id)
            if existing_packet_id is None:
                frontier_by_chapter[chapter_id] = packet_id
                continue
            if self._packet_lane_sort_key(
                packet_id,
                candidate_metadata.get(packet_id, {}),
            ) < self._packet_lane_sort_key(
                existing_packet_id,
                candidate_metadata.get(existing_packet_id, {}),
            ):
                frontier_by_chapter[chapter_id] = packet_id

        return sorted(
            frontier_by_chapter.values(),
            key=lambda packet_id: self._packet_lane_sort_key(
                packet_id,
                candidate_metadata.get(packet_id, {}),
            ),
        )

    def _packet_lane_sort_key(
        self,
        packet_id: str,
        metadata: dict[str, Any],
    ) -> tuple[int, str]:
        packet_ordinal = self._coerce_packet_ordinal(metadata.get("packet_ordinal"))
        return (
            packet_ordinal if packet_ordinal is not None else 10**9,
            str(packet_id),
        )

    def _translate_item_lane_sort_key(
        self,
        item: WorkItem,
        metadata: dict[str, Any],
    ) -> tuple[int, str, str]:
        packet_ordinal = self._coerce_packet_ordinal(metadata.get("packet_ordinal"))
        return (
            packet_ordinal if packet_ordinal is not None else 10**9,
            item.created_at.isoformat() if item.created_at is not None else "",
            str(item.id),
        )

    def _coerce_packet_ordinal(self, value: Any) -> int | None:
        try:
            if value is None:
                return None
            ordinal = int(value)
        except (TypeError, ValueError):
            return None
        return ordinal if ordinal >= 0 else None

    def _update_translate_packet_runtime_state(
        self,
        session: Session,
        *,
        packet_id: str,
        substate: str,
        run_id: str | None = None,
        work_item_id: str | None = None,
        attempt: int | None = None,
    ) -> None:
        packet = session.get(TranslationPacket, packet_id)
        if packet is None:
            return
        packet_json = dict(packet.packet_json or {})
        existing_runtime = packet_runtime_state(packet_json)
        packet_ordinal = self._coerce_packet_ordinal(
            existing_runtime.get("packet_ordinal") or packet_json.get("packet_ordinal")
        )
        packet_json["runtime_state"] = build_packet_runtime_state(
            substate=substate,
            packet_ordinal=packet_ordinal,
            run_id=run_id,
            work_item_id=work_item_id,
            attempt=attempt,
            updated_at=_utcnow().isoformat(),
        )
        packet.packet_json = packet_json
        packet.updated_at = _utcnow()
        session.merge(packet)
        session.flush()

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

        with session_scope(self.session_factory) as s:
            self._do_update_pipeline_stage(s, run_id, stage_key, status, extra, current_stage)

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
