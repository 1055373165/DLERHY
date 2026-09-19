from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, Text, Uuid, event, func, text
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
from book_agent.infra.db.base import Base, CreatedAtMixin, JsonDocument, TimestampMixin, UUIDPrimaryKeyMixin, enum_value_type


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
    error_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)
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
    reason_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)


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
    payload_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)


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
    # Partial UNIQUE on active runs per document. Prevents two runs
    # from simultaneously holding the QUEUED / RUNNING / DRAINING
    # "currently doing work" slot for the same document — catches
    # accidental double-submit, retry-on-retry, and resume races at
    # the DB layer. PAUSED is excluded on purpose: a retry can create
    # a new active run while an older run stays PAUSED; resuming the
    # paused one while the new one is active will fail loudly (right
    # behavior — operator has to pick one). Terminal states are
    # excluded because lineage chains keep them around forever.
    __table_args__ = (
        Index(
            "uq_document_runs_active_per_document",
            "document_id",
            unique=True,
            postgresql_where=text(
                "status IN ('queued','running','draining')"
            ),
            sqlite_where=text(
                "status IN ('queued','running','draining')"
            ),
        ),
    )

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
    status_detail_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Which executor instance runs this run's loop, until when. Instances take
    # a run over only after the lease expires; work item claims stay the
    # correctness guard, ownership keeps N instances from all ticking one run.
    executor_owner: Mapped[str | None] = mapped_column(Text)
    executor_lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "work_items"
    # Partial UNIQUE index on *active* rows: at most one work_item per
    # (run_id, stage, scope_type, scope_id) can be in a live status at
    # a time. Terminal rows (succeeded/terminal_failed/cancelled) are
    # excluded so the REPAIR stage can reseed after a prior attempt
    # terminates. Backs the app-layer dedupe in
    # RunExecutionService.seed_work_items against concurrent seeders.
    __table_args__ = (
        Index(
            "uq_work_items_active_scope",
            "run_id",
            "stage",
            "scope_type",
            "scope_id",
            unique=True,
            postgresql_where=text(
                "status IN ('pending','leased','running','retryable_failed')"
            ),
            sqlite_where=text(
                "status IN ('pending','leased','running','retryable_failed')"
            ),
        ),
    )

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
    input_version_bundle_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)
    output_artifact_refs_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)
    error_class: Mapped[str | None] = mapped_column(Text)
    error_detail_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)


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
    # Maximum seconds since the last successful work-item completion
    # (``status_detail_json.last_progress.completed_at``), falling back
    # to ``run.started_at`` if nothing has succeeded yet. When the window
    # elapses, ``enforce_budget_guardrails`` flips the run to PAUSED with
    # ``stop_reason="budget.no_progress_exceeded"`` — a softer guardrail
    # than ``max_wall_clock_seconds`` because it automatically rolls
    # forward each time the frontier makes progress. See P0.2b.
    max_no_progress_seconds: Mapped[int | None] = mapped_column(Integer)
    max_total_cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6))
    max_total_token_in: Mapped[int | None] = mapped_column(Integer)
    max_total_token_out: Mapped[int | None] = mapped_column(Integer)
    max_retry_count_per_work_item: Mapped[int | None] = mapped_column(Integer)
    max_consecutive_failures: Mapped[int | None] = mapped_column(Integer)
    max_parallel_workers: Mapped[int | None] = mapped_column(Integer)
    max_parallel_requests_per_provider: Mapped[int | None] = mapped_column(Integer)
    max_auto_followup_attempts: Mapped[int | None] = mapped_column(Integer)


class StageTransition(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only audit of pipeline stage / work_item status transitions.

    Every state change (stage cache update, work_item.status flip, reconcile
    detected drift) must insert one row in the same transaction as the
    change itself. No audit row ⇒ state change is considered illegal and
    Reconciler raises a P0 event on next scan.
    """

    __tablename__ = "stage_transitions"

    run_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("document_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    work_item_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("work_items.id", ondelete="SET NULL"),
    )
    from_status: Mapped[str] = mapped_column(Text, nullable=False)
    to_status: Mapped[str] = mapped_column(Text, nullable=False)
    triggered_by: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    caused_by_code: Mapped[str] = mapped_column(Text, nullable=False, default="")


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
    payload_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)


class Event(Base):
    """Append-only event bus (Phase 4 CC-1).

    Single source for SSE fan-out, cost telemetry, glossary/patch lifecycle.
    INSERT fires pg_notify('events_channel', id) via DB trigger. BIGSERIAL id
    gives monotonic ordering; consumers resume by last seen id.
    """

    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint("actor_kind IN ('user', 'agent', 'system')", name="events_actor_kind_check"),
    )

    id: Mapped[int] = mapped_column(
        # sqlite autoincrement only works on INTEGER PRIMARY KEY (ROWID
        # alias), not BIGINT. Use the dialect variant so Postgres still
        # gets BIGINT (matching prod migrations) while the smoke sqlite
        # schema gets a working autoincrement.
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        # Was func.clock_timestamp() (Postgres-only, per-row real-time
        # clock). Swapped to current_timestamp() — ANSI SQL, works in
        # sqlite. Consumers resume by id, so per-row clock granularity
        # was cosmetic.
        server_default=func.current_timestamp(),
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str | None] = mapped_column(Text)
    chapter_id: Mapped[str | None] = mapped_column(Text)
    packet_id: Mapped[str | None] = mapped_column(Text)
    actor_kind: Mapped[str] = mapped_column(Text, nullable=False, default="system")
    actor_id: Mapped[str] = mapped_column(Text, nullable=False, default="system")
    org_id: Mapped[str] = mapped_column(Text, nullable=False, default="default")
    correlation_id: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)


APPEND_ONLY_MODELS: tuple[type[Base], ...] = (AuditEvent, StageTransition, RunAuditEvent, Event)


class AppendOnlyViolation(RuntimeError):
    """Raised when a flush would rewrite an audit or event row."""


def _reject_update(_mapper, _connection, target) -> None:
    raise AppendOnlyViolation(f"{type(target).__name__} rows are append-only and cannot be updated.")


for _model in APPEND_ONLY_MODELS:
    event.listen(_model, "before_update", _reject_update)


def _install_structure_edit_guard() -> None:
    from book_agent.domain.models.document import StructureEdit

    event.listen(StructureEdit, "before_update", _reject_update)


_install_structure_edit_guard()
