from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from book_agent.domain.enums import (
    ActorType,
    DocumentRunStatus,
    WorkItemScopeType,
    WorkItemStage,
    WorkItemStatus,
)
from book_agent.domain.models.ops import RunAuditEvent
from book_agent.infra.repositories.run_control import ClaimedWorkItemBundle, RunControlRepository
from book_agent.services.run_control import DocumentRunSummary, RunControlService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(slots=True)
class ClaimedRunWorkItem:
    run_id: str
    work_item_id: str
    stage: str
    scope_type: str
    scope_id: str
    attempt: int
    priority: int
    lease_token: str
    worker_name: str
    worker_instance_id: str
    lease_expires_at: str


@dataclass(slots=True)
class ReclaimExpiredLeaseResult:
    expired_lease_count: int
    reclaimed_work_item_ids: list[str]


@dataclass(slots=True)
class RunBudgetGuardrailResult:
    run_summary: DocumentRunSummary
    budget_exceeded: bool
    stop_reason: str | None


class RunExecutionService:
    def __init__(
        self,
        repository: RunControlRepository,
        control_service: RunControlService | None = None,
    ):
        self.repository = repository
        self.control_service = control_service or RunControlService(repository)

    def seed_work_items(
        self,
        *,
        run_id: str,
        stage: WorkItemStage,
        scope_type: WorkItemScopeType,
        scope_ids: list[str],
        priority: int = 100,
        input_version_bundle_by_scope_id: dict[str, dict[str, Any]] | None = None,
    ) -> list[str]:
        created = self.repository.seed_work_items(
            run_id=run_id,
            stage=stage,
            scope_type=scope_type,
            scope_ids=scope_ids,
            priority=priority,
            input_version_bundle_by_scope_id=input_version_bundle_by_scope_id,
        )
        if created:
            run = self.repository.get_run(run_id)
            detail = dict(run.status_detail_json or {})
            counters = dict(detail.get("control_counters") or {})
            counters["seeded_work_item_count"] = int(counters.get("seeded_work_item_count", 0)) + len(created)
            detail["control_counters"] = counters
            run.status_detail_json = detail
            self.repository.save_run(
                run,
                audit_event=RunAuditEvent(
                    run_id=run_id,
                    work_item_id=None,
                    event_type="run.work_items.seeded",
                    actor_type=ActorType.SYSTEM,
                    actor_id="run-executor",
                    created_at=_utcnow(),
                    payload_json={
                        "stage": stage.value,
                        "scope_type": scope_type.value,
                        "created_count": len(created),
                    },
                ),
            )
        return [item.id for item in created]

    def seed_translate_work_items(
        self,
        *,
        run_id: str,
        packet_ids: list[str],
        priority: int = 100,
        input_version_bundle_by_packet_id: dict[str, dict] | None = None,
    ) -> list[str]:
        return self.seed_work_items(
            run_id=run_id,
            stage=WorkItemStage.TRANSLATE,
            scope_type=WorkItemScopeType.PACKET,
            scope_ids=packet_ids,
            priority=priority,
            input_version_bundle_by_scope_id=(
                input_version_bundle_by_packet_id
                or {
                    packet_id: {"packet_id": packet_id}
                    for packet_id in packet_ids
                }
            ),
        )

    def claim_next_work_item(
        self,
        *,
        run_id: str,
        stage: WorkItemStage,
        worker_name: str,
        worker_instance_id: str,
        lease_seconds: int,
    ) -> ClaimedRunWorkItem | None:
        run = self.repository.get_run(run_id)
        if run.status not in {DocumentRunStatus.QUEUED, DocumentRunStatus.RUNNING}:
            return None
        candidate_ids = self.repository.list_claimable_work_item_ids(
            run_id,
            stage=stage,
            limit=32,
        )
        for work_item_id in candidate_ids:
            claimed = self.claim_work_item_by_id(
                work_item_id=work_item_id,
                worker_name=worker_name,
                worker_instance_id=worker_instance_id,
                lease_seconds=lease_seconds,
            )
            if claimed is not None:
                return claimed
        return None

    def claim_work_item_by_id(
        self,
        *,
        work_item_id: str,
        worker_name: str,
        worker_instance_id: str,
        lease_seconds: int,
    ) -> ClaimedRunWorkItem | None:
        work_item = self.repository.get_work_item(work_item_id)
        run = self.repository.get_run(work_item.run_id)
        if run.status not in {DocumentRunStatus.QUEUED, DocumentRunStatus.RUNNING}:
            return None
        now = _utcnow()
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        bundle = self.repository.claim_work_item(
            work_item_id=work_item_id,
            worker_name=worker_name,
            worker_instance_id=worker_instance_id,
            lease_token=str(uuid4()),
            heartbeat_at=now,
            lease_expires_at=lease_expires_at,
        )
        if bundle is None:
            return None
        self.repository.save_run(
            run,
            audit_event=RunAuditEvent(
                run_id=run.id,
                work_item_id=bundle.work_item.id,
                event_type="work_item.leased",
                actor_type=ActorType.SYSTEM,
                actor_id=worker_instance_id,
                created_at=now,
                payload_json={
                    "stage": bundle.work_item.stage.value,
                    "scope_type": bundle.work_item.scope_type.value,
                    "scope_id": bundle.work_item.scope_id,
                    "attempt": bundle.work_item.attempt,
                    "lease_token": bundle.worker_lease.lease_token,
                    "worker_name": worker_name,
                },
            ),
        )
        return self._to_claimed_run_work_item(bundle)

    def claim_next_translate_work_item(
        self,
        *,
        run_id: str,
        worker_name: str,
        worker_instance_id: str,
        lease_seconds: int,
    ) -> ClaimedRunWorkItem | None:
        return self.claim_next_work_item(
            run_id=run_id,
            stage=WorkItemStage.TRANSLATE,
            worker_name=worker_name,
            worker_instance_id=worker_instance_id,
            lease_seconds=lease_seconds,
        )

    def start_work_item(self, *, lease_token: str, lease_seconds: int) -> ClaimedRunWorkItem:
        now = _utcnow()
        work_item = self.repository.mark_work_item_running(
            lease_token=lease_token,
            heartbeat_at=now,
            lease_expires_at=self._extend_lease(now, lease_seconds),
        )
        lease = self.repository.get_active_lease_by_token(lease_token)
        run = self.repository.get_run(work_item.run_id)
        self.repository.save_run(
            run,
            audit_event=RunAuditEvent(
                run_id=run.id,
                work_item_id=work_item.id,
                event_type="work_item.started",
                actor_type=ActorType.SYSTEM,
                actor_id=lease.worker_instance_id,
                created_at=now,
                payload_json={
                    "stage": work_item.stage.value,
                    "scope_type": work_item.scope_type.value,
                    "scope_id": work_item.scope_id,
                    "attempt": work_item.attempt,
                },
            ),
        )
        return self._to_claimed_run_work_item(ClaimedWorkItemBundle(work_item=work_item, worker_lease=lease))

    def heartbeat_work_item(
        self,
        *,
        lease_token: str,
        lease_seconds: int,
    ) -> bool:
        now = _utcnow()
        work_item = self.repository.heartbeat_lease(
            lease_token=lease_token,
            heartbeat_at=now,
            lease_expires_at=self._extend_lease(now, lease_seconds),
        )
        return work_item is not None

    def complete_translate_success(
        self,
        *,
        lease_token: str,
        packet_id: str,
        translation_run_id: str,
        token_in: int,
        token_out: int,
        cost_usd: float | None,
        latency_ms: int,
    ) -> ClaimedRunWorkItem:
        now = _utcnow()
        lease = self.repository.get_active_lease_by_token(lease_token)
        work_item = self.repository.release_work_item(
            lease_token=lease_token,
            status=WorkItemStatus.SUCCEEDED,
            released_at=now,
            output_artifact_refs_json={
                "packet_id": packet_id,
                "translation_run_id": translation_run_id,
            },
        )
        run = self.repository.get_run(work_item.run_id)
        detail = self._bump_success_progress(
            run.status_detail_json or {},
            token_in=token_in,
            token_out=token_out,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            packet_id=packet_id,
            translation_run_id=translation_run_id,
            completed_at=now,
        )
        run.status_detail_json = detail
        self.repository.save_run(
            run,
            audit_event=RunAuditEvent(
                run_id=run.id,
                work_item_id=work_item.id,
                event_type="work_item.succeeded",
                actor_type=ActorType.SYSTEM,
                actor_id=lease.worker_instance_id,
                created_at=now,
                payload_json={
                    "packet_id": packet_id,
                    "translation_run_id": translation_run_id,
                    "token_in": token_in,
                    "token_out": token_out,
                    "cost_usd": cost_usd,
                    "latency_ms": latency_ms,
                },
            ),
        )
        return self._to_claimed_run_work_item(ClaimedWorkItemBundle(work_item=work_item, worker_lease=lease))

    def complete_work_item_success(
        self,
        *,
        lease_token: str,
        output_artifact_refs_json: dict[str, Any] | None = None,
        payload_json: dict[str, Any] | None = None,
    ) -> ClaimedRunWorkItem:
        now = _utcnow()
        lease = self.repository.get_active_lease_by_token(lease_token)
        work_item = self.repository.release_work_item(
            lease_token=lease_token,
            status=WorkItemStatus.SUCCEEDED,
            released_at=now,
            output_artifact_refs_json=output_artifact_refs_json,
        )
        run = self.repository.get_run(work_item.run_id)
        self.repository.save_run(
            run,
            audit_event=RunAuditEvent(
                run_id=run.id,
                work_item_id=work_item.id,
                event_type="work_item.succeeded",
                actor_type=ActorType.SYSTEM,
                actor_id=lease.worker_instance_id,
                created_at=now,
                payload_json={
                    "stage": work_item.stage.value,
                    "scope_type": work_item.scope_type.value,
                    "scope_id": work_item.scope_id,
                    **(payload_json or {}),
                },
            ),
        )
        return self._to_claimed_run_work_item(ClaimedWorkItemBundle(work_item=work_item, worker_lease=lease))

    def complete_work_item_failure(
        self,
        *,
        lease_token: str,
        error_class: str,
        error_detail_json: dict[str, Any],
        retryable: bool,
    ) -> ClaimedRunWorkItem:
        now = _utcnow()
        lease = self.repository.get_active_lease_by_token(lease_token)
        work_item_before = self.repository.get_work_item(lease.work_item_id)
        budget = self.repository.get_budget_for_run(lease.run_id)
        max_retry_count = budget.max_retry_count_per_work_item if budget is not None else None
        should_retry = retryable and (max_retry_count is None or work_item_before.attempt < max_retry_count)
        next_status = WorkItemStatus.RETRYABLE_FAILED if should_retry else WorkItemStatus.TERMINAL_FAILED
        work_item = self.repository.release_work_item(
            lease_token=lease_token,
            status=next_status,
            released_at=now,
            error_class=error_class,
            error_detail_json=error_detail_json,
        )
        run = self.repository.get_run(work_item.run_id)
        detail = self._bump_failure_progress(
            run.status_detail_json or {},
            error_class=error_class,
            failed_at=now,
            retryable=should_retry,
        )
        run.status_detail_json = detail
        self.repository.save_run(
            run,
            audit_event=RunAuditEvent(
                run_id=run.id,
                work_item_id=work_item.id,
                event_type=(
                    "work_item.retryable_failed"
                    if should_retry
                    else "work_item.terminal_failed"
                ),
                actor_type=ActorType.SYSTEM,
                actor_id=lease.worker_instance_id,
                created_at=now,
                payload_json={
                    "error_class": error_class,
                    "error_detail_json": error_detail_json,
                    "attempt": work_item.attempt,
                },
            ),
        )
        return self._to_claimed_run_work_item(ClaimedWorkItemBundle(work_item=work_item, worker_lease=lease))

    def reclaim_expired_leases(self, *, run_id: str) -> ReclaimExpiredLeaseResult:
        now = _utcnow()
        expired_leases = self.repository.list_expired_active_leases(run_id, expired_before=now)
        reclaimed_ids: list[str] = []
        if not expired_leases:
            return ReclaimExpiredLeaseResult(expired_lease_count=0, reclaimed_work_item_ids=[])
        run = self.repository.get_run(run_id)
        for lease in expired_leases:
            work_item = self.repository.expire_lease(
                lease_id=lease.id,
                expired_at=now,
                error_class="lease_expired",
                error_detail_json={
                    "lease_token": lease.lease_token,
                    "worker_name": lease.worker_name,
                    "worker_instance_id": lease.worker_instance_id,
                    "lease_expires_at": lease.lease_expires_at.isoformat() if lease.lease_expires_at else None,
                },
            )
            if work_item is None:
                continue
            reclaimed_ids.append(work_item.id)
            self.repository.save_run(
                run,
                audit_event=RunAuditEvent(
                    run_id=run_id,
                    work_item_id=work_item.id,
                    event_type="worker_lease.expired",
                    actor_type=ActorType.SYSTEM,
                    actor_id="run-executor",
                    created_at=now,
                    payload_json={
                        "lease_token": lease.lease_token,
                        "worker_name": lease.worker_name,
                        "worker_instance_id": lease.worker_instance_id,
                    },
                ),
            )
        if reclaimed_ids:
            detail = dict(run.status_detail_json or {})
            counters = dict(detail.get("control_counters") or {})
            counters["expired_lease_reclaim_count"] = int(counters.get("expired_lease_reclaim_count", 0)) + len(reclaimed_ids)
            detail["control_counters"] = counters
            run.status_detail_json = detail
            self.repository.save_run(run)
        return ReclaimExpiredLeaseResult(
            expired_lease_count=len(reclaimed_ids),
            reclaimed_work_item_ids=reclaimed_ids,
        )

    def enforce_budget_guardrails(self, *, run_id: str) -> RunBudgetGuardrailResult:
        run = self.repository.get_run(run_id)
        budget = self.repository.get_budget_for_run(run_id)
        if budget is None:
            return RunBudgetGuardrailResult(
                run_summary=self.control_service.get_run_summary(run_id),
                budget_exceeded=False,
                stop_reason=None,
            )

        detail = dict(run.status_detail_json or {})
        usage = dict(detail.get("usage_summary") or {})
        counters = dict(detail.get("control_counters") or {})
        now = _utcnow()
        baseline_started_at = _ensure_utc(run.started_at) or _ensure_utc(run.created_at) or now
        elapsed_seconds = int((now - baseline_started_at).total_seconds())

        if budget.max_wall_clock_seconds is not None and elapsed_seconds >= budget.max_wall_clock_seconds:
            summary = self.control_service.pause_run_system(
                run_id,
                stop_reason="budget.wall_clock_exceeded",
                detail_json={
                    "elapsed_seconds": elapsed_seconds,
                    "max_wall_clock_seconds": budget.max_wall_clock_seconds,
                },
            )
            return RunBudgetGuardrailResult(summary, True, "budget.wall_clock_exceeded")

        total_cost_usd = float(usage.get("cost_usd", 0.0) or 0.0)
        if budget.max_total_cost_usd is not None and total_cost_usd >= float(budget.max_total_cost_usd):
            summary = self.control_service.pause_run_system(
                run_id,
                stop_reason="budget.cost_exceeded",
                detail_json={
                    "total_cost_usd": total_cost_usd,
                    "max_total_cost_usd": float(budget.max_total_cost_usd),
                },
            )
            return RunBudgetGuardrailResult(summary, True, "budget.cost_exceeded")

        total_token_in = int(usage.get("token_in", 0) or 0)
        if budget.max_total_token_in is not None and total_token_in >= budget.max_total_token_in:
            summary = self.control_service.pause_run_system(
                run_id,
                stop_reason="budget.token_in_exceeded",
                detail_json={
                    "total_token_in": total_token_in,
                    "max_total_token_in": budget.max_total_token_in,
                },
            )
            return RunBudgetGuardrailResult(summary, True, "budget.token_in_exceeded")

        total_token_out = int(usage.get("token_out", 0) or 0)
        if budget.max_total_token_out is not None and total_token_out >= budget.max_total_token_out:
            summary = self.control_service.pause_run_system(
                run_id,
                stop_reason="budget.token_out_exceeded",
                detail_json={
                    "total_token_out": total_token_out,
                    "max_total_token_out": budget.max_total_token_out,
                },
            )
            return RunBudgetGuardrailResult(summary, True, "budget.token_out_exceeded")

        consecutive_failures = int(counters.get("consecutive_failures", 0) or 0)
        if budget.max_consecutive_failures is not None and consecutive_failures >= budget.max_consecutive_failures:
            summary = self.control_service.fail_run_system(
                run_id,
                stop_reason="budget.consecutive_failures_exceeded",
                detail_json={
                    "consecutive_failures": consecutive_failures,
                    "max_consecutive_failures": budget.max_consecutive_failures,
                },
            )
            return RunBudgetGuardrailResult(summary, True, "budget.consecutive_failures_exceeded")

        return RunBudgetGuardrailResult(
            run_summary=self.control_service.get_run_summary(run_id),
            budget_exceeded=False,
            stop_reason=None,
        )

    def reconcile_run_terminal_state(self, *, run_id: str) -> DocumentRunSummary:
        run = self.repository.get_run(run_id)
        if run.status in {
            DocumentRunStatus.SUCCEEDED,
            DocumentRunStatus.FAILED,
            DocumentRunStatus.CANCELLED,
            DocumentRunStatus.PAUSED,
        }:
            return self.control_service.get_run_summary(run_id)

        inflight_count = self.repository.count_inflight_work_items(run_id)
        claimable_count = self.repository.count_claimable_work_items(run_id)
        terminal_failed_count = self.repository.count_terminal_failed_work_items(run_id)

        if inflight_count > 0:
            return self.control_service.get_run_summary(run_id)

        if run.status == DocumentRunStatus.DRAINING and claimable_count > 0:
            return self.control_service.pause_run_system(
                run_id,
                stop_reason="run.drain_complete_with_pending_items",
                detail_json={"remaining_claimable_work_items": claimable_count},
            )

        if claimable_count == 0:
            if terminal_failed_count > 0:
                return self.control_service.fail_run_system(
                    run_id,
                    stop_reason="run.terminal_failed_items_present",
                    detail_json={"terminal_failed_work_item_count": terminal_failed_count},
                )
            return self.control_service.succeed_run_system(
                run_id,
                detail_json={"completed_work_item_count": self.repository.count_succeeded_work_items(run_id)},
            )

        return self.control_service.get_run_summary(run_id)

    def _bump_success_progress(
        self,
        current: dict[str, Any],
        *,
        token_in: int,
        token_out: int,
        cost_usd: float | None,
        latency_ms: int,
        packet_id: str,
        translation_run_id: str,
        completed_at: datetime,
    ) -> dict[str, Any]:
        detail = dict(current)
        usage = dict(detail.get("usage_summary") or {})
        usage["token_in"] = int(usage.get("token_in", 0) or 0) + token_in
        usage["token_out"] = int(usage.get("token_out", 0) or 0) + token_out
        usage["cost_usd"] = round(float(usage.get("cost_usd", 0.0) or 0.0) + float(cost_usd or 0.0), 8)
        usage["latency_ms"] = int(usage.get("latency_ms", 0) or 0) + latency_ms
        detail["usage_summary"] = usage

        counters = dict(detail.get("control_counters") or {})
        counters["completed_work_item_count"] = int(counters.get("completed_work_item_count", 0)) + 1
        counters["consecutive_failures"] = 0
        detail["control_counters"] = counters
        detail["last_progress"] = {
            "packet_id": packet_id,
            "translation_run_id": translation_run_id,
            "completed_at": completed_at.astimezone(timezone.utc).isoformat(),
        }
        return detail

    def _bump_failure_progress(
        self,
        current: dict[str, Any],
        *,
        error_class: str,
        failed_at: datetime,
        retryable: bool,
    ) -> dict[str, Any]:
        detail = dict(current)
        counters = dict(detail.get("control_counters") or {})
        if retryable:
            counters["retryable_failure_count"] = int(counters.get("retryable_failure_count", 0)) + 1
        else:
            counters["terminal_failure_count"] = int(counters.get("terminal_failure_count", 0)) + 1
        counters["consecutive_failures"] = int(counters.get("consecutive_failures", 0)) + 1
        detail["control_counters"] = counters
        detail["last_failure"] = {
            "error_class": error_class,
            "retryable": retryable,
            "failed_at": failed_at.astimezone(timezone.utc).isoformat(),
        }
        return detail

    def _extend_lease(self, at: datetime, lease_seconds: int) -> datetime:
        return at + timedelta(seconds=lease_seconds)

    def _to_claimed_run_work_item(self, bundle: ClaimedWorkItemBundle) -> ClaimedRunWorkItem:
        return ClaimedRunWorkItem(
            run_id=bundle.work_item.run_id,
            work_item_id=bundle.work_item.id,
            stage=bundle.work_item.stage.value,
            scope_type=bundle.work_item.scope_type.value,
            scope_id=bundle.work_item.scope_id,
            attempt=bundle.work_item.attempt,
            priority=bundle.work_item.priority,
            lease_token=bundle.worker_lease.lease_token,
            worker_name=bundle.worker_lease.worker_name,
            worker_instance_id=bundle.worker_lease.worker_instance_id,
            lease_expires_at=bundle.worker_lease.lease_expires_at.astimezone(timezone.utc).isoformat(),
        )
