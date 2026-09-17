from __future__ import annotations

import logging
import os
import socket
import threading
import time
import traceback
from contextlib import ExitStack
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from book_agent.infra import metrics, tracing
from book_agent.core.ids import stable_id
from book_agent.core.run_context import bind_run_context
from book_agent.domain.enums import AgentTurnStatus
from book_agent.harness.agents.repair import AGENT_KIND as RepairAgent_KIND
from book_agent.harness.agents.repair import RepairAgent, remaining_blockers
from book_agent.harness.agents.export_review import AGENT_KIND as ExportReviewAgent_KIND
from book_agent.harness.agents.export_review import ExportReviewAgent
from book_agent.harness.agents.reviewer import AGENT_KIND as ReviewerAgent_KIND
from book_agent.harness.agents.structure import AGENT_KIND as StructureAgent_KIND
from book_agent.harness.agents.structure import StructureAgent
from book_agent.harness.agents.reviewer import ReviewerAgent
from book_agent.harness.agents.terminology import AGENT_KIND as TerminologyAgent_KIND
from book_agent.harness.agents.terminology import TerminologyAgent
from book_agent.harness.kernel.openai_model import agent_model_for_worker
from book_agent.harness.kernel.turn import AgentTurnRunner
from book_agent.infra.repositories.agent import AgentLedgerRepository
from book_agent.domain.enums import (
    DocumentRunStatus,
    ExportType,
    PacketStatus,
    WorkItemScopeType,
    WorkItemStage,
    WorkItemStatus,
)
from book_agent.domain.models import Block, Chapter
from book_agent.domain.models.ops import DocumentRun, WorkItem
from book_agent.domain.models.translation import TranslationPacket
from book_agent.infra.db.session import session_scope
from book_agent.infra.repositories.run_control import LeaseLostError, RunControlRepository
from book_agent.orchestrator.frontier_plan import TranslateFrontierPlan
from book_agent.orchestrator.pipeline_stage_cache import (
    read_cached_stages,
    write_cached_stages,
)
from book_agent.orchestrator.reconciler import Reconciler
from book_agent.orchestrator.run_plan import AGENT_STAGES, EXECUTABLE_RUN_TYPES, RunPlan, plan_for_run, translate_packet_scope
from book_agent.orchestrator.stage_gate import StageGateKeeper
from book_agent.orchestrator.stage_status import (
    StageStatus,
    StageStatusCalculator,
    StageTransitionLogger,
    stage_status_to_cache_label,
)
from book_agent.orchestrator.state_machine import (
    PACKET_RUNTIME_SUBSTATE_LEASED,
    PACKET_RUNTIME_SUBSTATE_RETRYABLE_FAILED,
    PACKET_RUNTIME_SUBSTATE_RUNNING,
    PACKET_RUNTIME_SUBSTATE_TERMINAL_FAILED,
    PACKET_RUNTIME_SUBSTATE_TRANSLATED,
    build_packet_runtime_state,
    packet_runtime_state,
)
from book_agent.services.export import ExportGateError, ExportUnavailableError
from book_agent.services.export_qa import AUDITED_EXPORT_TYPES, ExportQaService
from book_agent.services.run_control import RunControlService
from book_agent.services.run_execution import ClaimedRunWorkItem, RunExecutionService
from book_agent.services.workflows import DocumentWorkflowService
from book_agent.workers.failures import classify_failure
from book_agent.workers.translator import TranslationWorker


logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_document_run_executor(app) -> "DocumentRunExecutor":
    executor = getattr(app.state, "document_run_executor", None)
    if executor is not None:
        return executor
    from book_agent.core.config import get_settings

    settings = get_settings()
    if not settings.run_executor_enabled:
        executor = DisabledRunExecutor()
        app.state.document_run_executor = executor
        return executor
    ensure_database_state = getattr(app.state, "ensure_database_state", None)
    if callable(ensure_database_state):
        ensure_database_state()
    resolver = getattr(app.state, "resolve_translation_worker", None)

    executor = DocumentRunExecutor(
        session_factory=app.state.session_factory,
        export_root=app.state.export_root,
        translation_worker=getattr(app.state, "translation_worker", None),
        translation_worker_resolver=resolver if callable(resolver) else None,
        translation_max_output_repairs=settings.translation_max_output_repairs,
        run_ownership_ttl_seconds=settings.run_ownership_ttl_seconds,
    )
    executor.start()
    app.state.document_run_executor = executor
    return executor


