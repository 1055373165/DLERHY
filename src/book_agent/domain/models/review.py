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



class PilotQualityReport(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One run of the pilot's layered QA (domain/quality/pilot_qa.py) on the pilot's chapters.

    A row per run, like parse health reports: a repair round writes a new report and the earlier
    one stays, so the repair agent is judged by comparing the two. ``run_id`` is the pilot run whose
    ``pilot_qa`` stage wrote it (None for a report asked for by hand on chapters translated otherwise).
    ``layers_json`` holds L1–L7, each with its verdict, counts by severity, checks and findings.
    """

    __tablename__ = "pilot_quality_reports"
    __table_args__ = (
        CheckConstraint("status IN ('passed', 'warnings', 'blocking', 'error')", name="ck_pilot_quality_reports_status"),
        Index("idx_pilot_quality_reports_document", "document_id", "created_at"),
        Index("idx_pilot_quality_reports_run", "run_id"),
    )

    document_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), ForeignKey("document_runs.id", ondelete="SET NULL"))
    trigger: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    blocking_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    major_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    minor_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chapter_ids_json: Mapped[list[str]] = mapped_column(JsonDocument, nullable=False, default=list)
    layers_json: Mapped[list[dict[str, Any]]] = mapped_column(JsonDocument, nullable=False, default=list)
    # Where the exported chapters came from: the reading copy's file, a render in memory, or nothing.
    export_source: Mapped[str] = mapped_column(Text, nullable=False, default="none")
    error_text: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class PilotRepair(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One run of the pilot repair loop (services/pilot_repair.py) after the pilot's layered QA.

    docs/agent-upgrade/08-pilot-quality-gate.md §7. The loop takes the QA report's blocking and major
    findings, tries repairs from a closed toolbox, keeps a repair only when the same checks say it
    helped and nothing else got worse, and stops after three rounds or twice the pilot's cost. What
    it tried is in ``pilot_repair_attempts``; systemic defects it found are ``pilot_defect_tickets``;
    what it could not repair becomes one "需要你处理" question per issue class (an ``approvals`` row
    of kind ``pilot_question``). ``after_report_id`` is the QA report of the re-exported pilot.
    """

    __tablename__ = "pilot_repairs"
    __table_args__ = (
        CheckConstraint("status IN ('running', 'finished', 'error')", name="ck_pilot_repairs_status"),
        Index("idx_pilot_repairs_document", "document_id", "created_at"),
        Index("idx_pilot_repairs_run", "run_id"),
    )

    document_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), ForeignKey("document_runs.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    # passed (no blocking finding left) | escalated (some left, asked the reader) | nothing_to_do | error
    outcome: Mapped[str | None] = mapped_column(Text)
    # clean | max_rounds | budget | time | exhausted (every path tried)
    stop_reason: Mapped[str | None] = mapped_column(Text)
    rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    before_report_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("pilot_quality_reports.id", ondelete="SET NULL")
    )
    after_report_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("pilot_quality_reports.id", ondelete="SET NULL")
    )
    repaired_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    escalated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    remaining_blocking: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    remaining_major: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    budget_usd: Mapped[float | None] = mapped_column(Numeric(12, 6))
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)
    error_text: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PilotDefectTicket(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A systemic defect the pilot repair found (§7.3): evidence for the dev-side coding agent.

    The repair loop never changes code. For a problem whose cause is a generic rule (§7.1 rules 1–3)
    it gives the book a workaround from the toolbox and files a ticket here: the check, the pages,
    blocks and sample text, the suspected cause and the component to fix. Shown on the admin side,
    never to readers.
    """

    __tablename__ = "pilot_defect_tickets"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'fixed', 'wontfix')", name="ck_pilot_defect_tickets_status"),
        Index("idx_pilot_defect_tickets_document", "document_id", "created_at"),
        Index("idx_pilot_defect_tickets_check", "check_id", "status"),
    )

    document_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    repair_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), ForeignKey("pilot_repairs.id", ondelete="SET NULL"))
    check_id: Mapped[str] = mapped_column(Text, nullable=False)
    layer: Mapped[str] = mapped_column(Text, nullable=False)
    # The generic cause's id ("L7.cjk_latin_spacing" or "L2.figure_regions:page_furniture").
    issue_class: Mapped[str] = mapped_column(Text, nullable=False)
    classification_rule: Mapped[int] = mapped_column(Integer, nullable=False)
    # parser | export | translation
    component: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    suspected_cause: Mapped[str] = mapped_column(Text, nullable=False)
    reproduction: Mapped[str] = mapped_column(Text, nullable=False)
    # Samples: page, block and sentence ids, source and target text, finding keys.
    evidence_json: Mapped[list[dict[str, Any]]] = mapped_column(JsonDocument, nullable=False, default=list)
    # How much of the pilot it touched: findings, pages, chapters.
    impact_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)
    # The book's workaround: the tool, whether it was kept, the attempts.
    workaround_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="open")


class PilotRepairAttempt(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One repair the pilot repair loop tried: what, why, with which tool, and whether it was kept.

    Complete on purpose: the lessons ledger (Q6) learns from these rows which path worked for which
    problem on which kind of book.
    """

    __tablename__ = "pilot_repair_attempts"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('kept', 'reverted', 'failed', 'skipped')", name="ck_pilot_repair_attempts_outcome"
        ),
        CheckConstraint(
            "classification IN ('systemic', 'book_specific')", name="ck_pilot_repair_attempts_classification"
        ),
        Index("idx_pilot_repair_attempts_repair", "repair_id", "ordinal"),
        Index("idx_pilot_repair_attempts_document", "document_id", "check_id"),
    )

    repair_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), ForeignKey("pilot_repairs.id", ondelete="CASCADE"), nullable=False)
    document_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    round: Mapped[int] = mapped_column(Integer, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    check_id: Mapped[str] = mapped_column(Text, nullable=False)
    layer: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    # The findings this attempt repairs (their stable keys).
    finding_keys_json: Mapped[list[str]] = mapped_column(JsonDocument, nullable=False, default=list)
    classification: Mapped[str] = mapped_column(Text, nullable=False)
    # Which rule of §7.1 decided it (1–4), and the generic cause for a systemic one.
    classification_rule: Mapped[int] = mapped_column(Integer, nullable=False)
    classification_reason: Mapped[str] = mapped_column(Text, nullable=False)
    # The toolbox entry (§7.2) and the concrete path within it ("structure_edit" / "split_caption").
    tool: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    params_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)
    # rule (deterministic) | agent (a model chose the parameters)
    decided_by: Mapped[str] = mapped_column(Text, nullable=False, default="rule")
    # What the attempt touched: retranslated packets and changed blocks.
    packet_ids_json: Mapped[list[str]] = mapped_column(JsonDocument, nullable=False, default=list)
    block_ids_json: Mapped[list[str]] = mapped_column(JsonDocument, nullable=False, default=list)
    # Metrics before and after: severity counts, per-check counts, which target keys were present.
    before_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)
    after_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)
    resolved_keys_json: Mapped[list[str]] = mapped_column(JsonDocument, nullable=False, default=list)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    # Why it was reverted, failed or skipped ("not_improved", "worse:L6.negation_lost", an error).
    reason: Mapped[str | None] = mapped_column(Text)
    ticket_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("pilot_defect_tickets.id", ondelete="SET NULL")
    )
    token_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
