from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import case, func, select, update
from sqlalchemy.orm import Session

from book_agent.domain.enums import WorkItemScopeType, WorkItemStage, WorkItemStatus, WorkerLeaseStatus
from book_agent.domain.models import Document
from book_agent.domain.models.ops import DocumentRun, RunAuditEvent, RunBudget, WorkItem, WorkerLease


@dataclass(slots=True)
class RunEventPageBundle:
    total_count: int
    records: list[RunAuditEvent]


@dataclass(slots=True)
class ClaimedWorkItemBundle:
    work_item: WorkItem
    worker_lease: WorkerLease


class RunControlRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_document(self, document_id: str) -> Document:
        document = self.session.get(Document, document_id)
        if document is None:
            raise ValueError(f"Document not found: {document_id}")
        return document

    def get_run(self, run_id: str) -> DocumentRun:
        run = self.session.get(DocumentRun, run_id)
        if run is None:
            raise ValueError(f"Document run not found: {run_id}")
        return run

    def get_work_item(self, work_item_id: str) -> WorkItem:
        work_item = self.session.get(WorkItem, work_item_id)
        if work_item is None:
            raise ValueError(f"Work item not found: {work_item_id}")
        return work_item

    def get_budget_for_run(self, run_id: str) -> RunBudget | None:
        return self.session.scalar(select(RunBudget).where(RunBudget.run_id == run_id))

    def create_run(
        self,
        run: DocumentRun,
        *,
        budget: RunBudget | None = None,
        audit_event: RunAuditEvent | None = None,
    ) -> DocumentRun:
        now = _utcnow()
        run.created_at = run.created_at or now
        run.updated_at = run.updated_at or now
        self.session.add(run)
        self.session.flush()
        if budget is not None:
            budget.run_id = run.id
            self.session.add(budget)
        if audit_event is not None:
            audit_event.run_id = run.id
            self.session.add(audit_event)
        self.session.flush()
        return run

    def save_run(self, run: DocumentRun, audit_event: RunAuditEvent | None = None) -> DocumentRun:
        run.updated_at = _utcnow()
        self.session.merge(run)
        if audit_event is not None:
            self.session.add(audit_event)
        self.session.flush()
        return run

    def list_run_events(
        self,
        run_id: str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> RunEventPageBundle:
        total_count = self.session.scalar(
            select(func.count(RunAuditEvent.id)).where(RunAuditEvent.run_id == run_id)
        ) or 0
        query = (
            select(RunAuditEvent)
            .where(RunAuditEvent.run_id == run_id)
            .order_by(RunAuditEvent.created_at.desc(), RunAuditEvent.id.desc())
            .offset(offset)
        )
        if limit is not None:
            query = query.limit(limit)
        records = self.session.scalars(query).all()
        return RunEventPageBundle(total_count=total_count, records=records)

    def count_work_items_by_status(self, run_id: str) -> dict[str, int]:
        rows = self.session.execute(
            select(WorkItem.status, func.count(WorkItem.id))
            .where(WorkItem.run_id == run_id)
            .group_by(WorkItem.status)
        ).all()
        return {str(status.value): count for status, count in rows}

    def count_work_items_by_stage(self, run_id: str) -> dict[str, int]:
        rows = self.session.execute(
            select(WorkItem.stage, func.count(WorkItem.id))
            .where(WorkItem.run_id == run_id)
            .group_by(WorkItem.stage)
        ).all()
        return {str(stage.value): count for stage, count in rows}

    def count_worker_leases_by_status(self, run_id: str) -> dict[str, int]:
        rows = self.session.execute(
            select(WorkerLease.status, func.count(WorkerLease.id))
            .where(WorkerLease.run_id == run_id)
            .group_by(WorkerLease.status)
        ).all()
        return {str(status.value): count for status, count in rows}

    def latest_worker_heartbeat_at(self, run_id: str) -> datetime | None:
        return self.session.scalar(
            select(func.max(WorkerLease.last_heartbeat_at)).where(WorkerLease.run_id == run_id)
        )

    def latest_run_event_at(self, run_id: str) -> datetime | None:
        return self.session.scalar(
            select(func.max(RunAuditEvent.created_at)).where(RunAuditEvent.run_id == run_id)
        )

    def seed_work_items(
        self,
        *,
        run_id: str,
        stage: WorkItemStage,
        scope_type: WorkItemScopeType,
        scope_ids: list[str],
        priority: int,
        input_version_bundle_by_scope_id: dict[str, dict] | None = None,
    ) -> list[WorkItem]:
        if not scope_ids:
            return []
        existing_scope_ids = set(
            self.session.scalars(
                select(WorkItem.scope_id).where(
                    WorkItem.run_id == run_id,
                    WorkItem.stage == stage,
                    WorkItem.scope_type == scope_type,
                    WorkItem.scope_id.in_(scope_ids),
                )
            ).all()
        )
        created: list[WorkItem] = []
        version_map = input_version_bundle_by_scope_id or {}
        for scope_id in scope_ids:
            if scope_id in existing_scope_ids:
                continue
            work_item = WorkItem(
                run_id=run_id,
                stage=stage,
                scope_type=scope_type,
                scope_id=scope_id,
                priority=priority,
                status=WorkItemStatus.PENDING,
                input_version_bundle_json=version_map.get(scope_id, {}),
            )
            self.session.add(work_item)
            created.append(work_item)
        self.session.flush()
        return created

    def count_claimable_work_items(self, run_id: str) -> int:
        return self.session.scalar(
            select(func.count(WorkItem.id)).where(
                WorkItem.run_id == run_id,
                WorkItem.status.in_([WorkItemStatus.PENDING, WorkItemStatus.RETRYABLE_FAILED]),
            )
        ) or 0

    def count_inflight_work_items(self, run_id: str) -> int:
        return self.session.scalar(
            select(func.count(WorkItem.id)).where(
                WorkItem.run_id == run_id,
                WorkItem.status.in_([WorkItemStatus.LEASED, WorkItemStatus.RUNNING]),
            )
        ) or 0

    def count_terminal_failed_work_items(self, run_id: str) -> int:
        return self.session.scalar(
            select(func.count(WorkItem.id)).where(
                WorkItem.run_id == run_id,
                WorkItem.status == WorkItemStatus.TERMINAL_FAILED,
            )
        ) or 0

    def count_succeeded_work_items(self, run_id: str) -> int:
        return self.session.scalar(
            select(func.count(WorkItem.id)).where(
                WorkItem.run_id == run_id,
                WorkItem.status == WorkItemStatus.SUCCEEDED,
            )
        ) or 0

    def list_claimable_work_item_ids(
        self,
        run_id: str,
        *,
        stage: WorkItemStage | None = None,
        limit: int = 32,
    ) -> list[str]:
        stmt = (
            select(WorkItem.id)
            .where(
                WorkItem.run_id == run_id,
                WorkItem.status.in_([WorkItemStatus.PENDING, WorkItemStatus.RETRYABLE_FAILED]),
            )
            .order_by(WorkItem.priority.asc(), WorkItem.created_at.asc(), WorkItem.id.asc())
            .limit(limit)
        )
        if stage is not None:
            stmt = stmt.where(WorkItem.stage == stage)
        return list(self.session.scalars(stmt).all())

    def claim_work_item(
        self,
        *,
        work_item_id: str,
        worker_name: str,
        worker_instance_id: str,
        lease_token: str,
        heartbeat_at: datetime,
        lease_expires_at: datetime,
    ) -> ClaimedWorkItemBundle | None:
        result = self.session.execute(
            update(WorkItem)
            .where(
                WorkItem.id == work_item_id,
                WorkItem.status.in_([WorkItemStatus.PENDING, WorkItemStatus.RETRYABLE_FAILED]),
            )
            .values(
                status=WorkItemStatus.LEASED,
                lease_owner=worker_instance_id,
                lease_expires_at=lease_expires_at,
                last_heartbeat_at=heartbeat_at,
                attempt=case(
                    (WorkItem.status == WorkItemStatus.RETRYABLE_FAILED, WorkItem.attempt + 1),
                    else_=WorkItem.attempt,
                ),
                error_class=None,
                error_detail_json={},
            )
        )
        if result.rowcount != 1:
            self.session.flush()
            return None

        work_item = self.session.get(WorkItem, work_item_id)
        if work_item is None:
            raise ValueError(f"Work item not found after claim: {work_item_id}")

        worker_lease = WorkerLease(
            run_id=work_item.run_id,
            work_item_id=work_item.id,
            worker_name=worker_name,
            worker_instance_id=worker_instance_id,
            lease_token=lease_token,
            status=WorkerLeaseStatus.ACTIVE,
            lease_expires_at=lease_expires_at,
            last_heartbeat_at=heartbeat_at,
        )
        self.session.add(worker_lease)
        self.session.flush()
        return ClaimedWorkItemBundle(work_item=work_item, worker_lease=worker_lease)

    def get_active_lease_by_token(self, lease_token: str) -> WorkerLease:
        lease = self.session.scalar(
            select(WorkerLease).where(
                WorkerLease.lease_token == lease_token,
                WorkerLease.status == WorkerLeaseStatus.ACTIVE,
            )
        )
        if lease is None:
            raise ValueError(f"Active worker lease not found: {lease_token}")
        return lease

    def mark_work_item_running(
        self,
        *,
        lease_token: str,
        heartbeat_at: datetime,
        lease_expires_at: datetime,
    ) -> WorkItem:
        lease = self.get_active_lease_by_token(lease_token)
        self.session.execute(
            update(WorkItem)
            .where(
                WorkItem.id == lease.work_item_id,
                WorkItem.status == WorkItemStatus.LEASED,
            )
            .values(
                status=WorkItemStatus.RUNNING,
                started_at=case((WorkItem.started_at.is_(None), heartbeat_at), else_=WorkItem.started_at),
                last_heartbeat_at=heartbeat_at,
                lease_expires_at=lease_expires_at,
            )
        )
        lease.last_heartbeat_at = heartbeat_at
        lease.lease_expires_at = lease_expires_at
        self.session.flush()
        work_item = self.session.get(WorkItem, lease.work_item_id)
        if work_item is None:
            raise ValueError(f"Work item not found for lease token: {lease_token}")
        return work_item

    def heartbeat_lease(
        self,
        *,
        lease_token: str,
        heartbeat_at: datetime,
        lease_expires_at: datetime,
    ) -> WorkItem | None:
        lease = self.session.scalar(
            select(WorkerLease).where(
                WorkerLease.lease_token == lease_token,
                WorkerLease.status == WorkerLeaseStatus.ACTIVE,
            )
        )
        if lease is None:
            return None
        work_item = self.session.get(WorkItem, lease.work_item_id)
        if work_item is None or work_item.status not in {WorkItemStatus.LEASED, WorkItemStatus.RUNNING}:
            return None
        lease.last_heartbeat_at = heartbeat_at
        lease.lease_expires_at = lease_expires_at
        work_item.last_heartbeat_at = heartbeat_at
        work_item.lease_expires_at = lease_expires_at
        self.session.flush()
        return work_item

    def release_work_item(
        self,
        *,
        lease_token: str,
        status: WorkItemStatus,
        released_at: datetime,
        output_artifact_refs_json: dict | None = None,
        error_class: str | None = None,
        error_detail_json: dict | None = None,
    ) -> WorkItem:
        lease = self.get_active_lease_by_token(lease_token)
        work_item = self.session.get(WorkItem, lease.work_item_id)
        if work_item is None:
            raise ValueError(f"Work item not found for lease token: {lease_token}")
        work_item.status = status
        work_item.finished_at = released_at
        work_item.last_heartbeat_at = released_at
        work_item.lease_owner = None
        work_item.lease_expires_at = None
        if output_artifact_refs_json is not None:
            work_item.output_artifact_refs_json = output_artifact_refs_json
        if error_class is not None:
            work_item.error_class = error_class
        if error_detail_json is not None:
            work_item.error_detail_json = error_detail_json
        lease.status = WorkerLeaseStatus.RELEASED
        lease.last_heartbeat_at = released_at
        lease.released_at = released_at
        self.session.flush()
        return work_item

    def expire_lease(
        self,
        *,
        lease_id: str,
        expired_at: datetime,
        error_class: str,
        error_detail_json: dict,
    ) -> WorkItem | None:
        lease = self.session.get(WorkerLease, lease_id)
        if lease is None or lease.status != WorkerLeaseStatus.ACTIVE:
            return None
        work_item = self.session.get(WorkItem, lease.work_item_id)
        lease.status = WorkerLeaseStatus.EXPIRED
        lease.last_heartbeat_at = expired_at
        lease.released_at = expired_at
        if work_item is None:
            self.session.flush()
            return None
        if work_item.status not in {WorkItemStatus.LEASED, WorkItemStatus.RUNNING}:
            self.session.flush()
            return None
        work_item.status = WorkItemStatus.RETRYABLE_FAILED
        work_item.last_heartbeat_at = expired_at
        work_item.lease_owner = None
        work_item.lease_expires_at = None
        work_item.error_class = error_class
        work_item.error_detail_json = error_detail_json
        self.session.flush()
        return work_item

    def list_expired_active_leases(self, run_id: str, *, expired_before: datetime) -> list[WorkerLease]:
        return self.session.scalars(
            select(WorkerLease)
            .where(
                WorkerLease.run_id == run_id,
                WorkerLease.status == WorkerLeaseStatus.ACTIVE,
                WorkerLease.lease_expires_at < expired_before,
            )
            .order_by(WorkerLease.lease_expires_at.asc(), WorkerLease.id.asc())
        ).all()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
