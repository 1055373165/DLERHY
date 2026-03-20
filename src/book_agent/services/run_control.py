from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from book_agent.domain.enums import ActorType, DocumentRunStatus, DocumentRunType
from book_agent.domain.models.ops import DocumentRun, RunAuditEvent, RunBudget
from book_agent.infra.repositories.run_control import RunControlRepository


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RunControlTransitionError(ValueError):
    pass


@dataclass(slots=True)
class RunBudgetSummary:
    max_wall_clock_seconds: int | None
    max_total_cost_usd: float | None
    max_total_token_in: int | None
    max_total_token_out: int | None
    max_retry_count_per_work_item: int | None
    max_consecutive_failures: int | None
    max_parallel_workers: int | None
    max_parallel_requests_per_provider: int | None
    max_auto_followup_attempts: int | None


@dataclass(slots=True)
class RunWorkItemSummary:
    total_count: int
    status_counts: dict[str, int]
    stage_counts: dict[str, int]


@dataclass(slots=True)
class RunLeaseSummary:
    total_count: int
    status_counts: dict[str, int]
    latest_heartbeat_at: str | None


@dataclass(slots=True)
class RunEventSummary:
    event_count: int
    latest_event_at: str | None


@dataclass(slots=True)
class DocumentRunSummary:
    run_id: str
    document_id: str
    run_type: str
    status: str
    backend: str | None
    model_name: str | None
    requested_by: str | None
    priority: int
    resume_from_run_id: str | None
    stop_reason: str | None
    status_detail_json: dict[str, Any]
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None
    budget: RunBudgetSummary | None
    work_items: RunWorkItemSummary
    worker_leases: RunLeaseSummary
    events: RunEventSummary


@dataclass(slots=True)
class RunAuditEventRecord:
    event_id: str
    run_id: str
    work_item_id: str | None
    event_type: str
    actor_type: str
    actor_id: str | None
    payload_json: dict[str, Any]
    created_at: str


@dataclass(slots=True)
class RunAuditEventPage:
    run_id: str
    event_count: int
    record_count: int
    offset: int
    limit: int | None
    has_more: bool
    entries: list[RunAuditEventRecord]


