from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Numeric, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from book_agent.domain.enums import (
    ActorType,
    DocumentRunStatus,
    DocumentRunType,
    InvalidatedByType,
    InvalidatedObjectType,
    JobScopeType,
    JobStatus,
    JobType,
    WorkItemScopeType,
    WorkItemStage,
    WorkItemStatus,
    WorkerLeaseStatus,
)
from book_agent.infra.db.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin, enum_value_type


class JobRun(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "job_runs"

    job_type: Mapped[JobType] = mapped_column(
        enum_value_type(JobType, name="job_type"),
        nullable=False,
    )
    scope_type: Mapped[JobScopeType] = mapped_column(
        enum_value_type(JobScopeType, name="job_scope_type"),
        nullable=False,
    )
    scope_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        enum_value_type(JobStatus, name="job_status"),
        nullable=False,
    )
    retry_count: Mapped[int] = mapped_column(nullable=False, default=0)
    rerun_reason: Mapped[str | None] = mapped_column(Text)
    error_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ArtifactInvalidation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "artifact_invalidations"

    object_type: Mapped[InvalidatedObjectType] = mapped_column(
        enum_value_type(InvalidatedObjectType, name="invalidated_object_type"),
        nullable=False,
    )
    object_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    invalidated_by_type: Mapped[InvalidatedByType] = mapped_column(
        enum_value_type(InvalidatedByType, name="invalidated_by_type"),
        nullable=False,
    )
    invalidated_by_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    reason_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class AuditEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "audit_events"

    object_type: Mapped[str] = mapped_column(Text, nullable=False)
    object_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    actor_type: Mapped[ActorType] = mapped_column(
        enum_value_type(ActorType, name="actor_type"),
        nullable=False,
    )
    actor_id: Mapped[str | None] = mapped_column(Text)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class ChapterWorklistAssignment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chapter_worklist_assignments"

    document_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    chapter_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("chapters.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    owner_name: Mapped[str] = mapped_column(Text, nullable=False)
    assigned_by: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class DocumentRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "document_runs"

    document_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_type: Mapped[DocumentRunType] = mapped_column(
        enum_value_type(DocumentRunType, name="document_run_type"),
        nullable=False,
    )
    status: Mapped[DocumentRunStatus] = mapped_column(
        enum_value_type(DocumentRunStatus, name="document_run_status"),
        nullable=False,
    )
    backend: Mapped[str | None] = mapped_column(Text)
    model_name: Mapped[str | None] = mapped_column(Text)
    requested_by: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(nullable=False, default=100)
    resume_from_run_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("document_runs.id", ondelete="SET NULL"),
    )
    stop_reason: Mapped[str | None] = mapped_column(Text)
    status_detail_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "work_items"

    run_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("document_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    stage: Mapped[WorkItemStage] = mapped_column(
        enum_value_type(WorkItemStage, name="work_item_stage"),
        nullable=False,
    )
    scope_type: Mapped[WorkItemScopeType] = mapped_column(
        enum_value_type(WorkItemScopeType, name="work_item_scope_type"),
        nullable=False,
    )
    scope_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    attempt: Mapped[int] = mapped_column(nullable=False, default=1)
    priority: Mapped[int] = mapped_column(nullable=False, default=100)
    status: Mapped[WorkItemStatus] = mapped_column(
        enum_value_type(WorkItemStatus, name="work_item_status"),
        nullable=False,
        default=WorkItemStatus.PENDING,
    )
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    input_version_bundle_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    output_artifact_refs_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_class: Mapped[str | None] = mapped_column(Text)
    error_detail_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class WorkerLease(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "worker_leases"

    run_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("document_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    work_item_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    worker_name: Mapped[str] = mapped_column(Text, nullable=False)
    worker_instance_id: Mapped[str] = mapped_column(Text, nullable=False)
    lease_token: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    status: Mapped[WorkerLeaseStatus] = mapped_column(
        enum_value_type(WorkerLeaseStatus, name="worker_lease_status"),
        nullable=False,
        default=WorkerLeaseStatus.ACTIVE,
    )
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunBudget(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "run_budgets"

    run_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("document_runs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    max_wall_clock_seconds: Mapped[int | None] = mapped_column(Integer)
    max_total_cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6))
    max_total_token_in: Mapped[int | None] = mapped_column(Integer)
    max_total_token_out: Mapped[int | None] = mapped_column(Integer)
    max_retry_count_per_work_item: Mapped[int | None] = mapped_column(Integer)
    max_consecutive_failures: Mapped[int | None] = mapped_column(Integer)
    max_parallel_workers: Mapped[int | None] = mapped_column(Integer)
    max_parallel_requests_per_provider: Mapped[int | None] = mapped_column(Integer)
    max_auto_followup_attempts: Mapped[int | None] = mapped_column(Integer)


class RunAuditEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "run_audit_events"

    run_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("document_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    work_item_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("work_items.id", ondelete="SET NULL"),
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor_type: Mapped[ActorType] = mapped_column(
        enum_value_type(ActorType, name="run_audit_actor_type"),
        nullable=False,
    )
    actor_id: Mapped[str | None] = mapped_column(Text)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