def executor_instance_id() -> str:
    """host:pid:random, so a lease or run owner can be traced to a machine and process."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"


class DisabledRunExecutor:
    """Stands in on API-only replicas (``run_executor_enabled=false``): runs are executed elsewhere."""

    instance_id = None

    def start(self) -> None:
        return None

    def stop(self, *, work_timeout_seconds: float = 30.0) -> bool:
        return True

    def wake(self, run_id: str | None = None) -> None:
        return None


class DocumentRunExecutor:
    def __init__(
        self,
        *,
        session_factory: sessionmaker,
        export_root: str | Path,
        translation_worker: TranslationWorker | None,
        translation_worker_resolver: Callable[[], TranslationWorker] | None = None,
        agent_model_resolver: Callable[[TranslationWorker], Any] | None = None,
        poll_interval_seconds: float = 1.0,
        state_reconciler_interval_seconds: float = 30.0,
        lease_seconds: int = 120,
        review_lease_seconds: int = 1800,
        heartbeat_interval_seconds: int = 15,
        default_max_auto_followup_attempts: int = 2,
        default_max_blocker_repair_rounds: int = 10,
        default_max_parallel_workers: int = 8,
        translation_max_output_repairs: int = 1,
        run_ownership_ttl_seconds: float = 30.0,
        instance_id: str | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.instance_id = instance_id or executor_instance_id()
        # A run loop belongs to one instance at a time; the supervisor renews
        # ownership every poll, so another instance takes a run over only when
        # this one has stopped renewing for this long.
        self.run_ownership_ttl_seconds = max(float(poll_interval_seconds) * 3, float(run_ownership_ttl_seconds))
        self.export_root = str(Path(export_root).resolve())
        self.translation_worker = translation_worker
        self.translation_max_output_repairs = max(0, int(translation_max_output_repairs))
        # Resolved per workflow service so provider swaps and late app-state
        # initialization are picked up; a fixed worker is used only when no
        # resolver is supplied.
        self.translation_worker_resolver = translation_worker_resolver
        # Builds the agent model from the resolved translation worker; tests
        # inject scripted models here.
        self.agent_model_resolver = agent_model_resolver or agent_model_for_worker
        self.poll_interval_seconds = poll_interval_seconds
        self.state_reconciler_interval_seconds = max(
            0.0, float(state_reconciler_interval_seconds)
        )
        self._state_reconciler_last_at_by_run: dict[str, float] = {}
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

    def _maybe_reconcile_state(self, run_id: str) -> None:
        """Throttled read-only drift scan over stage cache vs physical state.

        The Reconciler never mutates rows — it only appends a
        ``stage_transitions`` audit row per drift finding. Findings are
        forensic signal; the run-loop's own gates keep bad states from
        advancing. This exists to detect writes that bypass the logger,
        so a postmortem can bisect when a cache-vs-physics lie started.
        """
        interval = self.state_reconciler_interval_seconds
        if interval <= 0:
            return
        now = time.monotonic()
        last = self._state_reconciler_last_at_by_run.get(run_id)
        if last is not None and (now - last) < interval:
            return
        self._state_reconciler_last_at_by_run[run_id] = now
        try:
            with session_scope(self.session_factory) as session:
                Reconciler(session).check_and_audit(run_id)
        except Exception:
            logger.warning("State reconciler scan failed for run %s", run_id, exc_info=True)

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

    def stop(self, *, work_timeout_seconds: float = 30.0) -> bool:
        """Stop scheduling and wait for threads; True when every thread has exited.

        Work threads may be inside an LLM call that cannot be interrupted; they
        finish (and record their result under their lease) after the timeout,
        so callers must not dispose the database engine unless this returns True.
        """
        self._stop_event.set()
        self._wake_event.set()
        # Join tier by tier and re-read the registry after each tier: the
        # supervisor may still start run threads, and run threads work threads,
        # until they observe the stop event.
        with self._lock:
            supervisor = self._supervisor_thread
        if supervisor is not None:
            supervisor.join(timeout=5)
        with self._lock:
            run_threads = list(self._active_run_threads.values())
        for thread in run_threads:
            thread.join(timeout=5)
        with self._lock:
            work_threads = [
                thread
                for thread_map in self._active_work_threads.values()
                for thread in thread_map.values()
            ]
        for thread in work_threads:
            thread.join(timeout=work_timeout_seconds)
            if thread.is_alive():
                logger.warning("Work thread %s still running after stop", thread.name)
        all_stopped = not any(
            thread is not None and thread.is_alive() for thread in [supervisor, *run_threads, *work_threads]
        )
        # Hand the runs to other instances now instead of after the ownership TTL;
        # work items still leased by lingering work threads stay protected by their leases.
        self._release_run_ownership()
        with self._lock:
            self._active_run_threads = {}
            self._active_work_threads = {}
            self._supervisor_thread = None
        return all_stopped

    def wake(self, run_id: str | None = None) -> None:
        self._wake_event.set()
        if run_id is None:
            return
        with self._lock:
            thread = self._active_run_threads.get(run_id)
        if thread is not None and not thread.is_alive():
            with self._lock:
                self._active_run_threads.pop(run_id, None)

    def _current_translation_worker(self) -> TranslationWorker | None:
        if self.translation_worker_resolver is not None:
            return self.translation_worker_resolver()
        return self.translation_worker

    def _workflow_service(self, session) -> DocumentWorkflowService:
        return DocumentWorkflowService(
            session,
            export_root=self.export_root,
            translation_worker=self._current_translation_worker(),
            translation_max_output_repairs=self.translation_max_output_repairs,
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
                for run_id in self._acquire_run_ownership(runnable_run_ids):
                    self._ensure_run_thread(run_id)
                self._reclaim_inactive_run_leases()
            except Exception:
                if self._stop_event.is_set():
                    return
                logger.exception("Run supervisor tick failed")
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
            if self._stop_event.is_set():
                return
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
            if self._stop_event.is_set():
                return
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
                        DocumentRun.run_type.in_(sorted(EXECUTABLE_RUN_TYPES)),
                        DocumentRun.status.in_(
                            [DocumentRunStatus.RUNNING, DocumentRunStatus.DRAINING]
                        ),
                    )
                    .order_by(DocumentRun.created_at.asc(), DocumentRun.id.asc())
                ).all()
            )

    def _acquire_run_ownership(self, run_ids: list[str]) -> list[str]:
        """Take or renew ownership of runnable runs; returns the ones this instance owns now."""
        if not run_ids:
            return []
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=self.run_ownership_ttl_seconds)
        owned: list[str] = []
        with session_scope(self.session_factory) as session:
            for run_id in run_ids:
                result = session.execute(
                    update(DocumentRun)
                    .where(
                        DocumentRun.id == run_id,
                        or_(
                            DocumentRun.executor_owner.is_(None),
                            DocumentRun.executor_owner == self.instance_id,
                            DocumentRun.executor_lease_expires_at.is_(None),
                            DocumentRun.executor_lease_expires_at < now,
                        ),
                    )
                    .values(executor_owner=self.instance_id, executor_lease_expires_at=expires_at)
                    .execution_options(synchronize_session=False)
                )
                if result.rowcount == 1:
                    owned.append(run_id)
        return owned

    def _release_run_ownership(self) -> None:
        try:
            with session_scope(self.session_factory) as session:
                session.execute(
                    update(DocumentRun)
                    .where(DocumentRun.executor_owner == self.instance_id)
                    .values(executor_owner=None, executor_lease_expires_at=None)
                    .execution_options(synchronize_session=False)
                )
        except Exception:
            logger.warning("Could not release run ownership for %s", self.instance_id, exc_info=True)

    def _run_loop(self, run_id: str) -> None:
        while not self._stop_event.is_set():
            if not self._acquire_run_ownership([run_id]):
                # Another instance owns the run (this one stopped renewing in time).
                return
            with session_scope(self.session_factory) as session:
                run_control = self._run_control_service(session)
                run_summary = run_control.get_run_summary(run_id)
            if run_summary.status not in {"running", "draining"}:
                return

            tick_started = time.monotonic()
            tick_span = ExitStack()
            tick_span.enter_context(tracing.span("executor.tick", **{"book_agent.run_id": run_id}))
            try:
                self._maybe_reconcile_state(run_id)
                self._reclaim_expired_leases(run_id)
                if self._enforce_budget_guardrails(run_id):
                    return
                plan = plan_for_run(run_summary.run_type, run_summary.status_detail_json)
                if any(self._process_agent_stage(run_id, stage, plan) for stage in plan.agent_stages):
                    continue
                if plan.includes("translate") and self._process_translate_stage(run_id, plan):
                    continue
                if plan.includes("review") and self._process_review_stage(run_id, plan):
                    continue
                if any(
                    self._process_export_stage(run_id, export_type=ExportType(stage), plan=plan)
                    for stage in plan.export_stages
                ):
                    continue
                with session_scope(self.session_factory) as session:
                    execution = self._run_execution_service(session)
                    summary = execution.reconcile_run_terminal_state(run_id=run_id)
                self._sync_pipeline_status(run_id, summary.status)
                if summary.status in {
                    "succeeded",
                    "succeeded_with_warnings",
                    "failed",
                    "paused",
                    "cancelled",
                }:
                    return
            except (IntegrityError, OperationalError):
                # Concurrent seeding lost a unique-index race or the database hiccuped;
                # the next tick re-reads state instead of failing the whole run.
                logger.warning("Run loop tick for %s hit a transient database error", run_id, exc_info=True)
            except Exception as exc:
                logger.exception("Run loop for %s failed with an unhandled exception", run_id)
                tracing.record_error(exc)
                self._fail_run(run_id, stop_reason="runner.unhandled_exception", exc=exc)
                return
            finally:
                metrics.EXECUTOR_TICK.observe(time.monotonic() - tick_started)
                tick_span.close()

            self._wake_event.wait(timeout=self.poll_interval_seconds)
            self._wake_event.clear()

    def _plan_for(self, run: DocumentRun) -> RunPlan:
        return plan_for_run(run.run_type, run.status_detail_json)

    @staticmethod
    def _next_stage_label(plan: RunPlan, stage: str) -> str:
        if stage in plan.stages:
            index = plan.stages.index(stage)
            if index + 1 < len(plan.stages):
                return plan.stages[index + 1]
        return "completed"

    def _enforce_budget_guardrails(self, run_id: str) -> bool:
        """Pause or fail the run when a configured budget is exhausted.

        Returns True when the run was stopped and the loop should exit.
        """
        with session_scope(self.session_factory) as session:
            result = self._run_execution_service(session).enforce_budget_guardrails(run_id=run_id)
        if not result.budget_exceeded:
            return False
        self._sync_pipeline_status(run_id, result.run_summary.status)
        return True

    def _reclaim_inactive_run_leases(self) -> list[str]:
        """Sweep expired leases of runs that have no run loop (paused, cancelled, failed).

        Their work threads may still be inside a provider call; once the lease
        has expired the item is put back so a later resume or retry does not
        race the old thread on the same packet.
        """
        with session_scope(self.session_factory) as session:
            run_ids = self._run_execution_service(session).run_ids_with_expired_leases_outside_loops()
        for run_id in run_ids:
            self._reclaim_expired_leases(run_id)
        return run_ids

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
        if reclaimed.expired_lease_count:
            metrics.LEASES_RECLAIMED.inc(reclaimed.expired_lease_count)
        return reclaimed.expired_lease_count > 0

    def _process_translate_stage(self, run_id: str, plan: RunPlan | None = None) -> bool:
        with session_scope(self.session_factory) as session:
            repository = RunControlRepository(session)
            execution = self._run_execution_service(session)
            run = repository.get_run(run_id)
            plan = plan or self._plan_for(run)
            packet_scope = plan.packet_ids
            next_stage = self._next_stage_label(plan, "translate")
            document_id = run.document_id
            if plan.agent_stages and not StageGateKeeper(session).can_start(
                run_id, document_id, "translate", plan_stages=plan.stages
            ):
                return False
            if self._downstream_stage_in_flight(session, run_id):
                # A review or export thread owns the packets it re-opens for
                # followup translation; seeding them here as well would translate
                # the same packet twice.
                return False
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
                packet_scope=packet_scope,
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
                        "total_packet_count": len(self._list_all_packet_ids(session, document_id, packet_scope)),
                        "pending_packet_count": len(
                            self._list_pending_packet_ids(session, document_id, packet_scope)
                        ),
                    },
                    current_stage="translate",
                    session=session,
                )
            if not active_translate_items:
                packet_ids = self._list_pending_packet_ids(session, document_id, packet_scope)
                current_stage = "translate" if packet_ids else next_stage
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
                        "total_packet_count": len(self._list_all_packet_ids(session, document_id, packet_scope)),
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
            self._update_pipeline_stage(run_id, "translate", status="succeeded", current_stage=next_stage)
        return False

    def _seed_translate_frontier_work_items(
        self,
        *,
        session: Session,
        execution: RunExecutionService,
        run_id: str,
        document_id: str,
        translate_items: list[WorkItem],
        packet_scope: frozenset[str] | None = None,
    ) -> list[str]:
        # DECIDE (read-only planner) → EXECUTE (single-writer seed).
        # Keep the two halves textually adjacent so any future tweak
        # that adds a read can't accidentally sneak a write into the
        # DECIDE half.
        plan = self._plan_translate_frontier(
            session=session,
            run_id=run_id,
            document_id=document_id,
            translate_items=translate_items,
            packet_scope=packet_scope,
        )
        if plan.is_empty:
            return []
        execution.seed_translate_work_items(
            run_id=run_id,
            packet_ids=plan.packet_ids,
            input_version_bundle_by_packet_id=self._translate_input_versions(session, plan.packet_ids),
        )
        return plan.packet_ids

    def _process_agent_stage(self, run_id: str, stage_key: str, plan: RunPlan | None = None) -> bool:
        """Drive an agent stage: one AGENT work item per turn attempt.

        The turn is the durable state. A new work item is seeded when there is
        no turn yet, or when the latest turn is RUNNING again after an approval
        or budget decision and nobody is executing it. A turn waiting for a
        decision leaves the stage RUNNING without seeding anything.
        """
        agent_kind = AGENT_STAGES[stage_key]
        with session_scope(self.session_factory) as session:
            repository = RunControlRepository(session)
            execution = self._run_execution_service(session)
            run = repository.get_run(run_id)
            plan = plan or self._plan_for(run)
            if not StageGateKeeper(session).can_start(run_id, run.document_id, stage_key, plan_stages=plan.stages):
                return False
            items = [
                item
                for item in self._list_stage_items(session, run_id, WorkItemStage.AGENT)
                if (item.input_version_bundle_json or {}).get("agent_kind") == agent_kind
            ]
            if any(item.status == WorkItemStatus.TERMINAL_FAILED for item in items):
                return False
            if any(item.status in {WorkItemStatus.LEASED, WorkItemStatus.RUNNING} for item in items):
                return False
            turn = AgentLedgerRepository(session).latest_turn(document_id=run.document_id, agent_kind=agent_kind, run_id=run_id)
            claimable = [item for item in items if item.status in {WorkItemStatus.PENDING, WorkItemStatus.RETRYABLE_FAILED}]
            if not claimable:
                if turn is not None and turn.status in {AgentTurnStatus.AWAITING_APPROVAL, AgentTurnStatus.PAUSED}:
                    self._update_pipeline_stage(
                        run_id, stage_key, status="running",
                        extra={"turn_id": turn.id, "turn_status": turn.status.value, "stop_reason": turn.stop_reason},
                        current_stage=stage_key, session=session,
                    )
                    return False
                if turn is not None and turn.status in {AgentTurnStatus.SUCCEEDED, AgentTurnStatus.CANCELLED}:
                    if turn.status == AgentTurnStatus.SUCCEEDED:
                        self._update_pipeline_stage(
                            run_id, stage_key, status="succeeded",
                            extra={"turn_id": turn.id}, current_stage=self._next_stage_label(plan, stage_key), session=session,
                        )
                    return False
                if turn is not None and turn.status == AgentTurnStatus.FAILED:
                    return False
                # No turn yet, or a turn resumed after a decision: seed an attempt.
                scope_id = stable_id("document-run-agent", run_id, agent_kind, str(len(items) + 1))
                execution.seed_work_items(
                    run_id=run_id,
                    stage=WorkItemStage.AGENT,
                    scope_type=WorkItemScopeType.DOCUMENT,
                    scope_ids=[scope_id],
                    input_version_bundle_by_scope_id={
                        scope_id: {
                            "document_id": run.document_id,
                            "agent_kind": agent_kind,
                            "stage": stage_key,
                            "terminology_mode": plan.terminology_mode,
                            "model_review_mode": plan.model_review_mode,
                            "structure_review_mode": plan.structure_review_mode,
                            "export_review_mode": plan.export_review_mode,
                            **({"resume_turn_id": turn.id} if turn is not None else {}),
                        }
                    },
                )
                self._update_pipeline_stage(run_id, stage_key, status="running", current_stage=stage_key, session=session)
                return True
            self._update_pipeline_stage(run_id, stage_key, status="running", current_stage=stage_key, session=session)
            claimed = execution.claim_work_item_by_id(
                work_item_id=claimable[0].id,
                worker_name=f"app.run.agent.{agent_kind}",
                worker_instance_id=f"app.agent:{self.instance_id}:{uuid4()}",
                lease_seconds=self.review_lease_seconds,
            )
        if claimed is None:
            return False
        self._ensure_work_thread(
            run_id=run_id,
            work_item_id=claimed.work_item_id,
            thread_name=f"book-agent-agent-{agent_kind}-{claimed.work_item_id}",
            target=lambda claimed=claimed: self._execute_agent_work_item(run_id, claimed, stage_key, plan),
        )
        return True

    def _execute_agent_work_item(self, run_id: str, claimed: ClaimedRunWorkItem, stage_key: str, plan: RunPlan | None) -> None:
        input_bundle = self._load_work_item_input_bundle(claimed.work_item_id)
        agent_kind = str(input_bundle.get("agent_kind") or AGENT_STAGES.get(stage_key, stage_key))
        next_stage = self._next_stage_label(plan, stage_key) if plan is not None else "translate"

        def _lease_check(session) -> None:
            self._run_execution_service(session).assert_lease_held(lease_token=claimed.lease_token)

        def _run_agent() -> dict[str, Any]:
            worker = self._current_translation_worker()
            model = self.agent_model_resolver(worker)
            registry, policy = self._agent_tools(agent_kind)
            with session_scope(self.session_factory) as session:
                self._run_execution_service(session).assert_lease_held(lease_token=claimed.lease_token)
                resume_turn_id = input_bundle.get("resume_turn_id")
                if resume_turn_id:
                    turn_id = str(resume_turn_id)
                else:
                    turn_id = self._start_agent_turn(
                        session,
                        agent_kind=agent_kind,
                        document_id=str(input_bundle.get("document_id") or ""),
                        run_id=run_id,
                        work_item_id=claimed.work_item_id,
                        worker=worker,
                        input_bundle=input_bundle,
                    )
            runner = AgentTurnRunner(
                session_factory=self.session_factory,
                model=model,
                registry=registry,
                policy=policy,
                lease_check=_lease_check,
                tool_extras={"workflow_factory": self._workflow_service},
            )
            try:
                outcome = runner.run(turn_id)
            except LeaseLostError:
                raise
            except Exception as exc:
                runner.fail(turn_id, error=exc)
                raise
            if outcome.status == AgentTurnStatus.FAILED:
                raise RuntimeError(f"agent turn {turn_id} failed: {outcome.stop_reason}")
            if outcome.status == AgentTurnStatus.SUCCEEDED and agent_kind == RepairAgent_KIND:
                with session_scope(self.session_factory) as session:
                    left = remaining_blockers(session, str(input_bundle.get("document_id") or ""))
                if left:
                    raise RuntimeError(
                        f"Document still has unresolved blocking review issues after the repair agent: {left} remaining "
                        f"(turn {turn_id})."
                    )
            return {
                "document_id": str(input_bundle.get("document_id") or ""),
                "agent_kind": agent_kind,
                "turn_id": turn_id,
                "turn_status": outcome.status.value,
                "stop_reason": outcome.stop_reason,
                "usage": outcome.usage,
            }

        def _on_success(payload: dict[str, Any], lease_token: str) -> None:
            with session_scope(self.session_factory) as session:
                execution = self._run_execution_service(session)
                execution.complete_work_item_success(
                    lease_token=lease_token,
                    output_artifact_refs_json={"turn_id": payload["turn_id"], "agent_kind": agent_kind},
                    payload_json=payload,
                )
                succeeded = payload.get("turn_status") == AgentTurnStatus.SUCCEEDED.value
                self._update_pipeline_stage(
                    run_id,
                    stage_key,
                    status="succeeded" if succeeded else "running",
                    extra=payload,
                    current_stage=next_stage if succeeded else stage_key,
                    session=session,
                )

        self._execute_claimed_work_item(
            run_id=run_id,
            claimed=claimed,
            worker_fn=_run_agent,
            on_success=_on_success,
            stage_key=stage_key,
            lease_seconds=self.review_lease_seconds,
        )

    def _agent_tools(self, agent_kind: str):
        if agent_kind == TerminologyAgent_KIND:
            return TerminologyAgent.registry(), TerminologyAgent.policy()
        if agent_kind == ReviewerAgent_KIND:
            return ReviewerAgent.registry(), ReviewerAgent.policy()
        if agent_kind == RepairAgent_KIND:
            return RepairAgent.registry(), RepairAgent.policy()
        if agent_kind == StructureAgent_KIND:
            return StructureAgent.registry(), StructureAgent.policy()
        if agent_kind == ExportReviewAgent_KIND:
            return ExportReviewAgent.registry(), ExportReviewAgent.policy()
        raise RuntimeError(f"unknown agent kind: {agent_kind}")

    def _start_agent_turn(
        self,
        session,
        *,
        agent_kind: str,
        document_id: str,
        run_id: str,
        work_item_id: str,
        worker,
        input_bundle: dict[str, Any],
    ) -> str:
        if agent_kind == TerminologyAgent_KIND:
            client = getattr(worker, "client", None)
            extraction_client = client if client is not None and hasattr(client, "generate_structured_object") else None
            model_name = worker.metadata().model_name if worker is not None else "echo-worker"
            seed = TerminologyAgent(session).start_turn(
                document_id=document_id,
                model_name=model_name,
                extraction_client=extraction_client,
                mode=str(input_bundle.get("terminology_mode") or "sampled"),
                run_id=run_id,
                work_item_id=work_item_id,
            )
            return seed.turn_id
        if agent_kind == ReviewerAgent_KIND:
            model_name = worker.metadata().model_name if worker is not None else "echo-worker"
            seed = ReviewerAgent(session).start_turn(
                document_id=document_id,
                model_name=model_name,
                mode=str(input_bundle.get("model_review_mode") or "sampled"),
                run_id=run_id,
                work_item_id=work_item_id,
            )
            return seed.turn_id
        if agent_kind == ExportReviewAgent_KIND:
            model_name = worker.metadata().model_name if worker is not None else "echo-worker"
            seed = ExportReviewAgent(session).start_turn(
                document_id=document_id,
                model_name=model_name,
                mode=str(input_bundle.get("export_review_mode") or "sampled"),
                run_id=run_id,
                work_item_id=work_item_id,
            )
            return seed.turn_id
        if agent_kind == StructureAgent_KIND:
            model_name = worker.metadata().model_name if worker is not None else "echo-worker"
            seed = StructureAgent(session).start_turn(
                document_id=document_id,
                model_name=model_name,
                mode=str(input_bundle.get("structure_review_mode") or "sampled"),
                run_id=run_id,
                work_item_id=work_item_id,
            )
            return seed.turn_id
        if agent_kind == RepairAgent_KIND:
            model_name = worker.metadata().model_name if worker is not None else "echo-worker"
            seed = RepairAgent(session).start_turn(
                document_id=document_id, model_name=model_name, run_id=run_id, work_item_id=work_item_id
            )
            return seed.turn_id
        raise RuntimeError(f"unknown agent kind: {agent_kind}")

    def _process_review_stage(self, run_id: str, plan: RunPlan | None = None) -> bool:
        with session_scope(self.session_factory) as session:
            repository = RunControlRepository(session)
            execution = self._run_execution_service(session)
            run = repository.get_run(run_id)
            plan = plan or self._plan_for(run)
            if not StageGateKeeper(session).can_start(
                run_id, run.document_id, "review", plan_stages=plan.stages
            ):
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
                self._update_pipeline_stage(run_id, "review", status="running", current_stage="review", session=session)
                return True
            if any(item.status == WorkItemStatus.TERMINAL_FAILED for item in review_items):
                return False
            if any(item.status in {WorkItemStatus.PENDING, WorkItemStatus.RETRYABLE_FAILED} for item in review_items):
                self._update_pipeline_stage(run_id, "review", status="running", current_stage="review", session=session)
                claimed = execution.claim_next_work_item(
                    run_id=run_id,
                    stage=WorkItemStage.REVIEW,
                    worker_name="app.run.review",
                    worker_instance_id=f"app.review:{self.instance_id}:{uuid4()}",
                    lease_seconds=self.lease_seconds,
                )
            else:
                claimed = None

        if claimed is not None:
            # Review can take many minutes; run it off the run thread so lease
            # reclaim, budgets and cancellation keep ticking.
            self._ensure_work_thread(
                run_id=run_id,
                work_item_id=claimed.work_item_id,
                thread_name=f"book-agent-review-{claimed.work_item_id}",
                target=lambda claimed=claimed: self._execute_review_work_item(run_id, claimed, plan),
            )
            return True

        if review_items and all(item.status == WorkItemStatus.SUCCEEDED for item in review_items):
            self._update_pipeline_stage(
                run_id,
                "review",
                status="succeeded",
                current_stage=self._next_stage_label(plan, "review"),
            )
        return False

    def _process_export_stage(
        self,
        run_id: str,
        *,
        export_type: ExportType,
        plan: RunPlan | None = None,
    ) -> bool:
        pipeline_key = export_type.value
        with session_scope(self.session_factory) as session:
            repository = RunControlRepository(session)
            execution = self._run_execution_service(session)
            run = repository.get_run(run_id)
            plan = plan or self._plan_for(run)
            if not StageGateKeeper(session).can_start(
                run_id, run.document_id, pipeline_key, plan_stages=plan.stages
            ):
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
                self._update_pipeline_stage(run_id, pipeline_key, status="running", current_stage=pipeline_key, session=session)
                return True
            if any(item.status == WorkItemStatus.TERMINAL_FAILED for item in export_items):
                return False
            if any(item.status in {WorkItemStatus.PENDING, WorkItemStatus.RETRYABLE_FAILED} for item in export_items):
                self._update_pipeline_stage(run_id, pipeline_key, status="running", current_stage=pipeline_key, session=session)
                claimed = execution.claim_next_work_item(
                    run_id=run_id,
                    stage=WorkItemStage.EXPORT,
                    worker_name=f"app.run.export.{export_type.value}",
                    worker_instance_id=f"app.export.{export_type.value}:{self.instance_id}:{uuid4()}",
                    lease_seconds=self.lease_seconds,
                )
            else:
                claimed = None

        if claimed is not None:
            self._ensure_work_thread(
                run_id=run_id,
                work_item_id=claimed.work_item_id,
                thread_name=f"book-agent-export-{export_type.value}-{claimed.work_item_id}",
                target=lambda claimed=claimed: self._execute_export_work_item(
                    run_id,
                    claimed,
                    export_type=export_type,
                    plan=plan,
                ),
            )
            return True

        if export_items and all(item.status == WorkItemStatus.SUCCEEDED for item in export_items):
            self._update_pipeline_stage(
                run_id,
                pipeline_key,
                status="succeeded",
                current_stage=self._next_stage_label(plan, pipeline_key),
            )
        return False

    def _execute_translate_work_item(self, run_id: str, claimed: ClaimedRunWorkItem) -> None:
        self._execute_claimed_work_item(
            run_id=run_id,
            claimed=claimed,
            worker_fn=lambda: self._translate_single_packet(
                claimed.scope_id,
                run_id=run_id,
                lease_token=claimed.lease_token,
            ),
            on_success=lambda payload, lease_token: self._complete_translate_success(
                payload, lease_token, run_id=run_id
            ),
            lease_seconds=self.lease_seconds,
        )

    def _execute_review_work_item(
        self,
        run_id: str,
        claimed: ClaimedRunWorkItem,
        plan: RunPlan | None = None,
    ) -> None:
        input_bundle = self._load_work_item_input_bundle(claimed.work_item_id)
        repairs_blockers = plan.review_repairs_blockers if plan is not None else True
        # With the Repair Agent planned, blockers rule repair leaves are its job.
        hands_off_to_repair_agent = plan is not None and plan.includes("repair")
        next_stage = self._next_stage_label(plan, "review") if plan is not None else "bilingual_html"

        def _run_review() -> dict[str, Any]:
            payload: dict[str, Any]
            remaining_blocking_issue_count = 0
            stop_reason = "unknown"
            with session_scope(self.session_factory) as session:
                workflow = self._workflow_service(session)
                document_id = str(input_bundle.get("document_id") or claimed.scope_id)
                if not repairs_blockers:
                    # Standalone review run: record issues, like the synchronous review.
                    result = workflow.review_document(document_id)
                    payload = {
                        "document_id": document_id,
                        "total_issue_count": result.total_issue_count,
                        "total_action_count": result.total_action_count,
                        "chapter_count": len(result.chapter_results),
                        "examined_chapter_count": result.examined_chapter_count,
                        "skipped_chapter_count": result.skipped_chapter_count,
                        "total_chapter_count": result.total_chapter_count,
                    }
                    self._run_execution_service(session).assert_lease_held(lease_token=claimed.lease_token)
                    return payload
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
                    "examined_chapter_count": result.examined_chapter_count,
                    "skipped_chapter_count": result.skipped_chapter_count,
                    "total_chapter_count": result.total_chapter_count,
                    "skipped_chapters": [
                        {
                            "chapter_id": s.chapter_id,
                            "reason": s.reason,
                            "pending_packet_count": s.pending_packet_count,
                            "failed_packet_count": s.failed_packet_count,
                        }
                        for s in result.skipped_chapters
                    ],
                    "auto_followup_requested": initial_result.auto_followup_requested,
                    "auto_followup_applied": initial_result.auto_followup_applied,
                    "auto_followup_attempt_count": initial_result.auto_followup_attempt_count,
                    "blocker_repair_requested": repair_result.requested,
                    "blocker_repair_applied": repair_result.applied,
                    "blocker_repair_round_count": repair_result.round_count,
                    "blocker_repair_round_limit": repair_result.round_limit,
                    "blocker_repair_execution_count": len(repair_result.executions),
                    "remaining_blocking_issue_count": remaining_blocking_issue_count,
                    "handed_to_repair_agent": bool(remaining_blocking_issue_count and hands_off_to_repair_agent),
                }
                self._run_execution_service(session).assert_lease_held(lease_token=claimed.lease_token)
            if remaining_blocking_issue_count > 0 and not hands_off_to_repair_agent:
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
                # Skip visibility (Phase 3): if any chapters were excluded from
                # review because their translate packets weren't TRANSLATED, mark
                # the stage as ``partial`` so the UI does not claim "done" for
                # content that was never examined.
                stage_status = (
                    "partial" if int(payload.get("skipped_chapter_count") or 0) > 0
                    else "succeeded"
                )
                self._update_pipeline_stage(
                    run_id,
                    "review",
                    status=stage_status,
                    extra=payload,
                    current_stage=next_stage,
                    session=session,
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
        plan: RunPlan | None = None,
    ) -> None:
        pipeline_key = export_type.value
        input_bundle = self._load_work_item_input_bundle(claimed.work_item_id)
        document_id = str(input_bundle.get("document_id") or "")
        auto_followup = plan.auto_followup_on_export_gate if plan is not None else True
        next_stage = (
            self._next_stage_label(plan, pipeline_key)
            if plan is not None
            else ("merged_html" if export_type == ExportType.BILINGUAL_HTML else "completed")
        )

        def _run_export() -> dict[str, Any]:
            with session_scope(self.session_factory) as session:
                workflow = self._workflow_service(session)
                max_attempts = (
                    plan.max_auto_followup_attempts
                    if plan is not None and plan.max_auto_followup_attempts is not None
                    else self._max_auto_followup_attempts(session, run_id)
                )
                try:
                    result = workflow.export_document(
                        document_id,
                        export_type,
                        auto_execute_followup_on_gate=auto_followup,
                        max_auto_followup_attempts=max_attempts,
                    )
                except ExportGateError:
                    # Keep the review issues and followup attempts the gate
                    # recorded; the work item still fails with the gate detail.
                    self._run_execution_service(session).assert_lease_held(lease_token=claimed.lease_token)
                    session.commit()
                    raise
                self._run_execution_service(session).assert_lease_held(lease_token=claimed.lease_token)
            qa_summary = self._audit_export(document_id, export_type, result)
            return {
                "document_id": document_id,
                "export_type": export_type.value,
                "file_path": result.file_path,
                "manifest_path": result.manifest_path,
                "chapter_export_count": len(result.chapter_results),
                "chapter_export_ids": [chapter.export_id for chapter in result.chapter_results],
                **({"qa": qa_summary} if qa_summary is not None else {}),
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
                    current_stage=next_stage,
                    session=session,
                )

        self._execute_claimed_work_item(
            run_id=run_id,
            claimed=claimed,
            worker_fn=_run_export,
            on_success=_on_success,
            stage_key=pipeline_key,
            lease_seconds=self.lease_seconds,
        )

    def _audit_export(self, document_id: str, export_type: ExportType, result) -> dict[str, Any] | None:
        """Export QA after a successful export; a QA problem is recorded, never raised."""
        if export_type not in AUDITED_EXPORT_TYPES:
            return None
        artifacts: list[tuple[str | None, Path]] = (
            [(chapter.chapter_id, Path(chapter.file_path)) for chapter in result.chapter_results]
            if result.chapter_results
            else ([(None, Path(result.file_path))] if result.file_path else [])
        )
        try:
            with session_scope(self.session_factory) as session:
                return ExportQaService(session).audit(document_id, export_type, artifacts).to_json()
        except Exception as exc:  # the export already succeeded
            logger.exception("Export QA failed for document %s (%s)", document_id, export_type.value)
            return {"export_type": export_type.value, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}

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
        with bind_run_context(run_id), tracing.span(
            f"work_item.{claimed.stage}",
            **{
                "book_agent.run_id": run_id,
                "book_agent.work_item_id": claimed.work_item_id,
                "book_agent.scope_type": claimed.scope_type,
                "book_agent.scope_id": claimed.scope_id,
                "book_agent.attempt": claimed.attempt,
            },
        ):
            self._execute_claimed_work_item_in_context(
                run_id=run_id,
                claimed=claimed,
                worker_fn=worker_fn,
                on_success=on_success,
                stage_key=stage_key,
                lease_window_seconds=lease_window_seconds,
                stop_event=stop_event,
                heartbeat_thread=heartbeat_thread,
            )

    def _execute_claimed_work_item_in_context(
        self,
        *,
        run_id: str,
        claimed: ClaimedRunWorkItem,
        worker_fn,
        on_success,
        stage_key: str | None,
        lease_window_seconds: int,
        stop_event: threading.Event,
        heartbeat_thread: threading.Thread,
    ) -> None:
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
            self._stop_heartbeat(heartbeat_thread, stop_event)
            on_success(payload, claimed.lease_token)
            metrics.WORK_ITEMS.inc(stage=str(claimed.stage), outcome="succeeded")
            tracing.annotate_current(**{"book_agent.outcome": "succeeded"})
            self.wake(run_id)
        except LeaseLostError:
            metrics.WORK_ITEMS.inc(stage=str(claimed.stage), outcome="lease_lost")
            tracing.annotate_current(**{"book_agent.outcome": "lease_lost"})
            self._stop_heartbeat(heartbeat_thread, stop_event)
            # The lease expired and the work item was reclaimed; its new owner
            # records the outcome, so discard this attempt without touching it.
            logger.warning(
                "Work item %s lost its lease; discarded this attempt's result",
                claimed.work_item_id,
            )
            self.wake(run_id)
        except Exception as exc:
            metrics.WORK_ITEMS.inc(stage=str(claimed.stage), outcome="failed")
            tracing.annotate_current(**{"book_agent.outcome": "failed"})
            tracing.record_error(exc)
            self._stop_heartbeat(heartbeat_thread, stop_event)
            try:
                self._complete_failure(
                    run_id=run_id,
                    claimed=claimed,
                    exc=exc,
                    stage_key=stage_key or claimed.stage,
                )
            except LeaseLostError:
                logger.warning("Work item %s lost its lease before its failure was recorded", claimed.work_item_id)
            except Exception:
                # The lease will expire and the run loop reclaims the item.
                logger.exception(
                    "Recording failure of work item %s failed (original error: %s)",
                    claimed.work_item_id,
                    exc,
                )

    def _stop_heartbeat(self, heartbeat_thread: threading.Thread, stop_event: threading.Event) -> None:
        stop_event.set()
        # The thread is not started yet if starting the work item itself failed.
        if heartbeat_thread.is_alive():
            heartbeat_thread.join(timeout=max(1, self.heartbeat_interval_seconds))

    def _translate_single_packet(
        self,
        packet_id: str,
        *,
        run_id: str | None = None,
        lease_token: str | None = None,
    ) -> dict[str, Any]:
        # Three transactions so no database connection is held open across the
        # LLM call: prepare (commits the call-started event), call the worker
        # outside any session, then persist the result.
        with session_scope(self.session_factory) as session:
            translation_service = self._workflow_service(session).translation_service
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
            prepared = translation_service.prepare_packet(packet_id, run_id=run_id)

        try:
            worker_result = translation_service.call_worker(prepared)
        except Exception as exc:
            with session_scope(self.session_factory) as session:
                self._workflow_service(session).translation_service.record_worker_failure(prepared, exc)
            raise

        with session_scope(self.session_factory) as session:
            artifacts = self._workflow_service(session).translation_service.persist_packet_result(
                prepared,
                worker_result,
                auto_commit_memory=False,
            )
            if lease_token is not None:
                # The LLM call may outlive the lease; only commit results while
                # this worker still owns the work item.
                self._run_execution_service(session).assert_lease_held(lease_token=lease_token)
            translation_run = artifacts.translation_run
            return {
                "packet_id": packet_id,
                "translation_run_id": translation_run.id,
                "token_in": translation_run.token_in or 0,
                "token_out": translation_run.token_out or 0,
                "cost_usd": float(translation_run.cost_usd or 0.0),
                "latency_ms": translation_run.latency_ms or 0,
            }

    def _complete_translate_success(
        self, payload: dict[str, Any], lease_token: str, *, run_id: str | None = None
    ) -> None:
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
            if run_id is not None:
                # Check spend as soon as it is known instead of waiting for
                # the next run-loop tick, so an overrun stops at one packet
                # rather than one packet per parallel worker.
                execution.enforce_budget_guardrails(run_id=run_id)

    def _complete_failure(
        self,
        *,
        run_id: str,
        claimed: ClaimedRunWorkItem,
        exc: Exception,
        stage_key: str,
    ) -> None:
        failure = classify_failure(exc)
        retryable = failure.retryable
        pause_reason = failure.pause_reason
        error_class = exc.__class__.__name__
        error_detail = {
            "message": str(exc),
            "failure_reason": failure.reason,
            "traceback": traceback.format_exc(limit=8),
        }
        if isinstance(exc, ExportGateError):
            error_detail["export_gate"] = exc.to_http_detail()
        elif isinstance(exc, ExportUnavailableError):
            # Not a review block: the renderer, the source file or the export type is the problem.
            error_detail["export_unavailable"] = {"reason": exc.reason}
        with session_scope(self.session_factory) as session:
            execution = self._run_execution_service(session)
            execution.complete_work_item_failure(
                lease_token=claimed.lease_token,
                error_class=error_class,
                error_detail_json=error_detail,
                retryable=retryable,
                pauses_run=pause_reason is not None,
            )
            if (
                claimed.stage == WorkItemStage.TRANSLATE.value
                and claimed.scope_type == WorkItemScopeType.PACKET.value
            ):
                self._update_translate_packet_runtime_state(
                    session,
                    packet_id=claimed.scope_id,
                    substate=(
                        PACKET_RUNTIME_SUBSTATE_RETRYABLE_FAILED
                        if retryable or pause_reason is not None
                        else PACKET_RUNTIME_SUBSTATE_TERMINAL_FAILED
                    ),
                    run_id=run_id,
                    work_item_id=claimed.work_item_id,
                    attempt=claimed.attempt,
                )
            # Same transaction as the work-item failure and the terminal
            # decision, so nobody observes a finished run with a stale stage.
            self._update_pipeline_stage(
                run_id,
                stage_key,
                status=("paused" if pause_reason is not None else ("retryable_failed" if retryable else "failed")),
                extra={
                    "error_class": error_class,
                    "error_message": str(exc),
                    **({"stop_reason": pause_reason} if pause_reason is not None else {}),
                    **({"export_gate": error_detail["export_gate"]} if "export_gate" in error_detail else {}),
                },
                current_stage=stage_key,
                session=session,
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
                worker_instance_id=f"app.translate:{self.instance_id}:{uuid4()}",
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

    def _plan_translate_frontier(
        self,
        *,
        session: Session,
        run_id: str,
        document_id: str,
        translate_items: list[WorkItem] | None = None,
        packet_scope: frozenset[str] | None = None,
    ) -> TranslateFrontierPlan:
        """DECIDE-phase planner: pick next TRANSLATE work_item targets.

        Pure read-only. Must not call session.add / session.flush /
        session.commit. The caller (seed_translate_frontier_work_items)
        owns the EXECUTE phase and is the single writer — this
        separation is what keeps double-seeds and status races out
        of the main loop. See P0.1 spec.

        Invariants enforced here:
        - At most one seeded packet per chapter ("one-packet-per-chapter
          frontier"): any chapter with a live TRANSLATE work_item
          (pending/leased/running/retryable_failed) is excluded.
        - No duplicate seed: packets already represented by a work_item
          in any status are excluded.
        - Deterministic ordering: results sorted by (packet_ordinal,
          packet_id) so two concurrent planners observing the same DB
          snapshot return the same list.
        """
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
        blocked_chapter_ids = frozenset(
            self._translate_item_chapter_id_map(session, chapter_blocking_items).values()
        )
        represented_packet_ids = frozenset(str(item.scope_id) for item in stage_items)
        candidate_packet_ids = self._list_pending_packet_ids(session, document_id, packet_scope)
        if not candidate_packet_ids:
            return TranslateFrontierPlan(
                packet_ids=[],
                blocked_chapter_ids=blocked_chapter_ids,
                represented_packet_ids=represented_packet_ids,
            )

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

        packet_ids = sorted(
            frontier_by_chapter.values(),
            key=lambda packet_id: self._packet_lane_sort_key(
                packet_id,
                candidate_metadata.get(packet_id, {}),
            ),
        )
        return TranslateFrontierPlan(
            packet_ids=packet_ids,
            blocked_chapter_ids=blocked_chapter_ids,
            represented_packet_ids=represented_packet_ids,
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
                    logger.warning("Lease %s is no longer active; stopping heartbeat", lease_token)
                    return
            except Exception:
                logger.warning("Heartbeat failed for lease %s", lease_token, exc_info=True)

    def _list_all_packet_ids(
        self,
        session,
        document_id: str,
        packet_scope: frozenset[str] | None = None,
    ) -> list[str]:
        if packet_scope is not None:
            return [packet_id for packet_id in self._list_all_packet_ids(session, document_id) if packet_id in packet_scope]
        return list(
            session.scalars(
                select(TranslationPacket.id)
                .join(Chapter, Chapter.id == TranslationPacket.chapter_id)
                .where(Chapter.document_id == document_id)
                .order_by(TranslationPacket.created_at.asc(), TranslationPacket.id.asc())
            ).all()
        )

    def _list_pending_packet_ids(
        self,
        session,
        document_id: str,
        packet_scope: frozenset[str] | None = None,
    ) -> list[str]:
        if packet_scope is not None:
            return [
                packet_id for packet_id in self._list_pending_packet_ids(session, document_id) if packet_id in packet_scope
            ]
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

    def _downstream_stage_in_flight(self, session, run_id: str) -> bool:
        return bool(
            session.scalar(
                select(func.count(WorkItem.id)).where(
                    WorkItem.run_id == run_id,
                    # Agent turns (the Repair Agent) re-open and retranslate packets too.
                    WorkItem.stage.in_([WorkItemStage.REVIEW, WorkItemStage.EXPORT, WorkItemStage.AGENT]),
                    WorkItem.status.in_([WorkItemStatus.LEASED, WorkItemStatus.RUNNING]),
                )
            )
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
        run = repository.get_run_for_update(run_id)
        detail = dict(run.status_detail_json or {})
        pipeline = dict(detail.get("pipeline") or {})
        stages = dict(read_cached_stages(pipeline) or {})
        stage_detail = dict(stages.get(stage_key) or {})
        previous_status = stage_detail.get("status", "unknown")
        stage_detail["status"] = status
        stage_detail["updated_at"] = _utcnow().isoformat()
        if extra:
            stage_detail.update(extra)
        stages[stage_key] = stage_detail
        write_cached_stages(pipeline, stages)
        if current_stage is not None:
            pipeline["current_stage"] = current_stage
        detail["pipeline"] = pipeline
        run.status_detail_json = detail
        repository.save_run(run)
        if previous_status != status:
            StageTransitionLogger(session).record(
                run_id=run_id,
                stage=stage_key,
                from_status=str(previous_status),
                to_status=str(status),
                triggered_by="main_loop",
                reason="pipeline_stage_cache_update",
            )

    def _sync_pipeline_status(self, run_id: str, run_status: str) -> None:
        if run_status in {"succeeded", "succeeded_with_warnings"}:
            with session_scope(self.session_factory) as session:
                self._finalize_stage_snapshots_on_success(session, run_id)
                self._do_update_pipeline_stage(
                    session, run_id, "pipeline", run_status, None, "completed"
                )
            return
        if run_status in {"failed", "paused", "cancelled"}:
            self._update_pipeline_stage(run_id, "pipeline", status=run_status, current_stage=run_status)

    def _finalize_stage_snapshots_on_success(self, session: Session, run_id: str) -> None:
        # Refresh the pipeline.stages cache to mirror derived truth.
        #
        # The prior implementation force-set every non-terminal stage to
        # "succeeded" when the run finished, which printed lies to the UI
        # whenever the executor's terminal decision itself was wrong
        # (e.g. translate physically still RUNNING while the run was
        # marked SUCCEEDED by a stale work-item count). We now consult
        # :class:`StageStatusCalculator` and only persist the derived
        # status — the cache becomes a snapshot of truth, never a
        # fabrication.
        repository = RunControlRepository(session)
        run = repository.get_run_for_update(run_id)
        detail = dict(run.status_detail_json or {})
        pipeline = dict(detail.get("pipeline") or {})
        stages = dict(read_cached_stages(pipeline) or {})
        if not stages:
            return
        calculator = StageStatusCalculator(session)
        packet_scope = translate_packet_scope(run.run_type, run.status_detail_json)
        now = _utcnow().isoformat()
        changed = False
        for stage_key, stage_detail in list(stages.items()):
            if stage_key == "pipeline":
                continue
            if not isinstance(stage_detail, dict):
                continue
            try:
                derived = calculator.stage_status(
                    run_id,
                    run.document_id,
                    stage_key,
                    packet_ids=packet_scope if stage_key == "translate" else None,
                )
            except ValueError:
                continue
            derived_label = stage_status_to_cache_label(derived)
            current_status = stage_detail.get("status")
            if current_status == derived_label:
                continue
            updated = dict(stage_detail)
            updated["status"] = derived_label
            updated["updated_at"] = now
            if stage_key == "translate" and derived == StageStatus.SUCCEEDED:
                updated["pending_packet_count"] = 0
            stages[stage_key] = updated
            changed = True
        if not changed:
            return
        write_cached_stages(pipeline, stages)
        detail["pipeline"] = pipeline
        run.status_detail_json = detail
        repository.save_run(run)

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