class RunControlService:
    def __init__(self, repository: RunControlRepository):
        self.repository = repository

    def create_run(
        self,
        *,
        document_id: str,
        run_type: DocumentRunType,
        requested_by: str,
        backend: str | None = None,
        model_name: str | None = None,
        priority: int = 100,
        resume_from_run_id: str | None = None,
        status_detail_json: dict[str, Any] | None = None,
        budget: RunBudgetSummary | None = None,
    ) -> DocumentRunSummary:
        self.repository.get_document(document_id)
        if resume_from_run_id is not None:
            resume_from_run = self.repository.get_run(resume_from_run_id)
            if resume_from_run.document_id != document_id:
                raise ValueError("resume_from_run_id must belong to the same document")

        run = DocumentRun(
            document_id=document_id,
            run_type=run_type,
            status=DocumentRunStatus.QUEUED,
            backend=backend,
            model_name=model_name,
            requested_by=requested_by,
            priority=priority,
            resume_from_run_id=resume_from_run_id,
            status_detail_json=self._with_default_status_detail(status_detail_json or {}),
        )
        created_at = _utcnow()
        run_budget = self._to_budget_model(budget)
        audit_event = RunAuditEvent(
            run_id=run.id,
            work_item_id=None,
            event_type="run.created",
            actor_type=ActorType.HUMAN,
            actor_id=requested_by,
            created_at=created_at,
            payload_json={
                "status": DocumentRunStatus.QUEUED.value,
                "run_type": run_type.value,
                "backend": backend,
                "model_name": model_name,
                "priority": priority,
                "resume_from_run_id": resume_from_run_id,
            },
        )
        self.repository.create_run(run, budget=run_budget, audit_event=audit_event)
        return self.get_run_summary(run.id)

    def get_run_summary(self, run_id: str) -> DocumentRunSummary:
        run = self.repository.get_run(run_id)
        budget = self.repository.get_budget_for_run(run_id)
        work_item_status_counts = self.repository.count_work_items_by_status(run_id)
        work_item_stage_counts = self.repository.count_work_items_by_stage(run_id)
        worker_lease_status_counts = self.repository.count_worker_leases_by_status(run_id)
        latest_heartbeat_at = self.repository.latest_worker_heartbeat_at(run_id)
        latest_event_at = self.repository.latest_run_event_at(run_id)
        event_count = self.repository.list_run_events(run_id, limit=0).total_count
        return DocumentRunSummary(
            run_id=run.id,
            document_id=run.document_id,
            run_type=run.run_type.value,
            status=run.status.value,
            backend=run.backend,
            model_name=run.model_name,
            requested_by=run.requested_by,
            priority=run.priority,
            resume_from_run_id=run.resume_from_run_id,
            stop_reason=run.stop_reason,
            status_detail_json=dict(run.status_detail_json or {}),
            created_at=self._isoformat(run.created_at),
            updated_at=self._isoformat(run.updated_at),
            started_at=self._isoformat(run.started_at),
            finished_at=self._isoformat(run.finished_at),
            budget=self._to_budget_summary(budget),
            work_items=RunWorkItemSummary(
                total_count=sum(work_item_status_counts.values()),
                status_counts=self._with_default_keys(
                    work_item_status_counts,
                    [
                        "pending",
                        "leased",
                        "running",
                        "succeeded",
                        "retryable_failed",
                        "terminal_failed",
                        "cancelled",
                    ],
                ),
                stage_counts=self._with_default_keys(
                    work_item_stage_counts,
                    ["bootstrap", "translate", "review", "repair", "export"],
                ),
            ),
            worker_leases=RunLeaseSummary(
                total_count=sum(worker_lease_status_counts.values()),
                status_counts=self._with_default_keys(
                    worker_lease_status_counts,
                    ["active", "released", "expired"],
                ),
                latest_heartbeat_at=self._isoformat(latest_heartbeat_at),
            ),
            events=RunEventSummary(
                event_count=event_count,
                latest_event_at=self._isoformat(latest_event_at),
            ),
        )

    def get_run_events(
        self,
        run_id: str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> RunAuditEventPage:
        self.repository.get_run(run_id)
        bundle = self.repository.list_run_events(run_id, limit=limit, offset=offset)
        entries = [
            RunAuditEventRecord(
                event_id=event.id,
                run_id=event.run_id,
                work_item_id=event.work_item_id,
                event_type=event.event_type,
                actor_type=event.actor_type.value,
                actor_id=event.actor_id,
                payload_json=dict(event.payload_json or {}),
                created_at=self._isoformat(event.created_at),
            )
            for event in bundle.records
        ]
        return RunAuditEventPage(
            run_id=run_id,
            event_count=bundle.total_count,
            record_count=len(entries),
            offset=offset,
            limit=limit,
            has_more=(offset + len(entries)) < bundle.total_count,
            entries=entries,
        )

    def pause_run(
        self,
        run_id: str,
        *,
        actor_id: str,
        note: str | None = None,
        detail_json: dict[str, Any] | None = None,
    ) -> DocumentRunSummary:
        return self._transition_run(
            run_id=run_id,
            allowed_from={
                DocumentRunStatus.QUEUED,
                DocumentRunStatus.RUNNING,
                DocumentRunStatus.DRAINING,
            },
            next_status=DocumentRunStatus.PAUSED,
            event_type="run.paused",
            actor_id=actor_id,
            note=note,
            detail_json=detail_json,
        )

    def resume_run(
        self,
        run_id: str,
        *,
        actor_id: str,
        note: str | None = None,
        detail_json: dict[str, Any] | None = None,
    ) -> DocumentRunSummary:
        current_status = self.repository.get_run(run_id).status
        return self._transition_run(
            run_id=run_id,
            allowed_from={DocumentRunStatus.QUEUED, DocumentRunStatus.PAUSED},
            next_status=DocumentRunStatus.RUNNING,
            event_type=("run.started" if current_status == DocumentRunStatus.QUEUED else "run.resumed"),
            actor_id=actor_id,
            note=note,
            detail_json=detail_json,
        )

    def retry_run(
        self,
        run_id: str,
        *,
        actor_id: str,
        note: str | None = None,
        detail_json: dict[str, Any] | None = None,
    ) -> DocumentRunSummary:
        previous_run = self.repository.get_run(run_id)
        if previous_run.status not in {
            DocumentRunStatus.FAILED,
            DocumentRunStatus.CANCELLED,
            DocumentRunStatus.PAUSED,
        }:
            raise RunControlTransitionError(
                "Only failed, cancelled, or paused runs can be retried from history."
            )

        previous_budget = self.repository.get_budget_for_run(run_id)
        retry_summary = self.create_run(
            document_id=previous_run.document_id,
            run_type=previous_run.run_type,
            requested_by=actor_id,
            backend=previous_run.backend,
            model_name=previous_run.model_name,
            priority=previous_run.priority,
            resume_from_run_id=run_id,
            status_detail_json=self._retry_status_detail(previous_run.status_detail_json or {}, run_id),
            budget=self._to_budget_summary(previous_budget),
        )
        previous_event = RunAuditEvent(
            run_id=previous_run.id,
            work_item_id=None,
            event_type="run.retry_requested",
            actor_type=ActorType.HUMAN,
            actor_id=actor_id,
            created_at=_utcnow(),
            payload_json={
                "retry_run_id": retry_summary.run_id,
                "note": note,
                "detail_json": detail_json or {},
            },
        )
        self.repository.save_run(previous_run, audit_event=previous_event)
        return self.resume_run(
            retry_summary.run_id,
            actor_id=actor_id,
            note=note or f"Retry run from {run_id}",
            detail_json={
                **(detail_json or {}),
                "retry_of_run_id": run_id,
            },
        )

    def drain_run(
        self,
        run_id: str,
        *,
        actor_id: str,
        note: str | None = None,
        detail_json: dict[str, Any] | None = None,
    ) -> DocumentRunSummary:
        return self._transition_run(
            run_id=run_id,
            allowed_from={
                DocumentRunStatus.QUEUED,
                DocumentRunStatus.RUNNING,
                DocumentRunStatus.PAUSED,
            },
            next_status=DocumentRunStatus.DRAINING,
            event_type="run.draining",
            actor_id=actor_id,
            note=note,
            detail_json=detail_json,
        )

    def pause_run_system(
        self,
        run_id: str,
        *,
        stop_reason: str,
        detail_json: dict[str, Any] | None = None,
    ) -> DocumentRunSummary:
        run = self.repository.get_run(run_id)
        if run.status not in {DocumentRunStatus.QUEUED, DocumentRunStatus.RUNNING, DocumentRunStatus.DRAINING}:
            return self.get_run_summary(run_id)
        return self._transition_run(
            run_id=run_id,
            allowed_from={DocumentRunStatus.QUEUED, DocumentRunStatus.RUNNING, DocumentRunStatus.DRAINING},
            next_status=DocumentRunStatus.PAUSED,
            event_type="run.paused",
            actor_id="system.run-control",
            note=stop_reason,
            detail_json=detail_json or {},
            actor_type=ActorType.SYSTEM,
        )

    def succeed_run_system(
        self,
        run_id: str,
        *,
        detail_json: dict[str, Any] | None = None,
    ) -> DocumentRunSummary:
        run = self.repository.get_run(run_id)
        if run.status == DocumentRunStatus.SUCCEEDED:
            return self.get_run_summary(run_id)
        if run.status not in {DocumentRunStatus.QUEUED, DocumentRunStatus.RUNNING, DocumentRunStatus.DRAINING}:
            return self.get_run_summary(run_id)
        return self._transition_run(
            run_id=run_id,
            allowed_from={DocumentRunStatus.QUEUED, DocumentRunStatus.RUNNING, DocumentRunStatus.DRAINING},
            next_status=DocumentRunStatus.SUCCEEDED,
            event_type="run.succeeded",
            actor_id="system.run-control",
            note=None,
            detail_json=detail_json or {},
            actor_type=ActorType.SYSTEM,
        )

    def fail_run_system(
        self,
        run_id: str,
        *,
        stop_reason: str,
        detail_json: dict[str, Any] | None = None,
    ) -> DocumentRunSummary:
        run = self.repository.get_run(run_id)
        if run.status == DocumentRunStatus.FAILED:
            return self.get_run_summary(run_id)
        if run.status not in {
            DocumentRunStatus.QUEUED,
            DocumentRunStatus.RUNNING,
            DocumentRunStatus.PAUSED,
            DocumentRunStatus.DRAINING,
        }:
            return self.get_run_summary(run_id)
        return self._transition_run(
            run_id=run_id,
            allowed_from={
                DocumentRunStatus.QUEUED,
                DocumentRunStatus.RUNNING,
                DocumentRunStatus.PAUSED,
                DocumentRunStatus.DRAINING,
            },
            next_status=DocumentRunStatus.FAILED,
            event_type="run.failed",
            actor_id="system.run-control",
            note=stop_reason,
            detail_json=detail_json or {},
            actor_type=ActorType.SYSTEM,
        )

    def cancel_run(
        self,
        run_id: str,
        *,
        actor_id: str,
        note: str | None = None,
        detail_json: dict[str, Any] | None = None,
    ) -> DocumentRunSummary:
        return self._transition_run(
            run_id=run_id,
            allowed_from={
                DocumentRunStatus.QUEUED,
                DocumentRunStatus.RUNNING,
                DocumentRunStatus.PAUSED,
                DocumentRunStatus.DRAINING,
            },
            next_status=DocumentRunStatus.CANCELLED,
            event_type="run.cancelled",
            actor_id=actor_id,
            note=note,
            detail_json=detail_json,
        )

    def _transition_run(
        self,
        *,
        run_id: str,
        allowed_from: set[DocumentRunStatus],
        next_status: DocumentRunStatus,
        event_type: str,
        actor_id: str,
        note: str | None,
        detail_json: dict[str, Any] | None,
        actor_type: ActorType = ActorType.HUMAN,
    ) -> DocumentRunSummary:
        run = self.repository.get_run(run_id)
        if run.status not in allowed_from:
            allowed_values = ", ".join(sorted(status.value for status in allowed_from))
            raise RunControlTransitionError(
                f"Run {run_id} cannot transition from {run.status.value} to {next_status.value}; allowed from: {allowed_values}"
            )

        previous_status = run.status
        now = _utcnow()
        run.status = next_status
        if next_status == DocumentRunStatus.RUNNING and run.started_at is None:
            run.started_at = now
        if next_status in {DocumentRunStatus.CANCELLED, DocumentRunStatus.FAILED, DocumentRunStatus.SUCCEEDED}:
            run.finished_at = now
        else:
            run.finished_at = None

        if next_status in {DocumentRunStatus.CANCELLED, DocumentRunStatus.FAILED, DocumentRunStatus.PAUSED}:
            run.stop_reason = note or "cancelled_by_operator"
        elif next_status == DocumentRunStatus.RUNNING:
            run.stop_reason = None

        run.status_detail_json = self._merge_status_detail(
            self._with_default_status_detail(run.status_detail_json or {}),
            action=event_type,
            actor_id=actor_id,
            note=note,
            detail_json=detail_json or {},
            at=now,
        )
        audit_event = RunAuditEvent(
            run_id=run.id,
            work_item_id=None,
            event_type=event_type,
            actor_type=actor_type,
            actor_id=actor_id,
            created_at=now,
            payload_json={
                "previous_status": previous_status.value,
                "next_status": next_status.value,
                "note": note,
                "detail_json": detail_json or {},
            },
        )
        self.repository.save_run(run, audit_event=audit_event)
        return self.get_run_summary(run.id)

    def _merge_status_detail(
        self,
        current: dict[str, Any],
        *,
        action: str,
        actor_id: str,
        note: str | None,
        detail_json: dict[str, Any],
        at: datetime,
    ) -> dict[str, Any]:
        merged = dict(current)
        merged["last_control"] = {
            "action": action,
            "actor_id": actor_id,
            "note": note,
            "detail_json": detail_json,
            "at": self._isoformat(at),
        }
        return merged

    def _with_default_status_detail(self, current: dict[str, Any]) -> dict[str, Any]:
        merged = dict(current)
        merged.setdefault(
            "usage_summary",
            {
                "token_in": 0,
                "token_out": 0,
                "cost_usd": 0.0,
                "latency_ms": 0,
            },
        )
        merged.setdefault(
            "control_counters",
            {
                "seeded_work_item_count": 0,
                "completed_work_item_count": 0,
                "retryable_failure_count": 0,
                "terminal_failure_count": 0,
                "expired_lease_reclaim_count": 0,
                "consecutive_failures": 0,
            },
        )
        return merged

    def _retry_status_detail(self, current: dict[str, Any], previous_run_id: str) -> dict[str, Any]:
        merged = dict(current)
        merged.pop("last_control", None)
        merged["retry_of_run_id"] = previous_run_id
        merged["usage_summary"] = {
            "token_in": 0,
            "token_out": 0,
            "cost_usd": 0.0,
            "latency_ms": 0,
        }
        merged["control_counters"] = {
            "seeded_work_item_count": 0,
            "completed_work_item_count": 0,
            "retryable_failure_count": 0,
            "terminal_failure_count": 0,
            "expired_lease_reclaim_count": 0,
            "consecutive_failures": 0,
        }
        pipeline = merged.get("pipeline")
        if isinstance(pipeline, dict):
            pipeline_copy = dict(pipeline)
            pipeline_copy["current_stage"] = "translate"
            stages = pipeline_copy.get("stages")
            if isinstance(stages, dict):
                pipeline_copy["stages"] = {
                    stage_name: {
                        **(stage_value if isinstance(stage_value, dict) else {}),
                        "status": "pending",
                    }
                    for stage_name, stage_value in stages.items()
                }
            merged["pipeline"] = pipeline_copy
        return merged

    def _to_budget_model(self, budget: RunBudgetSummary | None) -> RunBudget | None:
        if budget is None:
            return None
        if not any(
            (
                budget.max_wall_clock_seconds,
                budget.max_total_cost_usd,
                budget.max_total_token_in,
                budget.max_total_token_out,
                budget.max_retry_count_per_work_item,
                budget.max_consecutive_failures,
                budget.max_parallel_workers,
                budget.max_parallel_requests_per_provider,
                budget.max_auto_followup_attempts,
            )
        ):
            return None
        return RunBudget(
            run_id="",
            max_wall_clock_seconds=budget.max_wall_clock_seconds,
            max_total_cost_usd=budget.max_total_cost_usd,
            max_total_token_in=budget.max_total_token_in,
            max_total_token_out=budget.max_total_token_out,
            max_retry_count_per_work_item=budget.max_retry_count_per_work_item,
            max_consecutive_failures=budget.max_consecutive_failures,
            max_parallel_workers=budget.max_parallel_workers,
            max_parallel_requests_per_provider=budget.max_parallel_requests_per_provider,
            max_auto_followup_attempts=budget.max_auto_followup_attempts,
        )

    def _to_budget_summary(self, budget: RunBudget | None) -> RunBudgetSummary | None:
        if budget is None:
            return None
        return RunBudgetSummary(
            max_wall_clock_seconds=budget.max_wall_clock_seconds,
            max_total_cost_usd=(float(budget.max_total_cost_usd) if budget.max_total_cost_usd is not None else None),
            max_total_token_in=budget.max_total_token_in,
            max_total_token_out=budget.max_total_token_out,
            max_retry_count_per_work_item=budget.max_retry_count_per_work_item,
            max_consecutive_failures=budget.max_consecutive_failures,
            max_parallel_workers=budget.max_parallel_workers,
            max_parallel_requests_per_provider=budget.max_parallel_requests_per_provider,
            max_auto_followup_attempts=budget.max_auto_followup_attempts,
        )

    def _with_default_keys(self, current: dict[str, int], keys: list[str]) -> dict[str, int]:
        return {key: int(current.get(key, 0)) for key in keys}

    def _isoformat(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
