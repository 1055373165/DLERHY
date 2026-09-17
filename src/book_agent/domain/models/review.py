from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from book_agent.domain.enums import (
    IssueEventKind,
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
from book_agent.infra.db.base import (
    Base,
    CreatedAtMixin,
    JsonDocument,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    enum_value_type,
)


class ReviewIssue(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Current projection of a review issue.

    The row is upserted by every detection pass (review, export gate) through
    ``ReviewRepository.sync_issues``: ``created_at`` is the first sighting and
    never reset, ``version`` grows on every material change, and human
    decisions (``decided_by`` set, WONTFIX or RESOLVED) are never overturned
    by a detector. The history lives in ``review_issue_events``.
    """

    __tablename__ = "review_issues"
    __table_args__ = (Index("idx_review_issues_document_id", "document_id"),)

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
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)
    status: Mapped[IssueStatus] = mapped_column(
        enum_value_type(IssueStatus, name="issue_status"),
        nullable=False,
        default=IssueStatus.OPEN,
    )
    suggested_action: Mapped[str | None] = mapped_column(Text)
    resolution_note: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    reopen_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set by a human transition (triage / wontfix / resolve / reopen); detectors
    # respect it and only record that they saw the problem again.
    decided_by: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def human_decided(self) -> bool:
        return self.decided_by is not None and self.status in (IssueStatus.WONTFIX, IssueStatus.RESOLVED)


class ReviewIssueEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only issue history: one row per transition, carrying the issue version it produced."""

    __tablename__ = "review_issue_events"
    __table_args__ = (Index("idx_review_issue_events_issue_created", "issue_id", "created_at"),)

    issue_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("review_issues.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[IssueEventKind] = mapped_column(enum_value_type(IssueEventKind, name="issue_event_kind"), nullable=False)
    from_status: Mapped[IssueStatus | None] = mapped_column(enum_value_type(IssueStatus, name="issue_status"))
    to_status: Mapped[IssueStatus] = mapped_column(enum_value_type(IssueStatus, name="issue_status"), nullable=False)
    actor_kind: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)


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
    reason_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)
    created_by: Mapped[ActionActorType] = mapped_column(
        enum_value_type(ActionActorType, name="action_actor_type"),
        nullable=False,
    )


class Export(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "exports"
    __table_args__ = (
        # Structural guardrail: reject the exact pollution pattern that
        # caused the M0 incident — file_path pointing at an OS tempdir
        # that will be cleaned up out from under us. A route-level check
        # alone is insufficient because any writer (including a new
        # test harness) could still INSERT a tempdir path. This CHECK
        # makes that class of pollution impossible to land in the first
        # place. The `unrecoverable://<basename>` scheme is a deliberate
        # sentinel for rows whose artifact is known-gone (set by the M1
        # migration backfill); it does NOT match the regex.
        CheckConstraint(
            "file_path NOT LIKE '/var/folders/%' "
            "AND file_path NOT LIKE '/private/var/folders/%' "
            "AND file_path NOT LIKE '/tmp/%' "
            "AND file_path NOT LIKE '/private/tmp/%'",
            name="exports_file_path_no_tempdir_check",
        ),
    )

    document_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    export_type: Mapped[ExportType] = mapped_column(
        enum_value_type(ExportType, name="export_type"),
        nullable=False,
    )
    input_version_bundle_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ExportStatus] = mapped_column(
        enum_value_type(ExportStatus, name="export_status"),
        nullable=False,
    )
    # M1: artifact integrity fields. sha256 + byte_count let a verifier
    # detect tampering or silent corruption; last_verified_at supports
    # lazy re-verification on reads; stale_reason marks rows whose
    # artifact is known-gone so the download route can return 410 Gone
    # deterministically instead of re-inferring from a missing file.
    content_sha256: Mapped[str | None] = mapped_column(Text)
    byte_count: Mapped[int | None] = mapped_column(BigInteger)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stale_reason: Mapped[str | None] = mapped_column(Text)
    # Incremented on every re-export of this artifact; history in export_versions.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ExportVersion(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only history of one export artifact: what each re-export produced.

    The bytes live in the content-addressed blob store under ``content_sha256``
    (the blob reaper counts these rows as references), so an earlier version
    can be compared or restored after the canonical file was overwritten.
    Only the newest ``EXPORT_VERSION_RETENTION`` rows per export are kept.
    """

    __tablename__ = "export_versions"
    __table_args__ = (
        UniqueConstraint("export_id", "version", name="uq_export_versions_export_version"),
        Index("idx_export_versions_document_created", "document_id", "created_at"),
    )

    export_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("exports.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    export_type: Mapped[ExportType] = mapped_column(enum_value_type(ExportType, name="export_type"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    manifest_path: Mapped[str | None] = mapped_column(Text)
    content_sha256: Mapped[str | None] = mapped_column(Text)
    byte_count: Mapped[int | None] = mapped_column(BigInteger)
    input_version_bundle_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)

