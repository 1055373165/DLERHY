from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, Numeric, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from book_agent.domain.enums import (
    ActionActorType,
    ActionStatus,
    ActionType,
    Detector,
    ExportStatus,
    ExportType,
    IssueStatus,
    JobScopeType,
    RootCauseLayer,
    Severity,
)
from book_agent.infra.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_value_type


class ReviewIssue(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "review_issues"

    document_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    chapter_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), ForeignKey("chapters.id", ondelete="CASCADE"))
    block_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), ForeignKey("blocks.id", ondelete="CASCADE"))
    sentence_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("sentences.id", ondelete="CASCADE"),
    )
    packet_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("translation_packets.id", ondelete="SET NULL"),
    )
    issue_type: Mapped[str] = mapped_column(Text, nullable=False)
    root_cause_layer: Mapped[RootCauseLayer] = mapped_column(
        enum_value_type(RootCauseLayer, name="root_cause_layer"),
        nullable=False,
    )
    severity: Mapped[Severity] = mapped_column(
        enum_value_type(Severity, name="severity"),
        nullable=False,
    )
    blocking: Mapped[bool] = mapped_column(nullable=False, default=False)
    detector: Mapped[Detector] = mapped_column(
        enum_value_type(Detector, name="detector"),
        nullable=False,
    )
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[IssueStatus] = mapped_column(
        enum_value_type(IssueStatus, name="issue_status"),
        nullable=False,
        default=IssueStatus.OPEN,
    )
    suggested_action: Mapped[str | None] = mapped_column(Text)
    resolution_note: Mapped[str | None] = mapped_column(Text)


class ChapterQualitySummary(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chapter_quality_summaries"

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
    issue_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    action_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resolved_issue_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    coverage_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    alignment_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    term_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    format_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    blocking_issue_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    low_confidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    format_pollution_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class IssueAction(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "issue_actions"

    issue_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("review_issues.id", ondelete="CASCADE"),
        nullable=False,
    )
    action_type: Mapped[ActionType] = mapped_column(
        enum_value_type(ActionType, name="action_type"),
        nullable=False,
    )
    scope_type: Mapped[JobScopeType] = mapped_column(
        enum_value_type(JobScopeType, name="action_scope_type"),
        nullable=False,
    )
    scope_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    status: Mapped[ActionStatus] = mapped_column(
        enum_value_type(ActionStatus, name="action_status"),
        nullable=False,
        default=ActionStatus.PLANNED,
    )
    reason_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[ActionActorType] = mapped_column(
        enum_value_type(ActionActorType, name="action_actor_type"),
        nullable=False,
    )


class Export(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "exports"

    document_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    export_type: Mapped[ExportType] = mapped_column(
        enum_value_type(ExportType, name="export_type"),
        nullable=False,
    )
    input_version_bundle_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ExportStatus] = mapped_column(
        enum_value_type(ExportStatus, name="export_status"),
        nullable=False,
    )

    @property
    def runtime_v2_context(self) -> dict[str, Any] | None:
        payload = dict(self.input_version_bundle_json or {})
        runtime_v2 = payload.get("runtime_v2")
        return runtime_v2 if isinstance(runtime_v2, dict) else None
