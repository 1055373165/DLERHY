from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Numeric, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from book_agent.domain.enums import (
    ActorType,
    ChapterRunPhase,
    ChapterRunStatus,
    DocumentRunStatus,
    DocumentRunType,
    InvalidatedByType,
    InvalidatedObjectType,
    JobScopeType,
    JobStatus,
    JobType,
    PacketTaskAction,
    PacketTaskStatus,
    ReviewSessionStatus,
    ReviewTerminalityState,
    RuntimeBundleRevisionStatus,
    RuntimeIncidentKind,
    RuntimeIncidentStatus,
    RuntimePatchProposalStatus,
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
    runtime_bundle_revision_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
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


class ChapterRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chapter_runs"
    __table_args__ = (UniqueConstraint("run_id", "chapter_id", name="uq_chapter_runs_run_chapter"),)

    run_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("document_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    chapter_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("chapters.id", ondelete="CASCADE"),
        nullable=False,
    )
    desired_phase: Mapped[ChapterRunPhase] = mapped_column(
        enum_value_type(ChapterRunPhase, name="chapter_run_phase"),
        nullable=False,
        default=ChapterRunPhase.PACKETIZE,
    )
    observed_phase: Mapped[ChapterRunPhase] = mapped_column(
        enum_value_type(ChapterRunPhase, name="chapter_run_phase"),
        nullable=False,
        default=ChapterRunPhase.PACKETIZE,
    )
    status: Mapped[ChapterRunStatus] = mapped_column(
        enum_value_type(ChapterRunStatus, name="chapter_run_status"),
        nullable=False,
        default=ChapterRunStatus.ACTIVE,
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    observed_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    conditions_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status_detail_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    pause_reason: Mapped[str | None] = mapped_column(Text)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PacketTask(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "packet_tasks"
    __table_args__ = (
        UniqueConstraint(
            "chapter_run_id",
            "packet_id",
            "packet_generation",
            name="uq_packet_tasks_chapter_packet_generation",
        ),
    )

    chapter_run_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("chapter_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    packet_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("translation_packets.id", ondelete="CASCADE"),
        nullable=False,
    )
    packet_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    desired_action: Mapped[PacketTaskAction] = mapped_column(
        enum_value_type(PacketTaskAction, name="packet_task_action"),
        nullable=False,
        default=PacketTaskAction.TRANSLATE,
    )
    status: Mapped[PacketTaskStatus] = mapped_column(
        enum_value_type(PacketTaskStatus, name="packet_task_status"),
        nullable=False,
        default=PacketTaskStatus.PENDING,
    )
    input_version_bundle_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    context_snapshot_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    runtime_bundle_revision_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_translation_run_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("translation_runs.id", ondelete="SET NULL"),
    )
    last_work_item_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("work_items.id", ondelete="SET NULL"),
    )
    last_error_class: Mapped[str | None] = mapped_column(Text)
    conditions_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status_detail_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "review_sessions"
    __table_args__ = (
        UniqueConstraint(
            "chapter_run_id",
            "desired_generation",
            name="uq_review_sessions_chapter_generation",
        ),
    )

    chapter_run_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("chapter_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    desired_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    observed_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[ReviewSessionStatus] = mapped_column(
        enum_value_type(ReviewSessionStatus, name="review_session_status"),
        nullable=False,
        default=ReviewSessionStatus.ACTIVE,
    )
    terminality_state: Mapped[ReviewTerminalityState] = mapped_column(
        enum_value_type(ReviewTerminalityState, name="review_terminality_state"),
        nullable=False,
        default=ReviewTerminalityState.OPEN,
    )
    scope_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    runtime_bundle_revision_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    last_work_item_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("work_items.id", ondelete="SET NULL"),
    )
    conditions_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status_detail_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    last_terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RuntimeCheckpoint(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "runtime_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "scope_type",
            "scope_id",
            "checkpoint_key",
            name="uq_runtime_checkpoints_scope_key",
        ),
    )

    run_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("document_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    scope_type: Mapped[JobScopeType] = mapped_column(
        enum_value_type(JobScopeType, name="runtime_checkpoint_scope_type"),
        nullable=False,
    )
    scope_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    checkpoint_key: Mapped[str] = mapped_column(Text, nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    checkpoint_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class RuntimeIncident(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "runtime_incidents"
    __table_args__ = (
        UniqueConstraint(
            "scope_type",
            "scope_id",
            "fingerprint",
            name="uq_runtime_incidents_scope_fingerprint",
        ),
    )

    run_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("document_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    scope_type: Mapped[JobScopeType] = mapped_column(
        enum_value_type(JobScopeType, name="runtime_incident_scope_type"),
        nullable=False,
    )
    scope_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    incident_kind: Mapped[RuntimeIncidentKind] = mapped_column(
        enum_value_type(RuntimeIncidentKind, name="runtime_incident_kind"),
        nullable=False,
    )
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str | None] = mapped_column(Text)
    selected_route: Mapped[str | None] = mapped_column(Text)
    runtime_bundle_revision_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    status: Mapped[RuntimeIncidentStatus] = mapped_column(
        enum_value_type(RuntimeIncidentStatus, name="runtime_incident_status"),
        nullable=False,
        default=RuntimeIncidentStatus.OPEN,
    )
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    latest_work_item_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("work_items.id", ondelete="SET NULL"),
    )
    route_evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    latest_error_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    bundle_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status_detail_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RuntimePatchProposal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "runtime_patch_proposals"

    incident_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("runtime_incidents.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[RuntimePatchProposalStatus] = mapped_column(
        enum_value_type(RuntimePatchProposalStatus, name="runtime_patch_proposal_status"),
        nullable=False,
        default=RuntimePatchProposalStatus.PROPOSED,
    )
    proposed_by: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[str | None] = mapped_column(Text)
    patch_surface: Mapped[str | None] = mapped_column(Text)
    diff_manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    validation_report_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    published_bundle_revision_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("runtime_bundle_revisions.id", ondelete="SET NULL"),
    )
    status_detail_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class RuntimeBundleRevision(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "runtime_bundle_revisions"

    bundle_type: Mapped[str] = mapped_column(Text, nullable=False, default="runtime")
    revision_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[RuntimeBundleRevisionStatus] = mapped_column(
        enum_value_type(RuntimeBundleRevisionStatus, name="runtime_bundle_revision_status"),
        nullable=False,
        default=RuntimeBundleRevisionStatus.DRAFT,
    )
    parent_bundle_revision_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("runtime_bundle_revisions.id", ondelete="SET NULL"),
    )
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    rollout_scope_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    runtime_bundle_revision_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
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
    runtime_bundle_revision_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))


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
