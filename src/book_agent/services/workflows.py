from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any

from sqlalchemy import and_, case, distinct, func, or_, select
from sqlalchemy.orm import Session

from book_agent.core.ids import stable_id
from book_agent.domain.document_titles import document_display_title, document_source_title
from book_agent.domain.enums import (
    ActionType,
    ActorType,
    DocumentRunStatus,
    DocumentStatus,
    ExportStatus,
    ExportType,
    IssueStatus,
    JobScopeType,
    PacketStatus,
    SourceType,
)
from book_agent.domain.models import Chapter, ChapterWorklistAssignment, Document, Sentence
from book_agent.domain.models.ops import AuditEvent, DocumentRun, RunAuditEvent
from book_agent.domain.models.review import (
    ChapterQualitySummary as PersistedChapterQualitySummary,
    Export,
    IssueAction,
    ReviewIssue,
)
from book_agent.domain.models.translation import TranslationPacket
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.infra.repositories.export import ExportRepository
from book_agent.infra.repositories.ops import OpsRepository
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.infra.repositories.review import ReviewRepository
from book_agent.infra.repositories.translation import TranslationRepository
from book_agent.orchestrator.bootstrap import BootstrapOrchestrator
from book_agent.orchestrator.rerun import build_rerun_plan, packet_scope_ids_for_issue
from book_agent.services.actions import ActionExecutionArtifacts, IssueActionExecutor
from book_agent.services.bootstrap import BootstrapArtifacts
from book_agent.services.chapter_concept_autolock import ChapterConceptAutoLockService, build_default_concept_resolver
from book_agent.services.export import ExportArtifacts, ExportGateError, ExportService
from book_agent.services.export_routing import ExportRoutingService
from book_agent.services.epub_structure_refresh import EpubStructureRefreshArtifacts, EpubStructureRefreshService
from book_agent.services.pdf_structure_refresh import PdfStructureRefreshArtifacts, PdfStructureRefreshService
from book_agent.services.realign import RealignService
from book_agent.services.rebuild import TargetedRebuildService
from book_agent.services.rerun import RerunExecutionArtifacts, RerunService
from book_agent.services.review import NaturalnessSummary as ReviewNaturalnessSummary, ReviewArtifacts, ReviewService
from book_agent.services.runtime_bundle import RuntimeBundleService
from book_agent.services.translation import TranslationExecutionArtifacts, TranslationService
from book_agent.workers.translator import TranslationWorker


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


MAX_SAFE_UNLOCKED_CONCEPT_PACKET_FOLLOWUP = 3
MAX_SAFE_STALE_CHAPTER_BRIEF_PACKET_FOLLOWUP = 3
AUTO_FOLLOWUP_REPEAT_FAILURE_LIMIT = 2
AUTO_FOLLOWUP_EXECUTION_AUDIT_ACTIONS = {
    "review.auto_followup.executed",
    "document.blocker_repair.executed",
    "export.auto_followup.executed",
}


@dataclass(slots=True)
class ChapterSummary:
    chapter_id: str
    ordinal: int
    title_src: str | None
    status: str
    risk_level: str | None
    parse_confidence: float | None
    structure_flags: list[str]
    sentence_count: int
    packet_count: int
    open_issue_count: int
    bilingual_export_ready: bool = False
    latest_bilingual_export_at: str | None = None
    pdf_image_summary: dict[str, Any] | None = None
    quality_summary: "StoredChapterQualitySummary | None" = None


@dataclass(slots=True)
class StoredChapterQualitySummary:
    issue_count: int
    action_count: int
    resolved_issue_count: int
    coverage_ok: bool
    alignment_ok: bool
    term_ok: bool
    format_ok: bool
    blocking_issue_count: int
    low_confidence_count: int
    format_pollution_count: int


@dataclass(slots=True)
class NaturalnessSummarySnapshot:
    advisory_only: bool
    style_drift_issue_count: int
    affected_packet_count: int
    dominant_style_rules: list[str]
    preferred_hints: list[str]


@dataclass(slots=True)
class DocumentSummary:
    document_id: str
    source_type: str
    status: str
    title: str | None
    title_src: str | None
    title_tgt: str | None
    author: str | None
    pdf_profile: dict[str, Any] | None
    pdf_page_evidence: dict[str, Any] | None
    pdf_image_summary: dict[str, Any] | None
    chapter_count: int
    block_count: int
    sentence_count: int
    packet_count: int
    open_issue_count: int
    merged_export_ready: bool
    latest_merged_export_at: str | None
    chapter_bilingual_export_count: int
    latest_run_id: str | None
    latest_run_status: str | None
    latest_run_current_stage: str | None
    latest_run_updated_at: str | None
    runtime_v2_context: dict[str, Any] | None = None
    chapters: list[ChapterSummary] = field(default_factory=list)


@dataclass(slots=True)
class DocumentHistoryEntry:
    document_id: str
    source_type: str
    status: str
    title: str | None
    title_src: str | None
    title_tgt: str | None
    author: str | None
    source_path: str | None
    created_at: str
    updated_at: str
    chapter_count: int
    sentence_count: int
    packet_count: int
    merged_export_ready: bool
    latest_merged_export_at: str | None
    chapter_bilingual_export_count: int
    latest_run_id: str | None
    latest_run_status: str | None
    latest_run_current_stage: str | None
    latest_run_completed_work_item_count: int | None
    latest_run_total_work_item_count: int | None


@dataclass(slots=True)
class DocumentHistoryPage:
    total_count: int
    record_count: int
    offset: int
    limit: int | None
    has_more: bool
    entries: list[DocumentHistoryEntry]


@dataclass(slots=True)
class DocumentTranslationResult:
    document_id: str
    translated_packet_count: int
    skipped_packet_ids: list[str]
    translation_run_ids: list[str]
    review_required_sentence_ids: list[str]


def _display_author_value(author: str | None) -> str | None:
    normalized = re.sub(r"\s+", " ", (author or "")).strip()
    if not normalized:
        return None
    lowered = normalized.casefold()
    if "/" in lowered or "\\" in lowered:
        return None
    if lowered.endswith((".html", ".xhtml", ".htm", ".xml", ".opf", ".ncx")):
        return None
    return normalized


def _history_run_progress(run: DocumentRun | None) -> tuple[str | None, int | None, int | None]:
    if run is None:
        return None, None, None
    detail = dict(run.status_detail_json or {})
    pipeline = dict(detail.get("pipeline") or {})
    stages = dict(pipeline.get("stages") or {})
    translate = dict(stages.get("translate") or {})
    counters = dict(detail.get("control_counters") or {})
    current_stage = pipeline.get("current_stage")

    completed_raw = counters.get("completed_work_item_count")
    total_raw = translate.get("total_packet_count", counters.get("seeded_work_item_count"))

    try:
        completed = int(completed_raw) if completed_raw is not None else None
    except (TypeError, ValueError):
        completed = None
    try:
        total = int(total_raw) if total_raw is not None else None
    except (TypeError, ValueError):
        total = None
    return (
        str(current_stage) if current_stage is not None else None,
        completed,
        total,
    )


@dataclass(slots=True)
class ChapterReviewResult:
    chapter_id: str
    status: str
    issue_count: int
    action_count: int
    blocking_issue_count: int
    coverage_ok: bool
    alignment_ok: bool
    term_ok: bool
    format_ok: bool
    low_confidence_count: int
    format_pollution_count: int
    resolved_issue_count: int
    naturalness_summary: NaturalnessSummarySnapshot | None = None


@dataclass(slots=True)
class DocumentReviewResult:
    document_id: str
    total_issue_count: int
    total_action_count: int
    chapter_results: list[ChapterReviewResult]
    auto_followup_requested: bool = False
    auto_followup_applied: bool = False
    auto_followup_attempt_count: int = 0
    auto_followup_attempt_limit: int | None = None
    auto_followup_executions: list["ReviewAutoFollowupExecution"] | None = None


@dataclass(slots=True)
class DocumentBlockerRepairExecution:
    action_id: str
    issue_id: str
    issue_type: str
    action_type: str
    rerun_scope_type: str
    rerun_scope_ids: list[str]
    followup_executed: bool
    rerun_packet_ids: list[str]
    rerun_translation_run_ids: list[str]
    issue_resolved: bool | None


@dataclass(slots=True)
class DocumentBlockerRepairResult:
    document_id: str
    blocking_issue_count_before: int
    blocking_issue_count_after: int
    requested: bool
    applied: bool
    round_count: int
    round_limit: int
    executions: list[DocumentBlockerRepairExecution]
    stop_reason: str | None = None


@dataclass(slots=True)
class ReviewAutoFollowupExecution:
    action_id: str
    issue_id: str
    issue_type: str
    action_type: str
    rerun_scope_type: str
    rerun_scope_ids: list[str]
    followup_executed: bool
    rerun_packet_ids: list[str]
    rerun_translation_run_ids: list[str]
    issue_resolved: bool | None


@dataclass(slots=True)
class ChapterExportResult:
    chapter_id: str | None
    export_id: str
    export_type: str
    status: str
    file_path: str
    manifest_path: str | None = None


@dataclass(slots=True)
class DocumentExportResult:
    document_id: str
    export_type: str
    document_status: str
    chapter_results: list[ChapterExportResult]
    file_path: str | None = None
    manifest_path: str | None = None
    auto_followup_requested: bool = False
    auto_followup_applied: bool = False
    auto_followup_attempt_count: int = 0
    auto_followup_attempt_limit: int | None = None
    auto_followup_executions: list["ExportAutoFollowupExecution"] | None = None
    route_evidence_json: dict[str, Any] | None = None
    runtime_v2_context: dict[str, Any] | None = None


@dataclass(slots=True)
class ExportAutoFollowupSummary:
    event_count: int
    executed_event_count: int
    stop_event_count: int
    latest_event_at: str | None
    last_stop_reason: str | None


@dataclass(slots=True)
class ExportMisalignmentCountSummary:
    missing_target_sentence_count: int
    inactive_only_sentence_count: int
    orphan_target_segment_count: int
    inactive_target_segment_with_edges_count: int


@dataclass(slots=True)
class TranslationUsageSummary:
    run_count: int
    succeeded_run_count: int
    total_token_in: int
    total_token_out: int
    total_cost_usd: float
    total_latency_ms: int
    avg_latency_ms: float | None
    latest_run_at: str | None


@dataclass(slots=True)
class TranslationUsageBreakdownEntry:
    model_name: str
    worker_name: str | None
    provider: str | None
    run_count: int
    succeeded_run_count: int
    total_token_in: int
    total_token_out: int
    total_cost_usd: float
    total_latency_ms: int
    avg_latency_ms: float | None
    latest_run_at: str | None


@dataclass(slots=True)
class TranslationUsageTimelineEntry:
    bucket_start: str
    bucket_granularity: str
    run_count: int
    succeeded_run_count: int
    total_token_in: int
    total_token_out: int
    total_cost_usd: float
    total_latency_ms: int
    avg_latency_ms: float | None


@dataclass(slots=True)
class TranslationUsageHighlights:
    top_cost_entry: TranslationUsageBreakdownEntry | None
    top_latency_entry: TranslationUsageBreakdownEntry | None
    top_volume_entry: TranslationUsageBreakdownEntry | None


@dataclass(slots=True)
class IssueHotspotEntry:
    issue_type: str
    root_cause_layer: str
    issue_count: int
    open_issue_count: int
    triaged_issue_count: int
    resolved_issue_count: int
    wontfix_issue_count: int
    blocking_issue_count: int
    chapter_count: int
    latest_seen_at: str | None


@dataclass(slots=True)
class IssueChapterPressureEntry:
    chapter_id: str
    ordinal: int
    title_src: str | None
    chapter_status: str
    issue_count: int
    open_issue_count: int
    triaged_issue_count: int
    resolved_issue_count: int
    blocking_issue_count: int
    latest_issue_at: str | None


@dataclass(slots=True)
class IssueChapterHighlights:
    top_open_chapter: IssueChapterPressureEntry | None
    top_blocking_chapter: IssueChapterPressureEntry | None
    top_resolved_chapter: IssueChapterPressureEntry | None


@dataclass(slots=True)
class IssueChapterBreakdownEntry:
    chapter_id: str
    ordinal: int
    title_src: str | None
    chapter_status: str
    issue_type: str
    root_cause_layer: str
    issue_count: int
    open_issue_count: int
    triaged_issue_count: int
    resolved_issue_count: int
    blocking_issue_count: int
    active_blocking_issue_count: int
    latest_seen_at: str | None


@dataclass(slots=True)
class IssueChapterHeatmapEntry:
    chapter_id: str
    ordinal: int
    title_src: str | None
    chapter_status: str
    issue_count: int
    open_issue_count: int
    triaged_issue_count: int
    resolved_issue_count: int
    blocking_issue_count: int
    active_blocking_issue_count: int
    issue_family_count: int
    dominant_issue_type: str | None
    dominant_root_cause_layer: str | None
    dominant_issue_count: int
    latest_issue_at: str | None
    heat_score: int
    heat_level: str


@dataclass(slots=True)
class IssueChapterQueueEntry:
    chapter_id: str
    ordinal: int
    title_src: str | None
    chapter_status: str
    issue_count: int
    open_issue_count: int
    triaged_issue_count: int
    blocking_issue_count: int
    active_blocking_issue_count: int
    issue_family_count: int
    dominant_issue_type: str | None
    dominant_root_cause_layer: str | None
    dominant_issue_count: int
    latest_issue_at: str | None
    heat_score: int
    heat_level: str
    queue_rank: int
    queue_priority: str
    queue_driver: str
    needs_immediate_attention: bool
    oldest_active_issue_at: str | None
    age_hours: int | None
    age_bucket: str
    sla_target_hours: int | None
    sla_status: str
    owner_ready: bool
    owner_ready_reason: str
    is_assigned: bool
    assigned_owner_name: str | None
    assigned_at: str | None
    latest_activity_bucket_start: str | None
    latest_created_issue_count: int
    latest_resolved_issue_count: int
    latest_net_issue_delta: int
    regression_hint: str
    flapping_hint: bool


@dataclass(slots=True)
class IssueActivityTimelineEntry:
    bucket_start: str
    bucket_granularity: str
    created_issue_count: int
    resolved_issue_count: int
    wontfix_issue_count: int
    blocking_created_issue_count: int
    net_issue_delta: int
    estimated_open_issue_count: int


@dataclass(slots=True)
class IssueActivityBreakdownEntry:
    issue_type: str
    root_cause_layer: str
    issue_count: int
    open_issue_count: int
    blocking_issue_count: int
    latest_seen_at: str | None
    timeline: list[IssueActivityTimelineEntry]


@dataclass(slots=True)
class IssueActivityHighlights:
    top_regressing_entry: IssueActivityBreakdownEntry | None
    top_resolving_entry: IssueActivityBreakdownEntry | None
    top_blocking_entry: IssueActivityBreakdownEntry | None


@dataclass(slots=True)
class ExportIssueStatusSummary:
    issue_count: int
    open_issue_count: int
    resolved_issue_count: int
    blocking_issue_count: int


@dataclass(slots=True)
class ExportVersionEvidenceSummary:
    document_parser_version: int | None
    document_segmentation_version: int | None
    book_profile_version: int | None
    chapter_summary_version: int | None
    active_snapshot_versions: dict[str, int]


@dataclass(slots=True)
class ExportRecordSummary:
    export_id: str
    export_type: str
    status: str
    file_path: str
    manifest_path: str | None
    chapter_id: str | None
    chapter_summary_version: int | None
    created_at: str
    updated_at: str
    translation_usage_summary: TranslationUsageSummary | None = None
    translation_usage_breakdown: list[TranslationUsageBreakdownEntry] | None = None
    translation_usage_timeline: list[TranslationUsageTimelineEntry] | None = None
    translation_usage_highlights: TranslationUsageHighlights | None = None
    export_auto_followup_summary: ExportAutoFollowupSummary | None = None
    export_time_misalignment_counts: ExportMisalignmentCountSummary | None = None


@dataclass(slots=True)
class DocumentExportDashboard:
    document_id: str
    export_count: int
    successful_export_count: int
    filtered_export_count: int
    record_count: int
    offset: int
    limit: int | None
    has_more: bool
    applied_export_type_filter: str | None
    applied_status_filter: str | None
    latest_export_at: str | None
    export_counts_by_type: dict[str, int]
    latest_export_ids_by_type: dict[str, str]
    total_auto_followup_executed_count: int
    translation_usage_summary: TranslationUsageSummary | None
    translation_usage_breakdown: list[TranslationUsageBreakdownEntry]
    translation_usage_timeline: list[TranslationUsageTimelineEntry]
    translation_usage_highlights: TranslationUsageHighlights
    issue_hotspots: list[IssueHotspotEntry]
    issue_chapter_pressure: list[IssueChapterPressureEntry]
    issue_chapter_highlights: IssueChapterHighlights
    issue_chapter_breakdown: list[IssueChapterBreakdownEntry]
    issue_chapter_heatmap: list[IssueChapterHeatmapEntry]
    issue_chapter_queue: list[IssueChapterQueueEntry]
    issue_activity_timeline: list[IssueActivityTimelineEntry]
    issue_activity_breakdown: list[IssueActivityBreakdownEntry]
    issue_activity_highlights: IssueActivityHighlights
    records: list[ExportRecordSummary]


@dataclass(slots=True)
class DocumentChapterWorklist:
    document_id: str
    worklist_count: int
    filtered_worklist_count: int
    entry_count: int
    offset: int
    limit: int | None
    has_more: bool
    applied_queue_priority_filter: str | None
    applied_sla_status_filter: str | None
    applied_owner_ready_filter: bool | None
    applied_needs_immediate_attention_filter: bool | None
    applied_assigned_filter: bool | None
    applied_assigned_owner_filter: str | None
    queue_priority_counts: dict[str, int]
    sla_status_counts: dict[str, int]
    immediate_attention_count: int
    owner_ready_count: int
    assigned_count: int
    owner_workload_summary: list["ChapterOwnerWorkloadSummary"]
    owner_workload_highlights: dict[str, "ChapterOwnerWorkloadSummary | None"]
    highlights: dict[str, IssueChapterQueueEntry | None]
    entries: list[IssueChapterQueueEntry]


@dataclass(slots=True)
class ChapterWorklistAssignmentSummary:
    assignment_id: str
    document_id: str
    chapter_id: str
    owner_name: str
    assigned_by: str
    note: str | None
    assigned_at: str
    created_at: str
    updated_at: str


@dataclass(slots=True)
class ChapterOwnerWorkloadSummary:
    owner_name: str
    assigned_chapter_count: int
    immediate_count: int
    high_count: int
    medium_count: int
    breached_count: int
    due_soon_count: int
    on_track_count: int
    owner_ready_count: int
    total_open_issue_count: int
    total_active_blocking_issue_count: int
    oldest_active_issue_at: str | None
    latest_issue_at: str | None


@dataclass(slots=True)
class ChapterWorklistIssue:
    issue_id: str
    issue_type: str
    root_cause_layer: str
    severity: str
    status: str
    blocking: bool
    detector: str
    suggested_action: str | None
    created_at: str
    updated_at: str


@dataclass(slots=True)
class ChapterWorklistAction:
    action_id: str
    issue_id: str
    issue_type: str
    action_type: str
    scope_type: str
    scope_id: str | None
    status: str
    created_by: str
    created_at: str
    updated_at: str


@dataclass(slots=True)
class ChapterWorklistAssignmentHistoryEntry:
    event_id: str
    event_type: str
    owner_name: str | None
    performed_by: str | None
    note: str | None
    created_at: str


@dataclass(slots=True)
class DocumentChapterWorklistDetail:
    document_id: str
    chapter_id: str
    ordinal: int
    title_src: str | None
    chapter_status: str
    packet_count: int
    translated_packet_count: int
    current_issue_count: int
    current_open_issue_count: int
    current_triaged_issue_count: int
    current_active_blocking_issue_count: int
    assignment: ChapterWorklistAssignmentSummary | None
    queue_entry: IssueChapterQueueEntry | None
    quality_summary: StoredChapterQualitySummary | None
    issue_family_breakdown: list[IssueChapterBreakdownEntry]
    recent_issues: list[ChapterWorklistIssue]
    recent_actions: list[ChapterWorklistAction]
    assignment_history: list[ChapterWorklistAssignmentHistoryEntry]


@dataclass(slots=True)
class ExportDetail:
    document_id: str
    export_id: str
    export_type: str
    status: str
    file_path: str
    manifest_path: str | None
    chapter_id: str | None
    sentence_count: int
    target_segment_count: int
    created_at: str
    updated_at: str
    translation_usage_summary: TranslationUsageSummary | None
    translation_usage_breakdown: list[TranslationUsageBreakdownEntry] | None
    translation_usage_timeline: list[TranslationUsageTimelineEntry] | None
    translation_usage_highlights: TranslationUsageHighlights | None
    issue_status_summary: ExportIssueStatusSummary | None
    export_auto_followup_summary: ExportAutoFollowupSummary | None
    export_time_misalignment_counts: ExportMisalignmentCountSummary | None
    version_evidence_summary: ExportVersionEvidenceSummary
    runtime_v2_context: dict[str, Any] | None = None


@dataclass(slots=True)
class ExportAutoFollowupExecution:
    action_id: str
    issue_id: str
    action_type: str
    rerun_scope_type: str
    rerun_scope_ids: list[str]
    followup_executed: bool
    rerun_packet_ids: list[str]
    rerun_translation_run_ids: list[str]
    issue_resolved: bool | None

    def to_export_gate_payload(self) -> dict:
        return {
            "action_id": self.action_id,
            "issue_id": self.issue_id,
            "action_type": self.action_type,
            "rerun_scope_type": self.rerun_scope_type,
            "rerun_scope_ids": self.rerun_scope_ids,
            "followup_executed": self.followup_executed,
            "rerun_packet_ids": self.rerun_packet_ids,
            "rerun_translation_run_ids": self.rerun_translation_run_ids,
            "issue_resolved": self.issue_resolved,
        }


@dataclass(slots=True)
class ActionWorkflowResult:
    action_execution: ActionExecutionArtifacts
    rerun_execution: RerunExecutionArtifacts | None = None


class DocumentWorkflowService:
    def __init__(
        self,
        session: Session,
        export_root: str | Path = "artifacts/exports",
        translation_worker: TranslationWorker | None = None,
    ):
        self.session = session
        self.bootstrap_repository = BootstrapRepository(session)
        self.review_repository = ReviewRepository(session)
        self.export_repository = ExportRepository(session)
        self.run_control_repository = RunControlRepository(session)
        self.runtime_bundle_service = RuntimeBundleService(session)
        self.export_routing_service = ExportRoutingService(
            runtime_bundle_service=self.runtime_bundle_service
        )
        self.translation_service = TranslationService(
            TranslationRepository(session),
            worker=translation_worker,
        )
        self.review_service = ReviewService(self.review_repository)
        self.export_service = ExportService(
            self.export_repository,
            output_root=export_root,
            runtime_bundle_service=self.runtime_bundle_service,
            export_routing_service=self.export_routing_service,
        )
        self.ops_repository = OpsRepository(session)
        self.action_executor = IssueActionExecutor(self.ops_repository)
        self.targeted_rebuild_service = TargetedRebuildService(
            session,
            self.bootstrap_repository,
        )
        self.pdf_structure_refresh_service = PdfStructureRefreshService(
            session,
            self.bootstrap_repository,
        )
        self.epub_structure_refresh_service = EpubStructureRefreshService(
            session,
            self.bootstrap_repository,
        )
        self.rerun_service = RerunService(
            self.ops_repository,
            self.translation_service,
            self.review_service,
            self.targeted_rebuild_service,
            RealignService(self.ops_repository),
            self.pdf_structure_refresh_service,
        )

    def bootstrap_document(self, source_path: str | Path) -> DocumentSummary:
        artifacts: BootstrapArtifacts = BootstrapOrchestrator().bootstrap_document(source_path)
        self.bootstrap_repository.save(artifacts)
        return self.get_document_summary(artifacts.document.id)

    def bootstrap_epub(self, source_path: str | Path) -> DocumentSummary:
        return self.bootstrap_document(source_path)

    def refresh_pdf_structure(
        self,
        document_id: str,
        *,
        chapter_ids: list[str] | None = None,
    ) -> PdfStructureRefreshArtifacts:
        return self.pdf_structure_refresh_service.refresh_document(document_id, chapter_ids=chapter_ids)

    def refresh_epub_structure(
        self,
        document_id: str,
        *,
        chapter_ids: list[str] | None = None,
    ) -> EpubStructureRefreshArtifacts:
        return self.epub_structure_refresh_service.refresh_document(document_id, chapter_ids=chapter_ids)

    def get_document_summary(self, document_id: str) -> DocumentSummary:
        bundle = self.bootstrap_repository.load_document_bundle(document_id)
        open_issue_counts = self._open_issue_counts(document_id)
        quality_summary_map = self.review_repository.load_quality_summaries_for_document(document_id)
        chapter_export_map, merged_export_ready, latest_merged_export_at = self._chapter_export_status_map(document_id)
        latest_run = self._latest_document_run(document_id)
        latest_run_current_stage, _, _ = _history_run_progress(latest_run)
        chapter_pdf_image_summary_map = self._chapter_pdf_image_summary_map(bundle)
        runtime_v2_context = self._runtime_v2_context_for_run(latest_run)

        chapter_summaries: list[ChapterSummary] = []
        block_count = 0
        sentence_count = 0
        packet_count = 0
        for chapter_bundle in bundle.chapters:
            block_count += len(chapter_bundle.blocks)
            sentence_count += len(chapter_bundle.sentences)
            packet_count += len(chapter_bundle.translation_packets)
            chapter_summaries.append(
                ChapterSummary(
                    chapter_id=chapter_bundle.chapter.id,
                    ordinal=chapter_bundle.chapter.ordinal,
                    title_src=chapter_bundle.chapter.title_src,
                    status=chapter_bundle.chapter.status.value,
                    risk_level=chapter_bundle.chapter.risk_level.value if chapter_bundle.chapter.risk_level else None,
                    parse_confidence=self._chapter_parse_confidence(chapter_bundle.chapter),
                    structure_flags=self._chapter_structure_flags(chapter_bundle.chapter),
                    sentence_count=len(chapter_bundle.sentences),
                    packet_count=len(chapter_bundle.translation_packets),
                    open_issue_count=open_issue_counts.get(chapter_bundle.chapter.id, 0),
                    bilingual_export_ready=(chapter_bundle.chapter.id in chapter_export_map),
                    latest_bilingual_export_at=chapter_export_map.get(chapter_bundle.chapter.id),
                    pdf_image_summary=chapter_pdf_image_summary_map.get(chapter_bundle.chapter.id),
                    quality_summary=self._to_stored_quality_summary(
                        quality_summary_map.get(chapter_bundle.chapter.id)
                    ),
                )
            )

        return DocumentSummary(
            document_id=bundle.document.id,
            source_type=bundle.document.source_type.value,
            status=bundle.document.status.value,
            title=document_display_title(bundle.document),
            title_src=document_source_title(bundle.document),
            title_tgt=(bundle.document.title_tgt or None),
            author=_display_author_value(bundle.document.author),
            pdf_profile=bundle.document.metadata_json.get("pdf_profile"),
            pdf_page_evidence=bundle.document.metadata_json.get("pdf_page_evidence"),
            pdf_image_summary=self._document_pdf_image_summary(bundle),
            chapter_count=len(bundle.chapters),
            block_count=block_count,
            sentence_count=sentence_count,
            packet_count=packet_count,
            open_issue_count=sum(open_issue_counts.values()),
            merged_export_ready=merged_export_ready,
            latest_merged_export_at=latest_merged_export_at,
            chapter_bilingual_export_count=len(chapter_export_map),
            latest_run_id=(latest_run.id if latest_run is not None else None),
            latest_run_status=(latest_run.status.value if latest_run is not None else None),
            latest_run_current_stage=latest_run_current_stage,
            latest_run_updated_at=(latest_run.updated_at.isoformat() if latest_run is not None else None),
            runtime_v2_context=runtime_v2_context,
            chapters=chapter_summaries,
        )

    def list_document_history(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        query: str | None = None,
        source_type: SourceType | None = None,
        status: DocumentStatus | None = None,
        latest_run_status: DocumentRunStatus | None = None,
        merged_export_ready: bool | None = None,
    ) -> DocumentHistoryPage:
        statement = select(Document).order_by(Document.updated_at.desc(), Document.id.desc())
        normalized_query = (query or "").strip()
        if source_type is not None:
            statement = statement.where(Document.source_type == source_type)
        if status is not None:
            statement = statement.where(Document.status == status)
        if normalized_query:
            like_pattern = f"%{normalized_query}%"
            statement = statement.where(
                or_(
                    Document.id.ilike(like_pattern),
                    Document.title.ilike(like_pattern),
                    Document.title_src.ilike(like_pattern),
                    Document.title_tgt.ilike(like_pattern),
                    Document.author.ilike(like_pattern),
                    Document.source_path.ilike(like_pattern),
                )
            )
        documents = list(self.session.scalars(statement).all())
        document_ids = [document.id for document in documents]

        chapter_counts = self._chapter_count_map(document_ids)
        sentence_counts = self._sentence_count_map(document_ids)
        packet_counts = self._packet_count_map(document_ids)
        latest_runs = self._latest_run_map(document_ids)
        merged_export_status, chapter_export_counts = self._document_export_history_maps(document_ids)

        entries: list[DocumentHistoryEntry] = []
        for document in documents:
            latest_run = latest_runs.get(document.id)
            current_stage, completed_work_item_count, total_work_item_count = _history_run_progress(latest_run)
            entries.append(
                DocumentHistoryEntry(
                    document_id=document.id,
                    source_type=document.source_type.value,
                    status=document.status.value,
                    title=document_display_title(document),
                    title_src=document_source_title(document),
                    title_tgt=(document.title_tgt or None),
                    author=_display_author_value(document.author),
                    source_path=document.source_path,
                    created_at=document.created_at.isoformat(),
                    updated_at=document.updated_at.isoformat(),
                    chapter_count=chapter_counts.get(document.id, 0),
                    sentence_count=sentence_counts.get(document.id, 0),
                    packet_count=packet_counts.get(document.id, 0),
                    merged_export_ready=bool(merged_export_status.get(document.id, {}).get("ready")),
                    latest_merged_export_at=merged_export_status.get(document.id, {}).get("latest_export_at"),
                    chapter_bilingual_export_count=chapter_export_counts.get(document.id, 0),
                    latest_run_id=(latest_run.id if latest_run is not None else None),
                    latest_run_status=(latest_run.status.value if latest_run is not None else None),
                    latest_run_current_stage=current_stage,
                    latest_run_completed_work_item_count=completed_work_item_count,
                    latest_run_total_work_item_count=total_work_item_count,
                )
            )
        if latest_run_status is not None:
            entries = [
                entry for entry in entries if entry.latest_run_status == latest_run_status.value
            ]
        if merged_export_ready is not None:
            entries = [
                entry for entry in entries if entry.merged_export_ready is merged_export_ready
            ]

        total_count = len(entries)
        if offset:
            entries = entries[offset:]
        if limit is not None:
            entries = entries[:limit]
        record_count = len(entries)
        return DocumentHistoryPage(
            total_count=total_count,
            record_count=record_count,
            offset=offset,
            limit=limit,
            has_more=(offset + record_count) < total_count,
            entries=entries,
        )

    def _chapter_parse_confidence(self, chapter: Chapter) -> float | None:
        value = (chapter.metadata_json or {}).get("parse_confidence")
        if value is None:
            return None
        return float(value)

    def _chapter_structure_flags(self, chapter: Chapter) -> list[str]:
        flags = (chapter.metadata_json or {}).get("structure_flags") or []
        return [str(flag) for flag in flags]

    def _pdf_image_summary_payload(self, images: list[object]) -> dict[str, Any] | None:
        if not images:
            return None

        image_type_counts: dict[str, int] = {}
        page_numbers: set[int] = set()
        image_count = 0
        stored_asset_count = 0
        caption_linked_count = 0

        for image in images:
            image_count += 1
            page_numbers.add(int(image.page_number))
            image_type_counts[image.image_type] = image_type_counts.get(image.image_type, 0) + 1
            if (image.metadata_json or {}).get("linked_caption_block_id"):
                caption_linked_count += 1

            storage_path = str(image.storage_path or "")
            if storage_path and Path(storage_path).is_file():
                stored_asset_count += 1

        return {
            "schema_version": 1,
            "image_count": image_count,
            "page_count": len(page_numbers),
            "page_numbers": sorted(page_numbers),
            "stored_asset_count": stored_asset_count,
            "caption_linked_count": caption_linked_count,
            "uncaptioned_image_count": image_count - caption_linked_count,
            "image_type_counts": image_type_counts,
        }

    def _chapter_pdf_image_summary_map(self, bundle) -> dict[str, dict[str, Any]]:
        if not bundle.document_images:
            return {}

        block_id_to_chapter_id = {
            block.id: chapter_bundle.chapter.id
            for chapter_bundle in bundle.chapters
            for block in chapter_bundle.blocks
        }
        images_by_chapter: dict[str, list[object]] = {}
        for image in bundle.document_images:
            chapter_id = block_id_to_chapter_id.get(image.block_id or "")
            if chapter_id is None:
                continue
            images_by_chapter.setdefault(chapter_id, []).append(image)
        return {
            chapter_id: summary
            for chapter_id, summary in (
                (chapter_id, self._pdf_image_summary_payload(images))
                for chapter_id, images in images_by_chapter.items()
            )
            if summary is not None
        }

    def _document_pdf_image_summary(self, bundle) -> dict[str, Any] | None:
        summary = self._pdf_image_summary_payload(bundle.document_images)
        if summary is None:
            return None

        block_id_to_chapter_id = {
            block.id: chapter_bundle.chapter.id
            for chapter_bundle in bundle.chapters
            for block in chapter_bundle.blocks
        }
        chapter_image_counts: dict[str, int] = {}
        unassigned_image_count = 0

        for image in bundle.document_images:
            chapter_id = block_id_to_chapter_id.get(image.block_id or "")
            if chapter_id is None:
                unassigned_image_count += 1
            else:
                chapter_image_counts[chapter_id] = chapter_image_counts.get(chapter_id, 0) + 1
        return summary | {
            "unassigned_image_count": unassigned_image_count,
            "chapter_image_counts": chapter_image_counts,
        }

    def _latest_document_run(self, document_id: str) -> DocumentRun | None:
        return self.session.scalars(
            select(DocumentRun)
            .where(DocumentRun.document_id == document_id)
            .order_by(DocumentRun.created_at.desc(), DocumentRun.id.desc())
        ).first()

    def _chapter_export_status_map(self, document_id: str) -> tuple[dict[str, str], bool, str | None]:
        exports = self.export_repository.list_document_exports_filtered(
            document_id,
            status=ExportStatus.SUCCEEDED,
        )
        chapter_export_map: dict[str, str] = {}
        merged_export_ready = False
        latest_merged_export_at: str | None = None
        for export in exports:
            bundle = export.input_version_bundle_json or {}
            if export.export_type == ExportType.MERGED_HTML and not merged_export_ready:
                merged_export_ready = True
                latest_merged_export_at = export.created_at.isoformat()
            if export.export_type != ExportType.BILINGUAL_HTML:
                continue
            chapter_id = bundle.get("chapter_id")
            if not chapter_id or chapter_id in chapter_export_map:
                continue
            chapter_export_map[str(chapter_id)] = export.created_at.isoformat()
        return chapter_export_map, merged_export_ready, latest_merged_export_at

    def _chapter_count_map(self, document_ids: list[str]) -> dict[str, int]:
        if not document_ids:
            return {}
        rows = self.session.execute(
            select(Chapter.document_id, func.count(Chapter.id))
            .where(Chapter.document_id.in_(document_ids))
            .group_by(Chapter.document_id)
        ).all()
        return {str(document_id): int(count) for document_id, count in rows}

    def _sentence_count_map(self, document_ids: list[str]) -> dict[str, int]:
        if not document_ids:
            return {}
        rows = self.session.execute(
            select(Sentence.document_id, func.count(Sentence.id))
            .where(Sentence.document_id.in_(document_ids))
            .group_by(Sentence.document_id)
        ).all()
        return {str(document_id): int(count) for document_id, count in rows}

    def _packet_count_map(self, document_ids: list[str]) -> dict[str, int]:
        if not document_ids:
            return {}
        rows = self.session.execute(
            select(Chapter.document_id, func.count(TranslationPacket.id))
            .join(Chapter, Chapter.id == TranslationPacket.chapter_id)
            .where(Chapter.document_id.in_(document_ids))
            .group_by(Chapter.document_id)
        ).all()
        return {str(document_id): int(count) for document_id, count in rows}

    def _latest_run_map(self, document_ids: list[str]) -> dict[str, DocumentRun]:
        if not document_ids:
            return {}
        runs = self.session.scalars(
            select(DocumentRun)
            .where(DocumentRun.document_id.in_(document_ids))
            .order_by(DocumentRun.updated_at.desc(), DocumentRun.created_at.desc(), DocumentRun.id.desc())
        ).all()
        latest_runs: dict[str, DocumentRun] = {}
        for run in runs:
            latest_runs.setdefault(run.document_id, run)
        return latest_runs

    def _document_export_history_maps(
        self,
        document_ids: list[str],
    ) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
        if not document_ids:
            return {}, {}
        exports = self.session.scalars(
            select(Export)
            .where(
                Export.document_id.in_(document_ids),
                Export.status == ExportStatus.SUCCEEDED,
            )
            .order_by(Export.created_at.desc(), Export.id.desc())
        ).all()
        merged_export_status: dict[str, dict[str, Any]] = {}
        chapter_export_counts: dict[str, set[str]] = {}
        for export in exports:
            document_id = export.document_id
            if export.export_type == ExportType.MERGED_HTML:
                merged_export_status.setdefault(
                    document_id,
                    {
                        "ready": True,
                        "latest_export_at": export.created_at.isoformat(),
                    },
                )
                continue
            if export.export_type != ExportType.BILINGUAL_HTML:
                continue
            chapter_id = (export.input_version_bundle_json or {}).get("chapter_id")
            if chapter_id is None:
                continue
            chapter_export_counts.setdefault(document_id, set()).add(str(chapter_id))
        return (
            merged_export_status,
            {document_id: len(chapter_ids) for document_id, chapter_ids in chapter_export_counts.items()},
        )

    def translate_document(self, document_id: str, packet_ids: list[str] | None = None) -> DocumentTranslationResult:
        bundle = self.bootstrap_repository.load_document_bundle(document_id)
        requested_packet_ids = set(packet_ids or [])
        translated_packet_count = 0
        translation_run_ids: list[str] = []
        review_required_sentence_ids: list[str] = []
        skipped_packet_ids: list[str] = []

        packets = [
            packet
            for chapter_bundle in bundle.chapters
            for packet in chapter_bundle.translation_packets
            if not requested_packet_ids or packet.id in requested_packet_ids
        ]
        for packet in packets:
            if packet.status != PacketStatus.BUILT:
                skipped_packet_ids.append(packet.id)
                continue
            artifacts: TranslationExecutionArtifacts = self.translation_service.execute_packet(packet.id)
            translated_packet_count += 1
            translation_run_ids.append(artifacts.translation_run.id)
            review_required_sentence_ids.extend(
                sentence.id for sentence in artifacts.updated_sentences if sentence.sentence_status.value == "review_required"
            )

        return DocumentTranslationResult(
            document_id=document_id,
            translated_packet_count=translated_packet_count,
            skipped_packet_ids=skipped_packet_ids,
            translation_run_ids=translation_run_ids,
            review_required_sentence_ids=review_required_sentence_ids,
        )

    def _review_document_impl(
        self,
        bundle,
        *,
        chapter_results: list[ChapterReviewResult],
        total_issue_count: int,
        total_action_count: int,
        auto_execute_packet_followups: bool,
        max_auto_followup_attempts: int,
    ) -> DocumentReviewResult:
        auto_followup_executions: list[ReviewAutoFollowupExecution] = []
        attempted_action_ids: set[str] = set()

        for chapter_bundle in bundle.chapters:
            if chapter_bundle.translation_packets and not all(
                packet.status == PacketStatus.TRANSLATED for packet in chapter_bundle.translation_packets
            ):
                continue

            artifacts: ReviewArtifacts = self.review_service.review_chapter(chapter_bundle.chapter.id)
            if auto_execute_packet_followups:
                artifacts = self._apply_review_auto_followups(
                    chapter_id=chapter_bundle.chapter.id,
                    artifacts=artifacts,
                    attempted_action_ids=attempted_action_ids,
                    executions=auto_followup_executions,
                    attempt_limit=max_auto_followup_attempts,
                )
            total_issue_count += len(artifacts.issues)
            total_action_count += len(artifacts.actions)
            chapter = self.session.get(Chapter, chapter_bundle.chapter.id)
            chapter_results.append(
                ChapterReviewResult(
                    chapter_id=chapter_bundle.chapter.id,
                    status=(chapter.status.value if chapter is not None else chapter_bundle.chapter.status.value),
                    issue_count=len(artifacts.issues),
                    action_count=len(artifacts.actions),
                    blocking_issue_count=artifacts.summary.blocking_issue_count,
                    coverage_ok=artifacts.summary.coverage_ok,
                    alignment_ok=artifacts.summary.alignment_ok,
                    term_ok=artifacts.summary.term_ok,
                    format_ok=artifacts.summary.format_ok,
                    low_confidence_count=artifacts.summary.low_confidence_count,
                    format_pollution_count=artifacts.summary.format_pollution_count,
                    resolved_issue_count=len(artifacts.resolved_issue_ids),
                    naturalness_summary=self._to_naturalness_summary(artifacts.summary.naturalness_summary),
                )
            )

        return DocumentReviewResult(
            document_id=bundle.document.id,
            total_issue_count=total_issue_count,
            total_action_count=total_action_count,
            chapter_results=chapter_results,
            auto_followup_requested=auto_execute_packet_followups,
            auto_followup_applied=bool(auto_followup_executions),
            auto_followup_attempt_count=len(auto_followup_executions),
            auto_followup_attempt_limit=(max_auto_followup_attempts if auto_execute_packet_followups else None),
            auto_followup_executions=auto_followup_executions,
        )

    def review_document(
        self,
        document_id: str,
        *,
        auto_execute_packet_followups: bool = False,
        max_auto_followup_attempts: int = 2,
    ) -> DocumentReviewResult:
        bundle = self.bootstrap_repository.load_document_bundle(document_id)
        return self._review_document_impl(
            bundle,
            chapter_results=[],
            total_issue_count=0,
            total_action_count=0,
            auto_execute_packet_followups=auto_execute_packet_followups,
            max_auto_followup_attempts=max_auto_followup_attempts,
        )

    def repair_document_blockers_until_exportable(
        self,
        document_id: str,
        *,
        max_rounds: int = 4,
        max_actions_per_round: int = 64,
    ) -> DocumentBlockerRepairResult:
        round_limit = max(1, int(max_rounds))
        action_limit = max(1, int(max_actions_per_round))
        attempted_action_ids: set[str] = set()
        executions: list[DocumentBlockerRepairExecution] = []

        blocking_issues = self._list_document_active_blocking_issues(document_id)
        blocking_issue_count_before = len(blocking_issues)
        if blocking_issue_count_before == 0:
            return DocumentBlockerRepairResult(
                document_id=document_id,
                blocking_issue_count_before=0,
                blocking_issue_count_after=0,
                requested=False,
                applied=False,
                round_count=0,
                round_limit=round_limit,
                executions=[],
            )

        stop_reason: str | None = None
        round_count = 0

        while round_count < round_limit:
            blocking_issues = self._list_document_active_blocking_issues(document_id)
            if not blocking_issues:
                break
            candidate_actions = self._document_blocker_candidate_actions(
                issues=blocking_issues,
                attempted_action_ids=attempted_action_ids,
            )
            if not candidate_actions:
                stop_reason = "no_new_actions"
                break
            issue_by_id = {issue.id: issue for issue in blocking_issues}
            candidate_actions, blocked_actions = self._split_auto_followup_actions_by_manual_hold(
                issue_by_id=issue_by_id,
                actions=candidate_actions,
            )
            if not candidate_actions:
                stop_reason = "manual_hold_required"
                self._record_document_blocker_repair_stop(
                    document_id=document_id,
                    executions=executions,
                    round_limit=round_limit,
                    stop_reason=stop_reason,
                    issue_ids=[action.issue_id for action in blocked_actions],
                    followup_action_ids=[
                        str(getattr(action, "id", None) or getattr(action, "action_id", None) or "")
                        for action in blocked_actions
                    ],
                )
                break

            round_count += 1
            for action in candidate_actions[:action_limit]:
                issue = issue_by_id.get(action.issue_id)
                attempted_action_ids.add(action.id)
                result = self.execute_action(action.id, run_followup=True)
                rerun_execution = result.rerun_execution
                executions.append(
                    DocumentBlockerRepairExecution(
                        action_id=action.id,
                        issue_id=action.issue_id,
                        issue_type=(issue.issue_type if issue is not None else "unknown"),
                        action_type=action.action_type.value,
                        rerun_scope_type=result.action_execution.rerun_plan.scope_type.value,
                        rerun_scope_ids=result.action_execution.rerun_plan.scope_ids,
                        followup_executed=rerun_execution is not None,
                        rerun_packet_ids=(
                            rerun_execution.translated_packet_ids if rerun_execution is not None else []
                        ),
                        rerun_translation_run_ids=(
                            rerun_execution.translation_run_ids if rerun_execution is not None else []
                        ),
                        issue_resolved=(
                            rerun_execution.issue_resolved if rerun_execution is not None else None
                        ),
                    )
                )
                self._record_document_blocker_repair_execution(
                    document_id=document_id,
                    chapter_id=(issue.chapter_id if issue is not None else None),
                    execution=executions[-1],
                    attempt_index=len(executions),
                    round_index=round_count,
                    round_limit=round_limit,
                )

        blocking_issue_count_after = len(self._list_document_active_blocking_issues(document_id))
        if blocking_issue_count_after > 0 and stop_reason is None and round_count >= round_limit:
            stop_reason = "max_rounds_reached"

        return DocumentBlockerRepairResult(
            document_id=document_id,
            blocking_issue_count_before=blocking_issue_count_before,
            blocking_issue_count_after=blocking_issue_count_after,
            requested=True,
            applied=bool(executions),
            round_count=round_count,
            round_limit=round_limit,
            executions=executions,
            stop_reason=stop_reason,
        )

    def _list_document_active_blocking_issues(self, document_id: str) -> list[ReviewIssue]:
        return list(
            self.session.scalars(
                select(ReviewIssue)
                .where(
                    ReviewIssue.document_id == document_id,
                    ReviewIssue.blocking.is_(True),
                    ReviewIssue.status.in_([IssueStatus.OPEN, IssueStatus.TRIAGED]),
                )
                .order_by(ReviewIssue.created_at.asc(), ReviewIssue.id.asc())
            ).all()
        )

    def _document_blocker_candidate_actions(
        self,
        *,
        issues: list[ReviewIssue],
        attempted_action_ids: set[str],
    ) -> list[IssueAction]:
        if not issues:
            return []

        issue_by_id = {issue.id: issue for issue in issues}
        actions = self.export_repository.list_planned_issue_actions(list(issue_by_id))
        chapter_ordinals = self._chapter_ordinal_map(
            [issue.chapter_id for issue in issues if issue.chapter_id]
        )

        def _action_scope_rank(action: IssueAction) -> int:
            if action.scope_type == JobScopeType.DOCUMENT:
                return 0
            if action.scope_type == JobScopeType.CHAPTER:
                return 1
            if action.scope_type == JobScopeType.PACKET:
                return 2
            return 3

        def _action_priority(action: IssueAction) -> int:
            return {
                ActionType.REPARSE_DOCUMENT: 0,
                ActionType.REPARSE_CHAPTER: 1,
                ActionType.RESEGMENT_CHAPTER: 2,
                ActionType.REALIGN_ONLY: 3,
                ActionType.REBUILD_PACKET_THEN_RERUN: 4,
                ActionType.RERUN_PACKET: 5,
                ActionType.UPDATE_ENTITY_REGISTRY_THEN_RERUN_TARGETED: 6,
                ActionType.UPDATE_TERMBASE_THEN_RERUN_TARGETED: 7,
                ActionType.REBUILD_CHAPTER_BRIEF: 8,
                ActionType.REEXPORT_ONLY: 9,
                ActionType.EDIT_TARGET_ONLY: 10,
                ActionType.MANUAL_FINALIZE: 11,
            }.get(action.action_type, 99)

        def _issue_priority(issue: ReviewIssue) -> int:
            return {
                "MISORDERING": 0,
                "STRUCTURE_POLLUTION": 1,
                "ALIGNMENT_FAILURE": 2,
                "OMISSION": 3,
                "CONTEXT_FAILURE": 4,
                "TERM_CONFLICT": 5,
                "UNLOCKED_KEY_CONCEPT": 6,
                "STYLE_DRIFT": 7,
            }.get(issue.issue_type, 20)

        ordered_actions = sorted(
            (
                action
                for action in actions
                if action.id not in attempted_action_ids and action.issue_id in issue_by_id
            ),
            key=lambda action: (
                _action_scope_rank(action),
                _action_priority(action),
                _issue_priority(issue_by_id[action.issue_id]),
                chapter_ordinals.get(issue_by_id[action.issue_id].chapter_id or "", 10**9),
                str(action.scope_id or ""),
                action.id,
            ),
        )

        selected: list[IssueAction] = []
        reserved_document = False
        reserved_chapter_ids: set[str] = set()
        reserved_packet_ids: set[str] = set()
        reserved_sentence_ids: set[str] = set()

        for action in ordered_actions:
            if reserved_document:
                break
            issue = issue_by_id[action.issue_id]
            rerun_plan = build_rerun_plan(issue, action)
            scope_ids = [scope_id for scope_id in rerun_plan.scope_ids if scope_id]
            if rerun_plan.scope_type == JobScopeType.DOCUMENT:
                selected = [action]
                reserved_document = True
                break
            if rerun_plan.scope_type == JobScopeType.CHAPTER:
                chapter_id = scope_ids[0] if scope_ids else (issue.chapter_id or "")
                if not chapter_id or chapter_id in reserved_chapter_ids:
                    continue
                selected.append(action)
                reserved_chapter_ids.add(chapter_id)
                continue
            if rerun_plan.scope_type == JobScopeType.PACKET:
                if issue.chapter_id and issue.chapter_id in reserved_chapter_ids:
                    continue
                if not scope_ids or any(packet_id in reserved_packet_ids for packet_id in scope_ids):
                    continue
                selected.append(action)
                reserved_packet_ids.update(scope_ids)
                continue
            if rerun_plan.scope_type == JobScopeType.SENTENCE:
                sentence_id = scope_ids[0] if scope_ids else (issue.sentence_id or "")
                if not sentence_id or sentence_id in reserved_sentence_ids:
                    continue
                selected.append(action)
                reserved_sentence_ids.add(sentence_id)
                continue
        return selected

    def _chapter_ordinal_map(self, chapter_ids: list[str]) -> dict[str, int]:
        normalized_ids = sorted({chapter_id for chapter_id in chapter_ids if chapter_id})
        if not normalized_ids:
            return {}
        rows = self.session.execute(
            select(Chapter.id, Chapter.ordinal).where(Chapter.id.in_(normalized_ids))
        ).all()
        return {str(chapter_id): int(ordinal or 0) for chapter_id, ordinal in rows}

    def _split_auto_followup_actions_by_manual_hold(
        self,
        *,
        issue_by_id: dict[str, ReviewIssue],
        actions: list[Any],
    ) -> tuple[list[Any], list[Any]]:
        eligible: list[Any] = []
        blocked: list[Any] = []
        for action in actions:
            issue_id = str(getattr(action, "issue_id", "") or "")
            action_id = str(
                getattr(action, "id", None)
                or getattr(action, "action_id", None)
                or ""
            )
            issue = issue_by_id.get(issue_id)
            if issue is None or not action_id:
                eligible.append(action)
                continue
            if self._auto_followup_failed_execution_count(issue=issue, action_id=action_id) >= AUTO_FOLLOWUP_REPEAT_FAILURE_LIMIT:
                blocked.append(action)
                continue
            eligible.append(action)
        return eligible, blocked

    def _auto_followup_failed_execution_count(
        self,
        *,
        issue: ReviewIssue,
        action_id: str,
    ) -> int:
        scope_filters = [
            and_(
                AuditEvent.object_type == "document",
                AuditEvent.object_id == issue.document_id,
            ),
        ]
        if issue.chapter_id:
            scope_filters.append(
                and_(
                    AuditEvent.object_type == "chapter",
                    AuditEvent.object_id == issue.chapter_id,
                )
            )
        events = self.session.scalars(
            select(AuditEvent).where(
                AuditEvent.action.in_(sorted(AUTO_FOLLOWUP_EXECUTION_AUDIT_ACTIONS)),
                or_(*scope_filters),
            )
        ).all()
        failure_count = 0
        for event in events:
            payload = event.payload_json or {}
            if str(payload.get("action_id") or "") != action_id:
                continue
            if payload.get("issue_resolved") is False:
                failure_count += 1
        return failure_count

    def _apply_review_auto_followups(
        self,
        *,
        chapter_id: str,
        artifacts: ReviewArtifacts,
        attempted_action_ids: set[str],
        executions: list[ReviewAutoFollowupExecution],
        attempt_limit: int,
    ) -> ReviewArtifacts:
        current_artifacts = artifacts
        while len(executions) < attempt_limit:
            issue_by_id = {issue.id: issue for issue in current_artifacts.issues}
            candidate_actions = self._review_auto_followup_candidate_actions(
                current_artifacts,
                issue_by_id=issue_by_id,
                attempted_action_ids=attempted_action_ids,
            )
            if not candidate_actions:
                break
            candidate_actions, blocked_actions = self._split_auto_followup_actions_by_manual_hold(
                issue_by_id=issue_by_id,
                actions=candidate_actions,
            )
            if not candidate_actions:
                self._record_review_auto_followup_stop(
                    chapter_id=chapter_id,
                    executions=executions,
                    attempt_limit=attempt_limit,
                    stop_reason="manual_hold_required",
                    issue_ids=[action.issue_id for action in blocked_actions],
                    followup_action_ids=[
                        str(getattr(action, "id", None) or getattr(action, "action_id", None) or "")
                        for action in blocked_actions
                    ],
                )
                break
            followup_action = candidate_actions[0]
            if len(executions) >= attempt_limit:
                break
            issue = issue_by_id.get(followup_action.issue_id)
            if issue is not None and issue.issue_type == "UNLOCKED_KEY_CONCEPT":
                if not self._auto_lock_review_unlocked_concept(issue):
                    fallback_action = self._fallback_stale_brief_action_for_unlocked_concept(
                        artifacts=current_artifacts,
                        issue_by_id=issue_by_id,
                        attempted_action_ids=attempted_action_ids,
                        issue=issue,
                    )
                    if fallback_action is None:
                        continue
                    followup_action = fallback_action
                    issue = issue_by_id.get(followup_action.issue_id)
            projected_rerun_plan = (
                build_rerun_plan(issue, followup_action)
                if issue is not None
                else None
            )
            attempted_action_ids.add(followup_action.id)
            result = self.execute_action(
                followup_action.id,
                run_followup=(
                    projected_rerun_plan is not None
                    and projected_rerun_plan.scope_type == JobScopeType.PACKET
                ),
            )
            executions.append(
                ReviewAutoFollowupExecution(
                    action_id=followup_action.id,
                    issue_id=followup_action.issue_id,
                    issue_type=(issue.issue_type if issue is not None else "unknown"),
                    action_type=followup_action.action_type.value,
                    rerun_scope_type=result.action_execution.rerun_plan.scope_type.value,
                    rerun_scope_ids=result.action_execution.rerun_plan.scope_ids,
                    followup_executed=result.rerun_execution is not None,
                    rerun_packet_ids=(
                        result.rerun_execution.translated_packet_ids if result.rerun_execution else []
                    ),
                    rerun_translation_run_ids=(
                        result.rerun_execution.translation_run_ids if result.rerun_execution else []
                    ),
                    issue_resolved=(
                        result.rerun_execution.issue_resolved if result.rerun_execution else None
                    ),
                )
            )
            self._record_review_auto_followup_execution(
                chapter_id=chapter_id,
                execution=executions[-1],
                attempt_index=len(executions),
                attempt_limit=attempt_limit,
            )
            if result.rerun_execution is not None and result.rerun_execution.review_artifacts is not None:
                current_artifacts = result.rerun_execution.review_artifacts
            else:
                current_artifacts = self.review_service.review_chapter(chapter_id)
        return current_artifacts

    def _review_auto_followup_candidate_actions(
        self,
        artifacts: ReviewArtifacts,
        *,
        issue_by_id: dict[str, ReviewIssue],
        attempted_action_ids: set[str],
    ) -> list[IssueAction]:
        # Keep STALE_CHAPTER_BRIEF out of the general auto-followup pool.
        # It is only safe as a packet-scoped fallback when concept auto-lock fails
        # on the same affected packet set.
        eligible_issue_types = {"STYLE_DRIFT", "TERM_CONFLICT", "UNLOCKED_KEY_CONCEPT"}
        packet_issue_counts: dict[str, int] = {}
        packet_non_style_issue_counts: dict[str, int] = {}
        packet_issue_types: dict[str, set[str]] = {}
        for issue in artifacts.issues:
            if issue.issue_type not in eligible_issue_types:
                continue
            if issue.issue_type == "UNLOCKED_KEY_CONCEPT" and not self._review_issue_supports_unlocked_concept_auto_followup(issue):
                continue
            if issue.blocking and not self._review_issue_supports_blocking_auto_followup(issue):
                continue
            for packet_id in self._review_issue_followup_packet_ids(issue):
                packet_issue_counts[packet_id] = packet_issue_counts.get(packet_id, 0) + 1
                packet_issue_types.setdefault(packet_id, set()).add(issue.issue_type)
                if issue.issue_type != "STYLE_DRIFT":
                    packet_non_style_issue_counts[packet_id] = (
                        packet_non_style_issue_counts.get(packet_id, 0) + 1
                    )

        filtered_actions: list[IssueAction] = []
        projected_rerun_plans: dict[str, object] = {}
        for action in artifacts.actions:
            if action.id in attempted_action_ids:
                continue
            issue = issue_by_id.get(action.issue_id)
            if issue is None:
                continue
            if issue.issue_type == "UNLOCKED_KEY_CONCEPT" and not self._review_issue_supports_unlocked_concept_auto_followup(issue):
                continue
            if issue.blocking and not self._review_issue_supports_blocking_auto_followup(issue):
                continue
            if issue.issue_type not in eligible_issue_types:
                continue
            rerun_plan = build_rerun_plan(issue, action)
            if rerun_plan.scope_type != JobScopeType.PACKET or not rerun_plan.scope_ids:
                continue
            projected_rerun_plans[action.id] = rerun_plan
            filtered_actions.append(action)

        candidate_actions: list[IssueAction] = []
        seen_packet_ids: set[str] = set()

        def _packet_priority(packet_ids: list[str]) -> int:
            if any(
                packet_non_style_issue_counts.get(packet_id, 0) > 0
                or len(packet_issue_types.get(packet_id, set())) > 1
                for packet_id in packet_ids
            ):
                return 0
            if any(packet_issue_counts.get(packet_id, 0) > 0 for packet_id in packet_ids):
                return 1
            return 2

        def _candidate_priority(action: IssueAction) -> tuple[int, int, int, int, int, str, str]:
            issue = issue_by_id.get(action.issue_id)
            rerun_plan = projected_rerun_plans.get(action.id)
            if issue is None:
                return (2, 2, 3, 0, 0, str(action.scope_id), action.id)
            type_priority = {
                "TERM_CONFLICT": 0,
                "UNLOCKED_KEY_CONCEPT": 1,
                "STYLE_DRIFT": 2,
            }.get(issue.issue_type, 3)
            scope_ids = rerun_plan.scope_ids if rerun_plan is not None else [str(action.scope_id)]
            packet_priority = _packet_priority(scope_ids)
            packet_non_style_weight = sum(
                packet_non_style_issue_counts.get(packet_id, 0) for packet_id in scope_ids
            )
            packet_weight = (
                sum(packet_issue_counts.get(packet_id, 0) for packet_id in scope_ids)
            )
            return (
                0 if issue.blocking else 1,
                type_priority,
                packet_priority,
                -packet_non_style_weight,
                -packet_weight,
                ",".join(scope_ids),
                action.id,
            )

        for action in sorted(
            filtered_actions,
            key=_candidate_priority,
        ):
            rerun_plan = projected_rerun_plans[action.id]
            if any(packet_id in seen_packet_ids for packet_id in rerun_plan.scope_ids):
                continue
            seen_packet_ids.update(rerun_plan.scope_ids)
            candidate_actions.append(action)
        return candidate_actions

    def _review_issue_supports_blocking_auto_followup(self, issue: ReviewIssue) -> bool:
        return bool(
            issue.issue_type == "TERM_CONFLICT"
            and issue.packet_id
            and str((issue.evidence_json or {}).get("expected_target_term") or "").strip()
        )

    def _review_issue_supports_unlocked_concept_auto_followup(self, issue: ReviewIssue) -> bool:
        packet_ids_seen = self._review_issue_followup_packet_ids(issue)
        return bool(
            issue.issue_type == "UNLOCKED_KEY_CONCEPT"
            and packet_ids_seen
            and len(packet_ids_seen) <= MAX_SAFE_UNLOCKED_CONCEPT_PACKET_FOLLOWUP
            and str((issue.evidence_json or {}).get("source_term") or "").strip()
        )

    def _review_issue_supports_stale_brief_auto_followup(self, issue: ReviewIssue) -> bool:
        packet_ids_seen = self._review_issue_followup_packet_ids(issue)
        missing_concepts = [
            str(term).strip()
            for term in list((issue.evidence_json or {}).get("missing_concepts") or [])
            if str(term).strip()
        ]
        return bool(
            issue.issue_type == "STALE_CHAPTER_BRIEF"
            and packet_ids_seen
            and len(packet_ids_seen) <= MAX_SAFE_STALE_CHAPTER_BRIEF_PACKET_FOLLOWUP
            and missing_concepts
        )

    def _review_issue_followup_packet_ids(self, issue: ReviewIssue) -> list[str]:
        return packet_scope_ids_for_issue(issue)

    def _auto_lock_review_unlocked_concept(self, issue: ReviewIssue) -> bool:
        source_term = str((issue.evidence_json or {}).get("source_term") or "").strip()
        if not source_term or issue.chapter_id is None:
            return False
        artifacts = ChapterConceptAutoLockService(
            self.session,
            resolver=build_default_concept_resolver(),
        ).auto_lock_chapter_concepts(
            issue.chapter_id,
            source_terms=[source_term],
            min_times_seen=1,
        )
        return any(record.source_term.casefold() == source_term.casefold() for record in artifacts.locked_records)

    def _fallback_stale_brief_action_for_unlocked_concept(
        self,
        *,
        artifacts: ReviewArtifacts,
        issue_by_id: dict[str, ReviewIssue],
        attempted_action_ids: set[str],
        issue: ReviewIssue,
    ) -> IssueAction | None:
        packet_ids_seen = set(self._review_issue_followup_packet_ids(issue))
        if not packet_ids_seen:
            return None
        for action in artifacts.actions:
            if action.id in attempted_action_ids:
                continue
            fallback_issue = issue_by_id.get(action.issue_id)
            if fallback_issue is None or fallback_issue.issue_type != "STALE_CHAPTER_BRIEF":
                continue
            if not self._review_issue_supports_stale_brief_auto_followup(fallback_issue):
                continue
            fallback_packet_ids = set(self._review_issue_followup_packet_ids(fallback_issue))
            if fallback_packet_ids != packet_ids_seen:
                continue
            rerun_plan = build_rerun_plan(fallback_issue, action)
            if rerun_plan.scope_type != JobScopeType.PACKET or not rerun_plan.scope_ids:
                continue
            return action
        return None

    def export_document(
        self,
        document_id: str,
        export_type: ExportType,
        *,
        auto_execute_followup_on_gate: bool = False,
        max_auto_followup_attempts: int = 3,
    ) -> DocumentExportResult:
        auto_followup_executions: list[ExportAutoFollowupExecution] = []
        attempted_action_ids: set[str] = set()

        while True:
            bundle = self.bootstrap_repository.load_document_bundle(document_id)
            try:
                for chapter_bundle in bundle.chapters:
                    self.export_service.assert_chapter_exportable(chapter_bundle.chapter.id, export_type)
                break
            except ExportGateError as exc:
                if not auto_execute_followup_on_gate:
                    raise
                if not exc.followup_actions:
                    self._record_export_auto_followup_stop(
                        chapter_id=exc.chapter_id,
                        document_id=document_id,
                        export_type=export_type,
                        executions=auto_followup_executions,
                        attempt_limit=max_auto_followup_attempts,
                        stop_reason="no_followup_actions",
                        issue_ids=exc.issue_ids,
                        followup_action_ids=[],
                    )
                    raise self._with_auto_followup_telemetry(
                        exc,
                        auto_followup_executions,
                        requested=True,
                        attempt_limit=max_auto_followup_attempts,
                        stop_reason="no_followup_actions",
                    ) from exc
                candidate_actions = [
                    action
                    for action in exc.followup_actions
                    if action.action_id not in attempted_action_ids
                ]
                if not candidate_actions:
                    self._record_export_auto_followup_stop(
                        chapter_id=exc.chapter_id,
                        document_id=document_id,
                        export_type=export_type,
                        executions=auto_followup_executions,
                        attempt_limit=max_auto_followup_attempts,
                        stop_reason="no_new_actions",
                        issue_ids=exc.issue_ids,
                        followup_action_ids=[action.action_id for action in exc.followup_actions],
                    )
                    raise self._with_auto_followup_telemetry(
                        exc,
                        auto_followup_executions,
                        requested=True,
                        attempt_limit=max_auto_followup_attempts,
                        stop_reason="no_new_actions",
                    ) from exc
                issue_by_id = {
                    issue.id: issue
                    for issue in self.session.scalars(
                        select(ReviewIssue).where(ReviewIssue.id.in_(exc.issue_ids))
                    ).all()
                }
                candidate_actions, blocked_actions = self._split_auto_followup_actions_by_manual_hold(
                    issue_by_id=issue_by_id,
                    actions=candidate_actions,
                )
                if not candidate_actions:
                    self._record_export_auto_followup_stop(
                        chapter_id=exc.chapter_id,
                        document_id=document_id,
                        export_type=export_type,
                        executions=auto_followup_executions,
                        attempt_limit=max_auto_followup_attempts,
                        stop_reason="manual_hold_required",
                        issue_ids=exc.issue_ids,
                        followup_action_ids=[action.action_id for action in blocked_actions],
                    )
                    raise self._with_auto_followup_telemetry(
                        exc,
                        auto_followup_executions,
                        requested=True,
                        attempt_limit=max_auto_followup_attempts,
                        stop_reason="manual_hold_required",
                    ) from exc
                remaining_attempt_budget = max(max_auto_followup_attempts - len(auto_followup_executions), 0)
                if remaining_attempt_budget <= 0:
                    self._record_export_auto_followup_stop(
                        chapter_id=exc.chapter_id,
                        document_id=document_id,
                        export_type=export_type,
                        executions=auto_followup_executions,
                        attempt_limit=max_auto_followup_attempts,
                        stop_reason="max_attempts_reached",
                        issue_ids=exc.issue_ids,
                        followup_action_ids=[action.action_id for action in candidate_actions],
                    )
                    raise self._with_auto_followup_telemetry(
                        exc,
                        auto_followup_executions,
                        requested=True,
                        attempt_limit=max_auto_followup_attempts,
                        stop_reason="max_attempts_reached",
                    ) from exc
                executed_actions = candidate_actions[:remaining_attempt_budget]
                for followup_action in executed_actions:
                    attempted_action_ids.add(followup_action.action_id)
                    result = self.execute_action(
                        followup_action.action_id,
                        run_followup=followup_action.suggested_run_followup,
                    )
                    auto_followup_executions.append(
                        ExportAutoFollowupExecution(
                            action_id=followup_action.action_id,
                            issue_id=followup_action.issue_id,
                            action_type=followup_action.action_type,
                            rerun_scope_type=result.action_execution.rerun_plan.scope_type.value,
                            rerun_scope_ids=result.action_execution.rerun_plan.scope_ids,
                            followup_executed=result.rerun_execution is not None,
                            rerun_packet_ids=(
                                result.rerun_execution.translated_packet_ids if result.rerun_execution else []
                            ),
                            rerun_translation_run_ids=(
                                result.rerun_execution.translation_run_ids if result.rerun_execution else []
                            ),
                            issue_resolved=(
                                result.rerun_execution.issue_resolved if result.rerun_execution else None
                            ),
                        )
                    )
                    self._record_export_auto_followup_execution(
                        chapter_id=exc.chapter_id,
                        document_id=document_id,
                        export_type=export_type,
                        execution=auto_followup_executions[-1],
                        attempt_index=len(auto_followup_executions),
                        attempt_limit=max_auto_followup_attempts,
                    )
                if len(candidate_actions) > remaining_attempt_budget:
                    self._record_export_auto_followup_stop(
                        chapter_id=exc.chapter_id,
                        document_id=document_id,
                        export_type=export_type,
                        executions=auto_followup_executions,
                        attempt_limit=max_auto_followup_attempts,
                        stop_reason="max_attempts_reached",
                        issue_ids=exc.issue_ids,
                        followup_action_ids=[action.action_id for action in candidate_actions[remaining_attempt_budget:]],
                    )
                    raise self._with_auto_followup_telemetry(
                        exc,
                        auto_followup_executions,
                        requested=True,
                        attempt_limit=max_auto_followup_attempts,
                        stop_reason="max_attempts_reached",
                    ) from exc

        results: list[ChapterExportResult] = []
        document_file_path: str | None = None
        document_manifest_path: str | None = None

        if export_type == ExportType.MERGED_HTML:
            artifacts = self.export_service.export_document_merged_html(document_id)
            document = self.session.get(type(bundle.document), document_id) or bundle.document
            runtime_v2_context = self._runtime_v2_context_for_run(self._latest_document_run(document_id))
            self._persist_document_export_runtime_v2_context(
                document_id=document_id,
                export_type=export_type,
                export_records=[artifacts.export_record, markdown_artifacts.export_record],
                runtime_v2_context=runtime_v2_context,
            )
            return DocumentExportResult(
                document_id=document_id,
                export_type=export_type.value,
                document_status=document.status.value,
                file_path=str(artifacts.file_path),
                manifest_path=(str(artifacts.manifest_path) if artifacts.manifest_path is not None else None),
                chapter_results=results,
                auto_followup_requested=auto_execute_followup_on_gate,
                auto_followup_applied=bool(auto_followup_executions),
                auto_followup_attempt_count=len(auto_followup_executions),
                auto_followup_attempt_limit=(max_auto_followup_attempts if auto_execute_followup_on_gate else None),
                auto_followup_executions=auto_followup_executions,
                route_evidence_json=artifacts.route_evidence_json,
                runtime_v2_context=runtime_v2_context,
            )
        if export_type == ExportType.MERGED_MARKDOWN:
            artifacts = self.export_service.export_document_merged_markdown(document_id)
            document = self.session.get(type(bundle.document), document_id) or bundle.document
            runtime_v2_context = self._runtime_v2_context_for_run(self._latest_document_run(document_id))
            self._persist_document_export_runtime_v2_context(
                document_id=document_id,
                export_type=export_type,
                export_records=[artifacts.export_record],
                runtime_v2_context=runtime_v2_context,
            )
            return DocumentExportResult(
                document_id=document_id,
                export_type=export_type.value,
                document_status=document.status.value,
                file_path=str(artifacts.file_path),
                manifest_path=(str(artifacts.manifest_path) if artifacts.manifest_path is not None else None),
                chapter_results=results,
                auto_followup_requested=auto_execute_followup_on_gate,
                auto_followup_applied=bool(auto_followup_executions),
                auto_followup_attempt_count=len(auto_followup_executions),
                auto_followup_attempt_limit=(max_auto_followup_attempts if auto_execute_followup_on_gate else None),
                auto_followup_executions=auto_followup_executions,
                route_evidence_json=artifacts.route_evidence_json,
                runtime_v2_context=runtime_v2_context,
            )
        if export_type == ExportType.REBUILT_EPUB:
            artifacts = self.export_service.export_document_rebuilt_epub(document_id)
            document = self.session.get(type(bundle.document), document_id) or bundle.document
            runtime_v2_context = self._runtime_v2_context_for_run(self._latest_document_run(document_id))
            self._persist_document_export_runtime_v2_context(
                document_id=document_id,
                export_type=export_type,
                export_records=[artifacts.export_record],
                runtime_v2_context=runtime_v2_context,
            )
            return DocumentExportResult(
                document_id=document_id,
                export_type=export_type.value,
                document_status=document.status.value,
                file_path=str(artifacts.file_path),
                manifest_path=(str(artifacts.manifest_path) if artifacts.manifest_path is not None else None),
                chapter_results=results,
                auto_followup_requested=auto_execute_followup_on_gate,
                auto_followup_applied=bool(auto_followup_executions),
                auto_followup_attempt_count=len(auto_followup_executions),
                auto_followup_attempt_limit=(max_auto_followup_attempts if auto_execute_followup_on_gate else None),
                auto_followup_executions=auto_followup_executions,
                route_evidence_json=artifacts.route_evidence_json,
                runtime_v2_context=runtime_v2_context,
            )
        if export_type == ExportType.REBUILT_PDF:
            artifacts = self.export_service.export_document_rebuilt_pdf(document_id)
            document = self.session.get(type(bundle.document), document_id) or bundle.document
            runtime_v2_context = self._runtime_v2_context_for_run(self._latest_document_run(document_id))
            self._persist_document_export_runtime_v2_context(
                document_id=document_id,
                export_type=export_type,
                export_records=[artifacts.export_record],
                runtime_v2_context=runtime_v2_context,
            )
            return DocumentExportResult(
                document_id=document_id,
                export_type=export_type.value,
                document_status=document.status.value,
                file_path=str(artifacts.file_path),
                manifest_path=(str(artifacts.manifest_path) if artifacts.manifest_path is not None else None),
                chapter_results=results,
                auto_followup_requested=auto_execute_followup_on_gate,
                auto_followup_applied=bool(auto_followup_executions),
                auto_followup_attempt_count=len(auto_followup_executions),
                auto_followup_attempt_limit=(max_auto_followup_attempts if auto_execute_followup_on_gate else None),
                auto_followup_executions=auto_followup_executions,
                route_evidence_json=artifacts.route_evidence_json,
                runtime_v2_context=runtime_v2_context,
            )

        for chapter_bundle in bundle.chapters:
            artifacts: ExportArtifacts = self.export_service.export_chapter(chapter_bundle.chapter.id, export_type)
            results.append(
                ChapterExportResult(
                    chapter_id=chapter_bundle.chapter.id,
                    export_id=artifacts.export_record.id,
                    export_type=artifacts.export_record.export_type.value,
                    status=artifacts.export_record.status.value,
                    file_path=str(artifacts.file_path),
                    manifest_path=(str(artifacts.manifest_path) if artifacts.manifest_path is not None else None),
                )
            )

        document = self.session.get(type(bundle.document), document_id) or bundle.document
        return DocumentExportResult(
            document_id=document_id,
            export_type=export_type.value,
            document_status=document.status.value,
            file_path=document_file_path,
            manifest_path=document_manifest_path,
            chapter_results=results,
            auto_followup_requested=auto_execute_followup_on_gate,
            auto_followup_applied=bool(auto_followup_executions),
            auto_followup_attempt_count=len(auto_followup_executions),
            auto_followup_attempt_limit=(max_auto_followup_attempts if auto_execute_followup_on_gate else None),
            auto_followup_executions=auto_followup_executions,
            route_evidence_json=None,
            runtime_v2_context=self._runtime_v2_context_for_run(self._latest_document_run(document_id)),
        )

    def execute_action(self, action_id: str, run_followup: bool = False) -> ActionWorkflowResult:
        action_execution = self.action_executor.execute(action_id)
        rerun_execution = None
        if run_followup:
            rerun_execution = self.rerun_service.execute(action_execution.rerun_plan)
        return ActionWorkflowResult(
            action_execution=action_execution,
            rerun_execution=rerun_execution,
        )

    def get_document_export_dashboard(
        self,
        document_id: str,
        *,
        export_type: ExportType | None = None,
        status: ExportStatus | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> DocumentExportDashboard:
        exports = self.export_repository.list_document_exports(document_id)
        document_translation_runs = self.export_repository.list_document_translation_runs(document_id)
        filtered_exports = self.export_repository.list_document_exports_filtered(
            document_id,
            export_type=export_type,
            status=status,
            limit=limit,
            offset=offset,
        )
        filtered_export_count = self.export_repository.count_document_exports(
            document_id,
            export_type=export_type,
            status=status,
        )
        export_counts_by_type: dict[str, int] = {}
        latest_export_ids_by_type: dict[str, str] = {}
        total_auto_followup_executed_count = 0

        for export in exports:
            export_type_value = export.export_type.value
            export_counts_by_type[export_type_value] = export_counts_by_type.get(export_type_value, 0) + 1
            latest_export_ids_by_type.setdefault(export_type_value, export.id)
            auto_followup_summary = self._to_export_auto_followup_summary(export)
            if auto_followup_summary is not None:
                total_auto_followup_executed_count += auto_followup_summary.executed_event_count

        records = [self._to_export_record_summary(export) for export in filtered_exports]
        successful_export_count = sum(1 for export in exports if export.status.value == "succeeded")
        latest_export_at = exports[0].created_at.isoformat() if exports else None
        record_count = len(records)
        has_more = (offset + record_count) < filtered_export_count
        issue_chapter_pressure = self._to_issue_chapter_pressure(document_id)
        issue_chapter_breakdown = self._to_issue_chapter_breakdown(document_id)
        issue_chapter_heatmap = self._to_issue_chapter_heatmap(issue_chapter_breakdown)
        issue_chapter_activity = self._to_issue_chapter_activity_map(document_id)
        issue_chapter_worklist_meta = self._to_issue_chapter_worklist_meta(document_id)
        chapter_assignment_map = self._to_chapter_assignment_map(document_id)
        issue_activity_breakdown = self._to_issue_activity_breakdown(document_id)
        return DocumentExportDashboard(
            document_id=document_id,
            export_count=len(exports),
            successful_export_count=successful_export_count,
            filtered_export_count=filtered_export_count,
            record_count=record_count,
            offset=offset,
            limit=limit,
            has_more=has_more,
            applied_export_type_filter=(export_type.value if export_type is not None else None),
            applied_status_filter=(status.value if status is not None else None),
            latest_export_at=latest_export_at,
            export_counts_by_type=export_counts_by_type,
            latest_export_ids_by_type=latest_export_ids_by_type,
            total_auto_followup_executed_count=total_auto_followup_executed_count,
            translation_usage_summary=self._to_translation_usage_summary_from_runs(document_translation_runs),
            translation_usage_breakdown=self._to_translation_usage_breakdown_from_runs(document_translation_runs),
            translation_usage_timeline=self._to_translation_usage_timeline_from_runs(document_translation_runs),
            translation_usage_highlights=self._to_translation_usage_highlights_from_runs(document_translation_runs),
            issue_hotspots=self._to_issue_hotspots(document_id),
            issue_chapter_pressure=issue_chapter_pressure,
            issue_chapter_highlights=self._to_issue_chapter_highlights(issue_chapter_pressure),
            issue_chapter_breakdown=issue_chapter_breakdown,
            issue_chapter_heatmap=issue_chapter_heatmap,
            issue_chapter_queue=self._to_issue_chapter_queue(
                issue_chapter_heatmap,
                issue_chapter_activity,
                issue_chapter_worklist_meta,
                chapter_assignment_map,
            ),
            issue_activity_timeline=self._to_issue_activity_timeline(document_id),
            issue_activity_breakdown=issue_activity_breakdown,
            issue_activity_highlights=self._to_issue_activity_highlights(issue_activity_breakdown),
            records=records,
        )

    def get_document_export_detail(self, document_id: str, export_id: str) -> ExportDetail:
        export = self.export_repository.get_document_export(document_id, export_id)
        bundle = export.input_version_bundle_json or {}
        return ExportDetail(
            document_id=document_id,
            export_id=export.id,
            export_type=export.export_type.value,
            status=export.status.value,
            file_path=export.file_path,
            manifest_path=bundle.get("sidecar_manifest_path"),
            chapter_id=bundle.get("chapter_id"),
            sentence_count=bundle.get("sentence_count", 0),
            target_segment_count=bundle.get("target_segment_count", 0),
            created_at=export.created_at.isoformat(),
            updated_at=export.updated_at.isoformat(),
            translation_usage_summary=self._to_translation_usage_summary_from_json(
                bundle.get("translation_usage_summary")
            ),
            translation_usage_breakdown=self._to_translation_usage_breakdown_from_json(
                bundle.get("translation_usage_breakdown")
            ),
            translation_usage_timeline=self._to_translation_usage_timeline_from_json(
                bundle.get("translation_usage_timeline")
            ),
            translation_usage_highlights=self._to_translation_usage_highlights_from_json(
                bundle.get("translation_usage_highlights")
            ),
            issue_status_summary=self._to_export_issue_status_summary(bundle.get("issue_status_summary")),
            export_auto_followup_summary=self._to_export_auto_followup_summary(export),
            export_time_misalignment_counts=self._to_export_misalignment_summary(export),
            version_evidence_summary=self._to_export_version_evidence_summary(export),
            runtime_v2_context=export.runtime_v2_context,
        )

    def _runtime_v2_context_for_run(self, run: DocumentRun | None) -> dict[str, Any] | None:
        if run is None:
            return None
        status_detail = dict(run.status_detail_json or {})
        runtime_v2 = dict(status_detail.get("runtime_v2") or {})
        active_bundle_revision_id = runtime_v2.get("active_runtime_bundle_revision_id") or run.runtime_bundle_revision_id
        recovery = dict(runtime_v2.get("last_export_route_recovery") or {})
        evidence = dict(runtime_v2.get("last_export_route_evidence") or {})
        if not active_bundle_revision_id and not recovery and not evidence:
            return None
        context: dict[str, Any] = {
            "active_runtime_bundle_revision_id": active_bundle_revision_id,
            "runtime_bundle_revision_id": runtime_v2.get("runtime_bundle_revision_id") or run.runtime_bundle_revision_id,
            "recovered": bool(recovery),
            "last_export_route_recovery": recovery or None,
            "last_export_route_evidence": evidence or None,
        }
        if recovery:
            bound_work_item_ids = [
                str(work_item_id)
                for work_item_id in (recovery.get("bound_work_item_ids") or [])
                if str(work_item_id).strip()
            ]
            replay_work_item_id = recovery.get("replay_work_item_id") or (
                bound_work_item_ids[0] if bound_work_item_ids else None
            )
            context.update(
                {
                    "incident_id": recovery.get("incident_id"),
                    "proposal_id": recovery.get("proposal_id"),
                    "bundle_revision_id": recovery.get("bundle_revision_id"),
                    "selected_route": recovery.get("selected_route"),
                    "corrected_route": recovery.get("corrected_route"),
                    "route_candidates": list(recovery.get("route_candidates") or []),
                    "replay_scope_id": recovery.get("replay_scope_id"),
                    "bound_work_item_ids": bound_work_item_ids,
                    "replay_work_item_id": replay_work_item_id,
                }
            )
        if evidence:
            context["route_fingerprint"] = evidence.get("route_fingerprint")
            context["export_type"] = evidence.get("export_type")
            context["source_type"] = evidence.get("source_type")
        return context

    def _persist_document_export_runtime_v2_context(
        self,
        *,
        document_id: str,
        export_type: ExportType,
        export_records: list[Export],
        runtime_v2_context: dict[str, Any] | None,
    ) -> None:
        if not export_records:
            return
        runtime_v2_payload = dict(runtime_v2_context or {})
        for export in export_records:
            payload = dict(export.input_version_bundle_json or {})
            if runtime_v2_payload:
                payload["runtime_v2"] = runtime_v2_payload
            export.input_version_bundle_json = payload
            self.export_repository.save_export(export)
        if not runtime_v2_payload.get("recovered"):
            return
        latest_run = self._latest_document_run(document_id)
        if latest_run is None:
            return
        recovery = dict(runtime_v2_payload.get("last_export_route_recovery") or {})
        evidence = dict(runtime_v2_payload.get("last_export_route_evidence") or {})
        replay_work_item_id = runtime_v2_payload.get("replay_work_item_id")
        self.run_control_repository.record_run_event(
            RunAuditEvent(
                run_id=latest_run.id,
                work_item_id=replay_work_item_id,
                event_type="runtime_v2.export.replayed",
                actor_type=ActorType.SYSTEM,
                actor_id="runtime.export-controller",
                created_at=_utcnow(),
                payload_json={
                    "document_id": document_id,
                    "export_type": export_type.value,
                    "runtime_v2": runtime_v2_payload,
                    "incident_id": recovery.get("incident_id"),
                    "proposal_id": recovery.get("proposal_id"),
                    "bundle_revision_id": recovery.get("bundle_revision_id"),
                    "replay_scope_id": runtime_v2_payload.get("replay_scope_id"),
                    "replay_work_item_id": replay_work_item_id,
                    "bound_work_item_ids": list(runtime_v2_payload.get("bound_work_item_ids") or []),
                    "route_fingerprint": evidence.get("route_fingerprint"),
                },
            )
        )

    def get_document_chapter_worklist(
        self,
        document_id: str,
        *,
        queue_priority: str | None = None,
        sla_status: str | None = None,
        owner_ready: bool | None = None,
        needs_immediate_attention: bool | None = None,
        assigned: bool | None = None,
        assigned_owner_name: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> DocumentChapterWorklist:
        self.bootstrap_repository.load_document_bundle(document_id)

        issue_chapter_breakdown = self._to_issue_chapter_breakdown(document_id)
        issue_chapter_heatmap = self._to_issue_chapter_heatmap(issue_chapter_breakdown)
        issue_chapter_activity = self._to_issue_chapter_activity_map(document_id)
        issue_chapter_worklist_meta = self._to_issue_chapter_worklist_meta(document_id)
        chapter_assignment_map = self._to_chapter_assignment_map(document_id)
        entries = self._to_issue_chapter_queue(
            issue_chapter_heatmap,
            issue_chapter_activity,
            issue_chapter_worklist_meta,
            chapter_assignment_map,
        )

        filtered_entries = [
            entry
            for entry in entries
            if (queue_priority is None or entry.queue_priority == queue_priority)
            and (sla_status is None or entry.sla_status == sla_status)
            and (owner_ready is None or entry.owner_ready == owner_ready)
            and (
                needs_immediate_attention is None
                or entry.needs_immediate_attention == needs_immediate_attention
            )
            and (assigned is None or entry.is_assigned == assigned)
            and (
                assigned_owner_name is None
                or entry.assigned_owner_name == assigned_owner_name
            )
        ]
        paged_entries = filtered_entries[offset : (offset + limit) if limit is not None else None]

        queue_priority_counts: dict[str, int] = {}
        sla_status_counts: dict[str, int] = {}
        for entry in entries:
            queue_priority_counts[entry.queue_priority] = (
                queue_priority_counts.get(entry.queue_priority, 0) + 1
            )
            sla_status_counts[entry.sla_status] = sla_status_counts.get(entry.sla_status, 0) + 1

        owner_workload_summary = self._to_owner_workload_summary(entries)

        return DocumentChapterWorklist(
            document_id=document_id,
            worklist_count=len(entries),
            filtered_worklist_count=len(filtered_entries),
            entry_count=len(paged_entries),
            offset=offset,
            limit=limit,
            has_more=(offset + len(paged_entries)) < len(filtered_entries),
            applied_queue_priority_filter=queue_priority,
            applied_sla_status_filter=sla_status,
            applied_owner_ready_filter=owner_ready,
            applied_needs_immediate_attention_filter=needs_immediate_attention,
            applied_assigned_filter=assigned,
            applied_assigned_owner_filter=assigned_owner_name,
            queue_priority_counts=queue_priority_counts,
            sla_status_counts=sla_status_counts,
            immediate_attention_count=sum(1 for entry in entries if entry.needs_immediate_attention),
            owner_ready_count=sum(1 for entry in entries if entry.owner_ready),
            assigned_count=sum(1 for entry in entries if entry.is_assigned),
            owner_workload_summary=owner_workload_summary,
            owner_workload_highlights=self._to_owner_workload_highlights(owner_workload_summary),
            highlights=self._to_issue_chapter_worklist_highlights(entries),
            entries=paged_entries,
        )

    def get_document_chapter_worklist_detail(
        self,
        document_id: str,
        chapter_id: str,
    ) -> DocumentChapterWorklistDetail:
        bundle = self.bootstrap_repository.load_document_bundle(document_id)
        chapter_bundle = next(
            (chapter_bundle for chapter_bundle in bundle.chapters if chapter_bundle.chapter.id == chapter_id),
            None,
        )
        if chapter_bundle is None:
            raise ValueError(f"Chapter not found in document: {chapter_id}")

        issue_family_breakdown = [
            entry
            for entry in self._to_issue_chapter_breakdown(document_id)
            if entry.chapter_id == chapter_id
        ]
        chapter_activity = self._to_issue_chapter_activity_map(document_id)
        chapter_worklist_meta = self._to_issue_chapter_worklist_meta(document_id)
        chapter_assignment_map = self._to_chapter_assignment_map(document_id)
        queue_entries = self._to_issue_chapter_queue(
            self._to_issue_chapter_heatmap(issue_family_breakdown),
            chapter_activity,
            chapter_worklist_meta,
            chapter_assignment_map,
        )
        queue_entry = queue_entries[0] if queue_entries else None
        quality_summary = self.review_repository.load_quality_summaries_for_document(document_id).get(chapter_id)

        return DocumentChapterWorklistDetail(
            document_id=document_id,
            chapter_id=chapter_id,
            ordinal=chapter_bundle.chapter.ordinal,
            title_src=chapter_bundle.chapter.title_src,
            chapter_status=chapter_bundle.chapter.status.value,
            packet_count=len(chapter_bundle.translation_packets),
            translated_packet_count=sum(
                1
                for packet in chapter_bundle.translation_packets
                if packet.status == PacketStatus.TRANSLATED
            ),
            current_issue_count=sum(entry.issue_count for entry in issue_family_breakdown),
            current_open_issue_count=sum(entry.open_issue_count for entry in issue_family_breakdown),
            current_triaged_issue_count=sum(entry.triaged_issue_count for entry in issue_family_breakdown),
            current_active_blocking_issue_count=sum(
                entry.active_blocking_issue_count for entry in issue_family_breakdown
            ),
            assignment=chapter_assignment_map.get(chapter_id),
            queue_entry=queue_entry,
            quality_summary=self._to_stored_quality_summary(quality_summary),
            issue_family_breakdown=issue_family_breakdown,
            recent_issues=self._to_chapter_recent_issues(chapter_id),
            recent_actions=self._to_chapter_recent_actions(chapter_id),
            assignment_history=self._to_chapter_assignment_history(chapter_id),
        )

    def assign_document_chapter_worklist_owner(
        self,
        document_id: str,
        chapter_id: str,
        *,
        owner_name: str,
        assigned_by: str,
        note: str | None = None,
    ) -> ChapterWorklistAssignmentSummary:
        bundle = self.bootstrap_repository.load_document_bundle(document_id)
        if not any(chapter_bundle.chapter.id == chapter_id for chapter_bundle in bundle.chapters):
            raise ValueError(f"Chapter not found in document: {chapter_id}")

        assignment = self.session.scalar(
            select(ChapterWorklistAssignment).where(ChapterWorklistAssignment.chapter_id == chapter_id)
        )
        if assignment is None:
            assignment = ChapterWorklistAssignment(
                document_id=document_id,
                chapter_id=chapter_id,
            )
            self.session.add(assignment)
        assignment.document_id = document_id
        assignment.chapter_id = chapter_id
        assignment.owner_name = owner_name
        assignment.assigned_by = assigned_by
        assignment.note = note
        assignment.assigned_at = _utcnow()
        self.session.flush()
        self.session.refresh(assignment)

        audit = AuditEvent(
            object_type="chapter",
            object_id=chapter_id,
            action="chapter.worklist.assignment.set",
            actor_type=ActorType.HUMAN,
            actor_id=assigned_by,
            created_at=_utcnow(),
            payload_json={
                "document_id": document_id,
                "chapter_id": chapter_id,
                "owner_name": owner_name,
                "note": note,
            },
        )
        self.ops_repository.save_audits([audit])
        self.session.flush()
        return self._to_assignment_summary(assignment)

    def clear_document_chapter_worklist_owner(
        self,
        document_id: str,
        chapter_id: str,
        *,
        cleared_by: str,
        note: str | None = None,
    ) -> ChapterWorklistAssignmentSummary:
        self.bootstrap_repository.load_document_bundle(document_id)
        assignment = self.session.scalar(
            select(ChapterWorklistAssignment).where(
                ChapterWorklistAssignment.document_id == document_id,
                ChapterWorklistAssignment.chapter_id == chapter_id,
            )
        )
        if assignment is None:
            raise ValueError(f"Chapter worklist assignment not found: {chapter_id}")

        summary = self._to_assignment_summary(assignment)
        self.session.delete(assignment)
        self.session.flush()
        audit = AuditEvent(
            object_type="chapter",
            object_id=chapter_id,
            action="chapter.worklist.assignment.cleared",
            actor_type=ActorType.HUMAN,
            actor_id=cleared_by,
            created_at=_utcnow(),
            payload_json={
                "document_id": document_id,
                "chapter_id": chapter_id,
                "owner_name": summary.owner_name,
                "note": note,
            },
        )
        self.ops_repository.save_audits([audit])
        self.session.flush()
        return summary

    def _to_issue_chapter_worklist_highlights(
        self,
        entries: list[IssueChapterQueueEntry],
    ) -> dict[str, IssueChapterQueueEntry | None]:
        def _pick(candidates: list[IssueChapterQueueEntry]) -> IssueChapterQueueEntry | None:
            if not candidates:
                return None
            return max(
                candidates,
                key=lambda entry: (
                    entry.age_hours if entry.age_hours is not None else -1,
                    entry.heat_score,
                    entry.active_blocking_issue_count,
                    entry.open_issue_count,
                    -entry.ordinal,
                ),
            )

        return {
            "top_breached_entry": _pick([entry for entry in entries if entry.sla_status == "breached"]),
            "top_due_soon_entry": _pick([entry for entry in entries if entry.sla_status == "due_soon"]),
            "top_oldest_entry": _pick([entry for entry in entries if entry.age_hours is not None]),
            "top_immediate_entry": _pick([entry for entry in entries if entry.needs_immediate_attention]),
        }

    def _to_chapter_recent_issues(
        self,
        chapter_id: str,
        *,
        limit: int = 10,
    ) -> list[ChapterWorklistIssue]:
        issues = self.session.scalars(
            select(ReviewIssue)
            .where(ReviewIssue.chapter_id == chapter_id)
            .order_by(ReviewIssue.updated_at.desc(), ReviewIssue.created_at.desc())
            .limit(limit)
        ).all()
        return [
            ChapterWorklistIssue(
                issue_id=issue.id,
                issue_type=issue.issue_type,
                root_cause_layer=issue.root_cause_layer.value,
                severity=issue.severity.value,
                status=issue.status.value,
                blocking=issue.blocking,
                detector=issue.detector.value,
                suggested_action=issue.suggested_action,
                created_at=issue.created_at.isoformat(),
                updated_at=issue.updated_at.isoformat(),
            )
            for issue in issues
        ]

    def _to_chapter_recent_actions(
        self,
        chapter_id: str,
        *,
        limit: int = 10,
    ) -> list[ChapterWorklistAction]:
        rows = self.session.execute(
            select(IssueAction, ReviewIssue.issue_type)
            .join(ReviewIssue, IssueAction.issue_id == ReviewIssue.id)
            .where(ReviewIssue.chapter_id == chapter_id)
            .order_by(IssueAction.updated_at.desc(), IssueAction.created_at.desc())
            .limit(limit)
        ).all()
        return [
            ChapterWorklistAction(
                action_id=action.id,
                issue_id=action.issue_id,
                issue_type=issue_type,
                action_type=action.action_type.value,
                scope_type=action.scope_type.value,
                scope_id=action.scope_id,
                status=action.status.value,
                created_by=action.created_by.value,
                created_at=action.created_at.isoformat(),
                updated_at=action.updated_at.isoformat(),
            )
            for action, issue_type in rows
        ]

    def _to_chapter_assignment_history(
        self,
        chapter_id: str,
        *,
        limit: int = 20,
    ) -> list[ChapterWorklistAssignmentHistoryEntry]:
        assignment_actions = {
            "chapter.worklist.assignment.set": "set",
            "chapter.worklist.assignment.cleared": "cleared",
        }
        events = self.session.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.object_type == "chapter",
                AuditEvent.object_id == chapter_id,
                AuditEvent.action.in_(tuple(assignment_actions.keys())),
            )
            .order_by(AuditEvent.created_at.desc())
            .limit(limit)
        ).all()
        return [
            ChapterWorklistAssignmentHistoryEntry(
                event_id=event.id,
                event_type=assignment_actions[event.action],
                owner_name=(event.payload_json.get("owner_name") if event.payload_json else None),
                performed_by=event.actor_id,
                note=(event.payload_json.get("note") if event.payload_json else None),
                created_at=event.created_at.isoformat(),
            )
            for event in events
        ]

    def _to_export_record_summary(self, export) -> ExportRecordSummary:
        bundle = export.input_version_bundle_json or {}
        return ExportRecordSummary(
            export_id=export.id,
            export_type=export.export_type.value,
            status=export.status.value,
            file_path=export.file_path,
            manifest_path=bundle.get("sidecar_manifest_path"),
            chapter_id=bundle.get("chapter_id"),
            chapter_summary_version=bundle.get("chapter_summary_version"),
            created_at=export.created_at.isoformat(),
            updated_at=export.updated_at.isoformat(),
            translation_usage_summary=self._to_translation_usage_summary_from_json(
                bundle.get("translation_usage_summary")
            ),
            translation_usage_breakdown=self._to_translation_usage_breakdown_from_json(
                bundle.get("translation_usage_breakdown")
            ),
            translation_usage_timeline=self._to_translation_usage_timeline_from_json(
                bundle.get("translation_usage_timeline")
            ),
            translation_usage_highlights=self._to_translation_usage_highlights_from_json(
                bundle.get("translation_usage_highlights")
            ),
            export_auto_followup_summary=self._to_export_auto_followup_summary(export),
            export_time_misalignment_counts=self._to_export_misalignment_summary(export),
        )

    def _to_export_auto_followup_summary(self, export) -> ExportAutoFollowupSummary | None:
        bundle = export.input_version_bundle_json or {}
        auto_followup_summary_json = bundle.get("export_auto_followup_summary") or {}
        if not auto_followup_summary_json:
            return None
        return ExportAutoFollowupSummary(
            event_count=auto_followup_summary_json.get("event_count", 0),
            executed_event_count=auto_followup_summary_json.get("executed_event_count", 0),
            stop_event_count=auto_followup_summary_json.get("stop_event_count", 0),
            latest_event_at=auto_followup_summary_json.get("latest_event_at"),
            last_stop_reason=auto_followup_summary_json.get("last_stop_reason"),
        )

    def _to_export_misalignment_summary(self, export) -> ExportMisalignmentCountSummary | None:
        bundle = export.input_version_bundle_json or {}
        misalignment_counts_json = bundle.get("export_time_misalignment_counts") or {}
        if not misalignment_counts_json:
            return None
        return ExportMisalignmentCountSummary(
            missing_target_sentence_count=misalignment_counts_json.get("missing_target_sentence_count", 0),
            inactive_only_sentence_count=misalignment_counts_json.get("inactive_only_sentence_count", 0),
            orphan_target_segment_count=misalignment_counts_json.get("orphan_target_segment_count", 0),
            inactive_target_segment_with_edges_count=misalignment_counts_json.get(
                "inactive_target_segment_with_edges_count", 0
            ),
        )

    def _to_translation_usage_summary_from_runs(
        self,
        translation_runs,
    ) -> TranslationUsageSummary | None:
        if not translation_runs:
            return None
        run_count = len(translation_runs)
        succeeded_run_count = sum(1 for run in translation_runs if run.status.value == "succeeded")
        total_token_in = sum(run.token_in or 0 for run in translation_runs)
        total_token_out = sum(run.token_out or 0 for run in translation_runs)
        total_cost_usd = round(sum(float(run.cost_usd or 0) for run in translation_runs), 6)
        latency_values = [run.latency_ms for run in translation_runs if run.latency_ms is not None]
        total_latency_ms = sum(latency_values)
        avg_latency_ms = round(total_latency_ms / len(latency_values), 3) if latency_values else None
        latest_run_at = max(run.created_at for run in translation_runs).isoformat()
        return TranslationUsageSummary(
            run_count=run_count,
            succeeded_run_count=succeeded_run_count,
            total_token_in=total_token_in,
            total_token_out=total_token_out,
            total_cost_usd=total_cost_usd,
            total_latency_ms=total_latency_ms,
            avg_latency_ms=avg_latency_ms,
            latest_run_at=latest_run_at,
        )

    def _to_translation_usage_breakdown_from_runs(
        self,
        translation_runs,
    ) -> list[TranslationUsageBreakdownEntry]:
        if not translation_runs:
            return []

        grouped: dict[tuple[str, str | None, str | None], list] = {}
        for run in translation_runs:
            model_config = run.model_config_json or {}
            key = (
                run.model_name,
                model_config.get("worker"),
                model_config.get("provider"),
            )
            grouped.setdefault(key, []).append(run)

        breakdown: list[TranslationUsageBreakdownEntry] = []
        for (model_name, worker_name, provider), runs in grouped.items():
            latency_values = [run.latency_ms for run in runs if run.latency_ms is not None]
            total_latency_ms = sum(latency_values)
            avg_latency_ms = round(total_latency_ms / len(latency_values), 3) if latency_values else None
            breakdown.append(
                TranslationUsageBreakdownEntry(
                    model_name=model_name,
                    worker_name=worker_name,
                    provider=provider,
                    run_count=len(runs),
                    succeeded_run_count=sum(1 for run in runs if run.status.value == "succeeded"),
                    total_token_in=sum(run.token_in or 0 for run in runs),
                    total_token_out=sum(run.token_out or 0 for run in runs),
                    total_cost_usd=round(sum(float(run.cost_usd or 0) for run in runs), 6),
                    total_latency_ms=total_latency_ms,
                    avg_latency_ms=avg_latency_ms,
                    latest_run_at=max(run.created_at for run in runs).isoformat(),
                )
            )

        breakdown.sort(
            key=lambda entry: (
                -entry.total_cost_usd,
                -entry.run_count,
                entry.model_name,
                entry.worker_name or "",
            )
        )
        return breakdown

    def _to_translation_usage_timeline_from_runs(
        self,
        translation_runs,
    ) -> list[TranslationUsageTimelineEntry]:
        if not translation_runs:
            return []

        grouped: dict[str, list] = {}
        for run in translation_runs:
            bucket_start = run.created_at.date().isoformat()
            grouped.setdefault(bucket_start, []).append(run)

        timeline: list[TranslationUsageTimelineEntry] = []
        for bucket_start, runs in grouped.items():
            latency_values = [run.latency_ms for run in runs if run.latency_ms is not None]
            total_latency_ms = sum(latency_values)
            avg_latency_ms = round(total_latency_ms / len(latency_values), 3) if latency_values else None
            timeline.append(
                TranslationUsageTimelineEntry(
                    bucket_start=bucket_start,
                    bucket_granularity="day",
                    run_count=len(runs),
                    succeeded_run_count=sum(1 for run in runs if run.status.value == "succeeded"),
                    total_token_in=sum(run.token_in or 0 for run in runs),
                    total_token_out=sum(run.token_out or 0 for run in runs),
                    total_cost_usd=round(sum(float(run.cost_usd or 0) for run in runs), 6),
                    total_latency_ms=total_latency_ms,
                    avg_latency_ms=avg_latency_ms,
                )
            )

        timeline.sort(key=lambda entry: entry.bucket_start, reverse=True)
        return timeline

    def _to_translation_usage_highlights_from_runs(
        self,
        translation_runs,
    ) -> TranslationUsageHighlights:
        breakdown = self._to_translation_usage_breakdown_from_runs(translation_runs)
        if not breakdown:
            return TranslationUsageHighlights(
                top_cost_entry=None,
                top_latency_entry=None,
                top_volume_entry=None,
            )

        top_cost_entry = max(
            breakdown,
            key=lambda entry: (
                entry.total_cost_usd,
                entry.run_count,
                entry.model_name,
                entry.worker_name or "",
            ),
        )
        top_latency_entry = max(
            breakdown,
            key=lambda entry: (
                entry.avg_latency_ms or 0.0,
                entry.total_latency_ms,
                entry.model_name,
                entry.worker_name or "",
            ),
        )
        top_volume_entry = max(
            breakdown,
            key=lambda entry: (
                entry.run_count,
                entry.total_token_out,
                entry.model_name,
                entry.worker_name or "",
            ),
        )
        return TranslationUsageHighlights(
            top_cost_entry=top_cost_entry,
            top_latency_entry=top_latency_entry,
            top_volume_entry=top_volume_entry,
        )

    def _to_translation_usage_summary_from_json(self, payload: dict | None) -> TranslationUsageSummary | None:
        if not payload:
            return None
        return TranslationUsageSummary(
            run_count=payload.get("run_count", 0),
            succeeded_run_count=payload.get("succeeded_run_count", 0),
            total_token_in=payload.get("total_token_in", 0),
            total_token_out=payload.get("total_token_out", 0),
            total_cost_usd=float(payload.get("total_cost_usd", 0.0)),
            total_latency_ms=payload.get("total_latency_ms", 0),
            avg_latency_ms=payload.get("avg_latency_ms"),
            latest_run_at=payload.get("latest_run_at"),
        )

    def _to_translation_usage_breakdown_from_json(
        self,
        payload: list[dict] | None,
    ) -> list[TranslationUsageBreakdownEntry]:
        if not payload:
            return []
        return [
            TranslationUsageBreakdownEntry(
                model_name=entry.get("model_name", ""),
                worker_name=entry.get("worker_name"),
                provider=entry.get("provider"),
                run_count=entry.get("run_count", 0),
                succeeded_run_count=entry.get("succeeded_run_count", 0),
                total_token_in=entry.get("total_token_in", 0),
                total_token_out=entry.get("total_token_out", 0),
                total_cost_usd=float(entry.get("total_cost_usd", 0.0)),
                total_latency_ms=entry.get("total_latency_ms", 0),
                avg_latency_ms=entry.get("avg_latency_ms"),
                latest_run_at=entry.get("latest_run_at"),
            )
            for entry in payload
        ]

    def _to_export_issue_status_summary(self, payload: dict | None) -> ExportIssueStatusSummary | None:
        if not payload:
            return None
        return ExportIssueStatusSummary(
            issue_count=payload.get("issue_count", 0),
            open_issue_count=payload.get("open_issue_count", 0),
            resolved_issue_count=payload.get("resolved_issue_count", 0),
            blocking_issue_count=payload.get("blocking_issue_count", 0),
        )

    def _to_issue_hotspots(self, document_id: str) -> list[IssueHotspotEntry]:
        rows = self.session.execute(
            select(
                ReviewIssue.issue_type,
                ReviewIssue.root_cause_layer,
                func.count(ReviewIssue.id).label("issue_count"),
                func.sum(case((ReviewIssue.status == IssueStatus.OPEN, 1), else_=0)).label("open_issue_count"),
                func.sum(case((ReviewIssue.status == IssueStatus.TRIAGED, 1), else_=0)).label(
                    "triaged_issue_count"
                ),
                func.sum(case((ReviewIssue.status == IssueStatus.RESOLVED, 1), else_=0)).label(
                    "resolved_issue_count"
                ),
                func.sum(case((ReviewIssue.status == IssueStatus.WONTFIX, 1), else_=0)).label(
                    "wontfix_issue_count"
                ),
                func.sum(case((ReviewIssue.blocking.is_(True), 1), else_=0)).label("blocking_issue_count"),
                func.count(distinct(ReviewIssue.chapter_id)).label("chapter_count"),
                func.max(ReviewIssue.created_at).label("latest_seen_at"),
            )
            .where(ReviewIssue.document_id == document_id)
            .group_by(ReviewIssue.issue_type, ReviewIssue.root_cause_layer)
        ).all()
        hotspots = [
            IssueHotspotEntry(
                issue_type=issue_type,
                root_cause_layer=root_cause_layer.value,
                issue_count=issue_count or 0,
                open_issue_count=open_issue_count or 0,
                triaged_issue_count=triaged_issue_count or 0,
                resolved_issue_count=resolved_issue_count or 0,
                wontfix_issue_count=wontfix_issue_count or 0,
                blocking_issue_count=blocking_issue_count or 0,
                chapter_count=chapter_count or 0,
                latest_seen_at=latest_seen_at.isoformat() if latest_seen_at is not None else None,
            )
            for (
                issue_type,
                root_cause_layer,
                issue_count,
                open_issue_count,
                triaged_issue_count,
                resolved_issue_count,
                wontfix_issue_count,
                blocking_issue_count,
                chapter_count,
                latest_seen_at,
            ) in rows
        ]
        hotspots.sort(
            key=lambda entry: (
                -entry.open_issue_count,
                -entry.blocking_issue_count,
                -entry.issue_count,
                entry.issue_type,
                entry.root_cause_layer,
            )
        )
        return hotspots

    def _to_issue_chapter_pressure(self, document_id: str) -> list[IssueChapterPressureEntry]:
        rows = self.session.execute(
            select(
                Chapter.id,
                Chapter.ordinal,
                Chapter.title_src,
                Chapter.status,
                func.count(ReviewIssue.id).label("issue_count"),
                func.sum(case((ReviewIssue.status == IssueStatus.OPEN, 1), else_=0)).label("open_issue_count"),
                func.sum(case((ReviewIssue.status == IssueStatus.TRIAGED, 1), else_=0)).label(
                    "triaged_issue_count"
                ),
                func.sum(case((ReviewIssue.status == IssueStatus.RESOLVED, 1), else_=0)).label(
                    "resolved_issue_count"
                ),
                func.sum(case((ReviewIssue.blocking.is_(True), 1), else_=0)).label("blocking_issue_count"),
                func.max(ReviewIssue.created_at).label("latest_issue_at"),
            )
            .join(ReviewIssue, ReviewIssue.chapter_id == Chapter.id)
            .where(Chapter.document_id == document_id)
            .group_by(Chapter.id, Chapter.ordinal, Chapter.title_src, Chapter.status)
        ).all()
        chapters = [
            IssueChapterPressureEntry(
                chapter_id=chapter_id,
                ordinal=ordinal,
                title_src=title_src,
                chapter_status=chapter_status.value,
                issue_count=issue_count or 0,
                open_issue_count=open_issue_count or 0,
                triaged_issue_count=triaged_issue_count or 0,
                resolved_issue_count=resolved_issue_count or 0,
                blocking_issue_count=blocking_issue_count or 0,
                latest_issue_at=latest_issue_at.isoformat() if latest_issue_at is not None else None,
            )
            for (
                chapter_id,
                ordinal,
                title_src,
                chapter_status,
                issue_count,
                open_issue_count,
                triaged_issue_count,
                resolved_issue_count,
                blocking_issue_count,
                latest_issue_at,
            ) in rows
        ]
        chapters.sort(
            key=lambda entry: (
                -entry.open_issue_count,
                -entry.blocking_issue_count,
                -entry.issue_count,
                entry.ordinal,
                entry.chapter_id,
            )
        )
        return chapters

    def _to_issue_activity_timeline(self, document_id: str) -> list[IssueActivityTimelineEntry]:
        issues = self.session.scalars(
            select(ReviewIssue).where(ReviewIssue.document_id == document_id)
        ).all()
        return self._build_issue_activity_timeline(issues)

    def _to_issue_chapter_breakdown(self, document_id: str) -> list[IssueChapterBreakdownEntry]:
        rows = self.session.execute(
            select(
                Chapter.id,
                Chapter.ordinal,
                Chapter.title_src,
                Chapter.status,
                ReviewIssue.issue_type,
                ReviewIssue.root_cause_layer,
                func.count(ReviewIssue.id).label("issue_count"),
                func.sum(case((ReviewIssue.status == IssueStatus.OPEN, 1), else_=0)).label("open_issue_count"),
                func.sum(case((ReviewIssue.status == IssueStatus.TRIAGED, 1), else_=0)).label(
                    "triaged_issue_count"
                ),
                func.sum(case((ReviewIssue.status == IssueStatus.RESOLVED, 1), else_=0)).label(
                    "resolved_issue_count"
                ),
                func.sum(case((ReviewIssue.blocking.is_(True), 1), else_=0)).label("blocking_issue_count"),
                func.sum(
                    case(
                        (
                            ReviewIssue.blocking.is_(True)
                            & ReviewIssue.status.in_([IssueStatus.OPEN, IssueStatus.TRIAGED]),
                            1,
                        ),
                        else_=0,
                    )
                ).label("active_blocking_issue_count"),
                func.max(ReviewIssue.created_at).label("latest_seen_at"),
            )
            .join(ReviewIssue, ReviewIssue.chapter_id == Chapter.id)
            .where(Chapter.document_id == document_id)
            .group_by(
                Chapter.id,
                Chapter.ordinal,
                Chapter.title_src,
                Chapter.status,
                ReviewIssue.issue_type,
                ReviewIssue.root_cause_layer,
            )
        ).all()
        entries = [
            IssueChapterBreakdownEntry(
                chapter_id=chapter_id,
                ordinal=ordinal,
                title_src=title_src,
                chapter_status=chapter_status.value,
                issue_type=issue_type,
                root_cause_layer=root_cause_layer.value,
                issue_count=issue_count or 0,
                open_issue_count=open_issue_count or 0,
                triaged_issue_count=triaged_issue_count or 0,
                resolved_issue_count=resolved_issue_count or 0,
                blocking_issue_count=blocking_issue_count or 0,
                active_blocking_issue_count=active_blocking_issue_count or 0,
                latest_seen_at=latest_seen_at.isoformat() if latest_seen_at is not None else None,
            )
            for (
                chapter_id,
                ordinal,
                title_src,
                chapter_status,
                issue_type,
                root_cause_layer,
                issue_count,
                open_issue_count,
                triaged_issue_count,
                resolved_issue_count,
                blocking_issue_count,
                active_blocking_issue_count,
                latest_seen_at,
            ) in rows
        ]
        entries.sort(
            key=lambda entry: (
                entry.ordinal,
                -entry.open_issue_count,
                -entry.active_blocking_issue_count,
                -entry.issue_count,
                entry.issue_type,
                entry.root_cause_layer,
            )
        )
        return entries

    def _to_issue_chapter_highlights(
        self,
        chapters: list[IssueChapterPressureEntry],
    ) -> IssueChapterHighlights:
        if not chapters:
            return IssueChapterHighlights(
                top_open_chapter=None,
                top_blocking_chapter=None,
                top_resolved_chapter=None,
            )

        top_open_chapter = (
            max(
                (entry for entry in chapters if entry.open_issue_count > 0),
                key=lambda entry: (
                    entry.open_issue_count,
                    entry.blocking_issue_count,
                    entry.issue_count,
                    -entry.ordinal,
                    entry.chapter_id,
                ),
            )
            if any(entry.open_issue_count > 0 for entry in chapters)
            else None
        )
        top_blocking_chapter = (
            max(
                (entry for entry in chapters if entry.blocking_issue_count > 0),
                key=lambda entry: (
                    entry.blocking_issue_count,
                    entry.open_issue_count,
                    entry.issue_count,
                    -entry.ordinal,
                    entry.chapter_id,
                ),
            )
            if any(entry.blocking_issue_count > 0 for entry in chapters)
            else None
        )
        top_resolved_chapter = (
            max(
                (entry for entry in chapters if entry.resolved_issue_count > 0),
                key=lambda entry: (
                    entry.resolved_issue_count,
                    entry.issue_count,
                    -entry.ordinal,
                    entry.chapter_id,
                ),
            )
            if any(entry.resolved_issue_count > 0 for entry in chapters)
            else None
        )
        return IssueChapterHighlights(
            top_open_chapter=top_open_chapter,
            top_blocking_chapter=top_blocking_chapter,
            top_resolved_chapter=top_resolved_chapter,
        )

    def _to_issue_chapter_heatmap(
        self,
        breakdown: list[IssueChapterBreakdownEntry],
    ) -> list[IssueChapterHeatmapEntry]:
        if not breakdown:
            return []

        def _heat_level(score: int) -> str:
            if score <= 0:
                return "none"
            if score <= 3:
                return "low"
            if score <= 6:
                return "medium"
            if score <= 11:
                return "high"
            return "critical"

        grouped: dict[str, list[IssueChapterBreakdownEntry]] = {}
        for entry in breakdown:
            grouped.setdefault(entry.chapter_id, []).append(entry)

        heatmap: list[IssueChapterHeatmapEntry] = []
        for chapter_entries in grouped.values():
            first = chapter_entries[0]
            dominant = max(
                chapter_entries,
                key=lambda entry: (
                    entry.open_issue_count,
                    entry.active_blocking_issue_count,
                    entry.issue_count,
                    entry.issue_type,
                    entry.root_cause_layer,
                ),
            )
            latest_issue_at = max(
                (entry.latest_seen_at for entry in chapter_entries if entry.latest_seen_at is not None),
                default=None,
            )
            open_issue_count = sum(entry.open_issue_count for entry in chapter_entries)
            triaged_issue_count = sum(entry.triaged_issue_count for entry in chapter_entries)
            resolved_issue_count = sum(entry.resolved_issue_count for entry in chapter_entries)
            blocking_issue_count = sum(entry.blocking_issue_count for entry in chapter_entries)
            active_blocking_issue_count = sum(entry.active_blocking_issue_count for entry in chapter_entries)
            heat_score = (
                open_issue_count * 3
                + triaged_issue_count * 2
                + active_blocking_issue_count * 4
            )
            heatmap.append(
                IssueChapterHeatmapEntry(
                    chapter_id=first.chapter_id,
                    ordinal=first.ordinal,
                    title_src=first.title_src,
                    chapter_status=first.chapter_status,
                    issue_count=sum(entry.issue_count for entry in chapter_entries),
                    open_issue_count=open_issue_count,
                    triaged_issue_count=triaged_issue_count,
                    resolved_issue_count=resolved_issue_count,
                    blocking_issue_count=blocking_issue_count,
                    active_blocking_issue_count=active_blocking_issue_count,
                    issue_family_count=len(chapter_entries),
                    dominant_issue_type=dominant.issue_type,
                    dominant_root_cause_layer=dominant.root_cause_layer,
                    dominant_issue_count=dominant.issue_count,
                    latest_issue_at=latest_issue_at,
                    heat_score=heat_score,
                    heat_level=_heat_level(heat_score),
                )
            )

        heatmap.sort(
            key=lambda entry: (
                -entry.heat_score,
                -entry.open_issue_count,
                -entry.active_blocking_issue_count,
                -entry.issue_count,
                entry.ordinal,
                entry.chapter_id,
            )
        )
        return heatmap

    def _to_issue_chapter_queue(
        self,
        heatmap: list[IssueChapterHeatmapEntry],
        chapter_activity: dict[str, list[IssueActivityTimelineEntry]],
        chapter_worklist_meta: dict[str, dict[str, object]],
        chapter_assignment_map: dict[str, ChapterWorklistAssignmentSummary],
    ) -> list[IssueChapterQueueEntry]:
        actionable_entries = [
            entry
            for entry in heatmap
            if entry.open_issue_count > 0
            or entry.triaged_issue_count > 0
            or entry.active_blocking_issue_count > 0
        ]

        def _is_pdf_image_caption_gap(entry: IssueChapterHeatmapEntry) -> bool:
            return entry.dominant_issue_type == "IMAGE_CAPTION_RECOVERY_REQUIRED"

        def _priority(entry: IssueChapterHeatmapEntry) -> str:
            if entry.active_blocking_issue_count > 0:
                return "immediate"
            if _is_pdf_image_caption_gap(entry):
                return "high"
            if entry.heat_score >= 6 or entry.open_issue_count >= 3:
                return "high"
            return "medium"

        def _driver(entry: IssueChapterHeatmapEntry) -> str:
            if entry.active_blocking_issue_count > 0:
                return "active_blocking"
            if _is_pdf_image_caption_gap(entry):
                return "pdf_image_caption_gap"
            if entry.open_issue_count > 0:
                return "open_pressure"
            return "triaged_backlog"

        def _sla_target_hours(priority: str) -> int:
            if priority == "immediate":
                return 4
            if priority == "high":
                return 24
            return 72

        def _age_bucket(age_hours: int | None, sla_target_hours: int | None) -> str:
            if age_hours is None or sla_target_hours is None:
                return "unknown"
            if age_hours <= 0:
                return "fresh"
            if age_hours < max(1, int(sla_target_hours * 0.5)):
                return "fresh"
            if age_hours < sla_target_hours:
                return "aging"
            return "overdue"

        def _sla_status(age_hours: int | None, sla_target_hours: int | None) -> str:
            if age_hours is None or sla_target_hours is None:
                return "unknown"
            if age_hours >= sla_target_hours:
                return "breached"
            if age_hours >= max(1, int(sla_target_hours * 0.75)):
                return "due_soon"
            return "on_track"

        def _owner_ready_reason(entry: IssueChapterHeatmapEntry) -> str:
            if entry.dominant_issue_type is None or entry.dominant_root_cause_layer is None:
                return "missing_issue_family"
            if _is_pdf_image_caption_gap(entry):
                return "pdf_image_caption_issue_detected"
            return "clear_dominant_issue_family"

        def _regression_hint(timeline: list[IssueActivityTimelineEntry]) -> str:
            if not timeline:
                return "stable"
            latest = timeline[0]
            if latest.net_issue_delta > 0:
                return "regressing"
            if latest.resolved_issue_count > 0 and latest.net_issue_delta <= 0:
                return "resolving"
            return "stable"

        def _flapping_hint(timeline: list[IssueActivityTimelineEntry]) -> bool:
            recent_deltas = [entry.net_issue_delta for entry in timeline[:3] if entry.net_issue_delta != 0]
            if len(recent_deltas) < 2:
                return False
            return any(delta > 0 for delta in recent_deltas) and any(delta < 0 for delta in recent_deltas)

        actionable_entries.sort(
            key=lambda entry: (
                -entry.active_blocking_issue_count,
                -entry.heat_score,
                -entry.open_issue_count,
                -entry.triaged_issue_count,
                -entry.issue_count,
                entry.ordinal,
                entry.chapter_id,
            )
        )
        return [
            (
                lambda priority, meta, assignment: IssueChapterQueueEntry(
                    chapter_id=entry.chapter_id,
                    ordinal=entry.ordinal,
                    title_src=entry.title_src,
                    chapter_status=entry.chapter_status,
                    issue_count=entry.issue_count,
                    open_issue_count=entry.open_issue_count,
                    triaged_issue_count=entry.triaged_issue_count,
                    blocking_issue_count=entry.blocking_issue_count,
                    active_blocking_issue_count=entry.active_blocking_issue_count,
                    issue_family_count=entry.issue_family_count,
                    dominant_issue_type=entry.dominant_issue_type,
                    dominant_root_cause_layer=entry.dominant_root_cause_layer,
                    dominant_issue_count=entry.dominant_issue_count,
                    latest_issue_at=entry.latest_issue_at,
                    heat_score=entry.heat_score,
                    heat_level=entry.heat_level,
                    queue_rank=index,
                    queue_priority=priority,
                    queue_driver=_driver(entry),
                    needs_immediate_attention=entry.active_blocking_issue_count > 0,
                    oldest_active_issue_at=meta.get("oldest_active_issue_at") if meta else None,
                    age_hours=meta.get("age_hours") if meta else None,
                    age_bucket=_age_bucket(
                        meta.get("age_hours") if meta else None,
                        _sla_target_hours(priority),
                    ),
                    sla_target_hours=_sla_target_hours(priority),
                    sla_status=_sla_status(
                        meta.get("age_hours") if meta else None,
                        _sla_target_hours(priority),
                    ),
                    owner_ready=(
                        entry.dominant_issue_type is not None
                        and entry.dominant_root_cause_layer is not None
                    ),
                    owner_ready_reason=_owner_ready_reason(entry),
                    is_assigned=assignment is not None,
                    assigned_owner_name=(assignment.owner_name if assignment is not None else None),
                    assigned_at=(assignment.assigned_at if assignment is not None else None),
                    latest_activity_bucket_start=(
                        chapter_activity.get(entry.chapter_id, [None])[0].bucket_start
                        if chapter_activity.get(entry.chapter_id)
                        else None
                    ),
                    latest_created_issue_count=(
                        chapter_activity.get(entry.chapter_id, [None])[0].created_issue_count
                        if chapter_activity.get(entry.chapter_id)
                        else 0
                    ),
                    latest_resolved_issue_count=(
                        chapter_activity.get(entry.chapter_id, [None])[0].resolved_issue_count
                        if chapter_activity.get(entry.chapter_id)
                        else 0
                    ),
                    latest_net_issue_delta=(
                        chapter_activity.get(entry.chapter_id, [None])[0].net_issue_delta
                        if chapter_activity.get(entry.chapter_id)
                        else 0
                    ),
                    regression_hint=_regression_hint(chapter_activity.get(entry.chapter_id, [])),
                    flapping_hint=_flapping_hint(chapter_activity.get(entry.chapter_id, [])),
                )
            )(
                _priority(entry),
                chapter_worklist_meta.get(entry.chapter_id, {}),
                chapter_assignment_map.get(entry.chapter_id),
            )
            for index, entry in enumerate(actionable_entries, start=1)
        ]

    def _to_issue_chapter_activity_map(
        self,
        document_id: str,
    ) -> dict[str, list[IssueActivityTimelineEntry]]:
        issues = self.session.scalars(
            select(ReviewIssue).where(
                ReviewIssue.document_id == document_id,
                ReviewIssue.chapter_id.is_not(None),
            )
        ).all()
        grouped: dict[str, list[ReviewIssue]] = {}
        for issue in issues:
            if issue.chapter_id is None:
                continue
            grouped.setdefault(issue.chapter_id, []).append(issue)
        return {
            chapter_id: self._build_issue_activity_timeline(chapter_issues)
            for chapter_id, chapter_issues in grouped.items()
        }

    def _to_issue_chapter_worklist_meta(
        self,
        document_id: str,
    ) -> dict[str, dict[str, object]]:
        rows = self.session.execute(
            select(
                ReviewIssue.chapter_id,
                func.min(ReviewIssue.created_at).label("oldest_active_issue_at"),
            )
            .where(
                ReviewIssue.document_id == document_id,
                ReviewIssue.chapter_id.is_not(None),
                ReviewIssue.status.in_([IssueStatus.OPEN, IssueStatus.TRIAGED]),
            )
            .group_by(ReviewIssue.chapter_id)
        ).all()
        now = _utcnow()
        meta: dict[str, dict[str, object]] = {}
        for chapter_id, oldest_active_issue_at in rows:
            if chapter_id is None or oldest_active_issue_at is None:
                continue
            if oldest_active_issue_at.tzinfo is None:
                oldest_active_issue_at = oldest_active_issue_at.replace(tzinfo=timezone.utc)
            age_hours = max(0, int((now - oldest_active_issue_at).total_seconds() // 3600))
            meta[chapter_id] = {
                "oldest_active_issue_at": oldest_active_issue_at.isoformat(),
                "age_hours": age_hours,
            }
        return meta

    def _to_chapter_assignment_map(
        self,
        document_id: str,
    ) -> dict[str, ChapterWorklistAssignmentSummary]:
        assignments = self.session.scalars(
            select(ChapterWorklistAssignment).where(
                ChapterWorklistAssignment.document_id == document_id
            )
        ).all()
        return {
            assignment.chapter_id: self._to_assignment_summary(assignment)
            for assignment in assignments
        }

    def _to_owner_workload_summary(
        self,
        entries: list[IssueChapterQueueEntry],
    ) -> list[ChapterOwnerWorkloadSummary]:
        grouped: dict[str, list[IssueChapterQueueEntry]] = {}
        for entry in entries:
            if not entry.is_assigned or not entry.assigned_owner_name:
                continue
            grouped.setdefault(entry.assigned_owner_name, []).append(entry)

        summaries: list[ChapterOwnerWorkloadSummary] = []
        for owner_name, owner_entries in grouped.items():
            oldest_active_issue_at = min(
                (
                    entry.oldest_active_issue_at
                    for entry in owner_entries
                    if entry.oldest_active_issue_at is not None
                ),
                default=None,
            )
            latest_issue_at = max(
                (entry.latest_issue_at for entry in owner_entries if entry.latest_issue_at is not None),
                default=None,
            )
            summaries.append(
                ChapterOwnerWorkloadSummary(
                    owner_name=owner_name,
                    assigned_chapter_count=len(owner_entries),
                    immediate_count=sum(1 for entry in owner_entries if entry.queue_priority == "immediate"),
                    high_count=sum(1 for entry in owner_entries if entry.queue_priority == "high"),
                    medium_count=sum(1 for entry in owner_entries if entry.queue_priority == "medium"),
                    breached_count=sum(1 for entry in owner_entries if entry.sla_status == "breached"),
                    due_soon_count=sum(1 for entry in owner_entries if entry.sla_status == "due_soon"),
                    on_track_count=sum(1 for entry in owner_entries if entry.sla_status == "on_track"),
                    owner_ready_count=sum(1 for entry in owner_entries if entry.owner_ready),
                    total_open_issue_count=sum(entry.open_issue_count for entry in owner_entries),
                    total_active_blocking_issue_count=sum(
                        entry.active_blocking_issue_count for entry in owner_entries
                    ),
                    oldest_active_issue_at=oldest_active_issue_at,
                    latest_issue_at=latest_issue_at,
                )
            )

        summaries.sort(
            key=lambda summary: (
                -summary.assigned_chapter_count,
                -summary.immediate_count,
                -summary.breached_count,
                -summary.total_active_blocking_issue_count,
                -summary.total_open_issue_count,
                summary.owner_name,
            )
        )
        return summaries

    def _to_owner_workload_highlights(
        self,
        summaries: list[ChapterOwnerWorkloadSummary],
    ) -> dict[str, ChapterOwnerWorkloadSummary | None]:
        def _pick(candidates: list[ChapterOwnerWorkloadSummary]) -> ChapterOwnerWorkloadSummary | None:
            if not candidates:
                return None
            return sorted(
                candidates,
                key=lambda summary: (
                    -summary.assigned_chapter_count,
                    -summary.immediate_count,
                    -summary.breached_count,
                    -summary.total_active_blocking_issue_count,
                    -summary.total_open_issue_count,
                    summary.owner_name,
                ),
            )[0]

        return {
            "top_loaded_owner": _pick(summaries),
            "top_breached_owner": _pick(
                [summary for summary in summaries if summary.breached_count > 0]
            ),
            "top_blocking_owner": _pick(
                [
                    summary
                    for summary in summaries
                    if summary.total_active_blocking_issue_count > 0
                ]
            ),
            "top_immediate_owner": _pick(
                [summary for summary in summaries if summary.immediate_count > 0]
            ),
        }

    def _to_assignment_summary(
        self,
        assignment: ChapterWorklistAssignment,
    ) -> ChapterWorklistAssignmentSummary:
        return ChapterWorklistAssignmentSummary(
            assignment_id=assignment.id,
            document_id=assignment.document_id,
            chapter_id=assignment.chapter_id,
            owner_name=assignment.owner_name,
            assigned_by=assignment.assigned_by,
            note=assignment.note,
            assigned_at=assignment.assigned_at.isoformat(),
            created_at=assignment.created_at.isoformat(),
            updated_at=assignment.updated_at.isoformat(),
        )

    def _to_issue_activity_breakdown(self, document_id: str) -> list[IssueActivityBreakdownEntry]:
        issues = self.session.scalars(
            select(ReviewIssue).where(ReviewIssue.document_id == document_id)
        ).all()
        if not issues:
            return []

        grouped: dict[tuple[str, str], list[ReviewIssue]] = {}
        for issue in issues:
            key = (issue.issue_type, issue.root_cause_layer.value)
            grouped.setdefault(key, []).append(issue)

        breakdown = [
            IssueActivityBreakdownEntry(
                issue_type=issue_type,
                root_cause_layer=root_cause_layer,
                issue_count=len(group_issues),
                open_issue_count=sum(1 for issue in group_issues if issue.status == IssueStatus.OPEN),
                blocking_issue_count=sum(1 for issue in group_issues if issue.blocking),
                latest_seen_at=max(issue.created_at for issue in group_issues).isoformat(),
                timeline=self._build_issue_activity_timeline(group_issues),
            )
            for (issue_type, root_cause_layer), group_issues in grouped.items()
        ]
        breakdown.sort(
            key=lambda entry: (
                -entry.open_issue_count,
                -entry.blocking_issue_count,
                -entry.issue_count,
                entry.issue_type,
                entry.root_cause_layer,
            )
        )
        return breakdown

    def _to_translation_usage_breakdown_entry_from_json(
        self,
        payload: dict | None,
    ) -> TranslationUsageBreakdownEntry | None:
        if not payload:
            return None
        return TranslationUsageBreakdownEntry(
            model_name=payload.get("model_name", ""),
            worker_name=payload.get("worker_name"),
            provider=payload.get("provider"),
            run_count=payload.get("run_count", 0),
            succeeded_run_count=payload.get("succeeded_run_count", 0),
            total_token_in=payload.get("total_token_in", 0),
            total_token_out=payload.get("total_token_out", 0),
            total_cost_usd=float(payload.get("total_cost_usd", 0.0)),
            total_latency_ms=payload.get("total_latency_ms", 0),
            avg_latency_ms=payload.get("avg_latency_ms"),
            latest_run_at=payload.get("latest_run_at"),
        )

    def _to_translation_usage_timeline_from_json(
        self,
        payload: list[dict] | None,
    ) -> list[TranslationUsageTimelineEntry]:
        if not payload:
            return []
        timeline: list[TranslationUsageTimelineEntry] = []
        for entry in payload:
            timeline.append(
                TranslationUsageTimelineEntry(
                    bucket_start=entry.get("bucket_start", ""),
                    bucket_granularity=entry.get("bucket_granularity", "day"),
                    run_count=entry.get("run_count", 0),
                    succeeded_run_count=entry.get("succeeded_run_count", 0),
                    total_token_in=entry.get("total_token_in", 0),
                    total_token_out=entry.get("total_token_out", 0),
                    total_cost_usd=float(entry.get("total_cost_usd", 0.0)),
                    total_latency_ms=entry.get("total_latency_ms", 0),
                    avg_latency_ms=entry.get("avg_latency_ms"),
                )
            )
        return timeline

    def _to_translation_usage_highlights_from_json(
        self,
        payload: dict | None,
    ) -> TranslationUsageHighlights:
        if not payload:
            return TranslationUsageHighlights(
                top_cost_entry=None,
                top_latency_entry=None,
                top_volume_entry=None,
            )
        return TranslationUsageHighlights(
            top_cost_entry=self._to_translation_usage_breakdown_entry_from_json(
                payload.get("top_cost_entry")
            ),
            top_latency_entry=self._to_translation_usage_breakdown_entry_from_json(
                payload.get("top_latency_entry")
            ),
            top_volume_entry=self._to_translation_usage_breakdown_entry_from_json(
                payload.get("top_volume_entry")
            ),
        )

    def _to_issue_activity_highlights(
        self,
        breakdown: list[IssueActivityBreakdownEntry],
    ) -> IssueActivityHighlights:
        if not breakdown:
            return IssueActivityHighlights(
                top_regressing_entry=None,
                top_resolving_entry=None,
                top_blocking_entry=None,
            )

        def latest_metrics(entry: IssueActivityBreakdownEntry) -> IssueActivityTimelineEntry | None:
            return entry.timeline[0] if entry.timeline else None

        regressing_candidates = [
            entry for entry in breakdown if (latest_metrics(entry).net_issue_delta if latest_metrics(entry) else 0) > 0
        ]
        resolving_candidates = [
            entry
            for entry in breakdown
            if (latest_metrics(entry).resolved_issue_count if latest_metrics(entry) else 0) > 0
        ]
        blocking_candidates = [entry for entry in breakdown if entry.blocking_issue_count > 0]

        top_regressing_entry = (
            max(
                regressing_candidates,
                key=lambda entry: (
                    latest_metrics(entry).net_issue_delta if latest_metrics(entry) else 0,
                    latest_metrics(entry).created_issue_count if latest_metrics(entry) else 0,
                    entry.open_issue_count,
                    entry.issue_type,
                    entry.root_cause_layer,
                ),
            )
            if regressing_candidates
            else None
        )
        top_resolving_entry = (
            max(
                resolving_candidates,
                key=lambda entry: (
                    latest_metrics(entry).resolved_issue_count if latest_metrics(entry) else 0,
                    entry.issue_count,
                    entry.issue_type,
                    entry.root_cause_layer,
                ),
            )
            if resolving_candidates
            else None
        )
        top_blocking_entry = (
            max(
                blocking_candidates,
                key=lambda entry: (
                    entry.blocking_issue_count,
                    entry.open_issue_count,
                    entry.issue_count,
                    entry.issue_type,
                    entry.root_cause_layer,
                ),
            )
            if blocking_candidates
            else None
        )
        return IssueActivityHighlights(
            top_regressing_entry=top_regressing_entry,
            top_resolving_entry=top_resolving_entry,
            top_blocking_entry=top_blocking_entry,
        )

    def _build_issue_activity_timeline(self, issues: list[ReviewIssue]) -> list[IssueActivityTimelineEntry]:
        if not issues:
            return []

        buckets: dict[str, dict[str, int]] = {}

        def _bucket(date_value) -> str:
            return date_value.date().isoformat()

        for issue in issues:
            created_bucket = _bucket(issue.created_at)
            created_entry = buckets.setdefault(
                created_bucket,
                {
                    "created_issue_count": 0,
                    "resolved_issue_count": 0,
                    "wontfix_issue_count": 0,
                    "blocking_created_issue_count": 0,
                },
            )
            created_entry["created_issue_count"] += 1
            if issue.blocking:
                created_entry["blocking_created_issue_count"] += 1

            if issue.status == IssueStatus.RESOLVED:
                resolved_bucket = _bucket(issue.updated_at)
                resolved_entry = buckets.setdefault(
                    resolved_bucket,
                    {
                        "created_issue_count": 0,
                        "resolved_issue_count": 0,
                        "wontfix_issue_count": 0,
                        "blocking_created_issue_count": 0,
                    },
                )
                resolved_entry["resolved_issue_count"] += 1
            elif issue.status == IssueStatus.WONTFIX:
                wontfix_bucket = _bucket(issue.updated_at)
                wontfix_entry = buckets.setdefault(
                    wontfix_bucket,
                    {
                        "created_issue_count": 0,
                        "resolved_issue_count": 0,
                        "wontfix_issue_count": 0,
                        "blocking_created_issue_count": 0,
                    },
                )
                wontfix_entry["wontfix_issue_count"] += 1

        timeline: list[IssueActivityTimelineEntry] = []
        estimated_open_issue_count = 0
        for bucket_start in sorted(buckets.keys()):
            entry = buckets[bucket_start]
            net_issue_delta = (
                entry["created_issue_count"]
                - entry["resolved_issue_count"]
                - entry["wontfix_issue_count"]
            )
            estimated_open_issue_count += net_issue_delta
            timeline.append(
                IssueActivityTimelineEntry(
                    bucket_start=bucket_start,
                    bucket_granularity="day",
                    created_issue_count=entry["created_issue_count"],
                    resolved_issue_count=entry["resolved_issue_count"],
                    wontfix_issue_count=entry["wontfix_issue_count"],
                    blocking_created_issue_count=entry["blocking_created_issue_count"],
                    net_issue_delta=net_issue_delta,
                    estimated_open_issue_count=max(estimated_open_issue_count, 0),
                )
            )
        timeline.sort(key=lambda entry: entry.bucket_start, reverse=True)
        return timeline

    def _to_export_version_evidence_summary(self, export) -> ExportVersionEvidenceSummary:
        bundle = export.input_version_bundle_json or {}
        return ExportVersionEvidenceSummary(
            document_parser_version=bundle.get("document_parser_version"),
            document_segmentation_version=bundle.get("document_segmentation_version"),
            book_profile_version=bundle.get("book_profile_version"),
            chapter_summary_version=bundle.get("chapter_summary_version"),
            active_snapshot_versions=bundle.get("active_snapshot_versions") or {},
        )

    def _open_issue_counts(self, document_id: str) -> dict[str, int]:
        rows = self.session.execute(
            select(ReviewIssue.chapter_id, func.count(ReviewIssue.id))
            .where(
                ReviewIssue.document_id == document_id,
                ReviewIssue.status == IssueStatus.OPEN,
                ReviewIssue.chapter_id.is_not(None),
            )
            .group_by(ReviewIssue.chapter_id)
        ).all()
        return {chapter_id: count for chapter_id, count in rows if chapter_id is not None}

    def _with_auto_followup_telemetry(
        self,
        exc: ExportGateError,
        executions: list[ExportAutoFollowupExecution],
        *,
        requested: bool,
        attempt_limit: int,
        stop_reason: str,
    ) -> ExportGateError:
        return ExportGateError(
            str(exc),
            chapter_id=exc.chapter_id,
            issue_ids=exc.issue_ids,
            followup_actions=exc.followup_actions,
            auto_followup_requested=requested,
            auto_followup_attempt_count=len(executions),
            auto_followup_attempt_limit=attempt_limit,
            auto_followup_stop_reason=stop_reason,
            auto_followup_executions=[execution.to_export_gate_payload() for execution in executions],
        )

    def _record_review_auto_followup_execution(
        self,
        *,
        chapter_id: str,
        execution: ReviewAutoFollowupExecution,
        attempt_index: int,
        attempt_limit: int,
    ) -> None:
        audit = AuditEvent(
            id=stable_id(
                "audit",
                "chapter",
                chapter_id,
                "review.auto_followup.executed",
                execution.action_id,
                str(attempt_index),
            ),
            object_type="chapter",
            object_id=chapter_id,
            action="review.auto_followup.executed",
            actor_type=ActorType.SYSTEM,
            actor_id="document-review-workflow",
            payload_json={
                "attempt_index": attempt_index,
                "attempt_limit": attempt_limit,
                "issue_id": execution.issue_id,
                "action_id": execution.action_id,
                "issue_type": execution.issue_type,
                "action_type": execution.action_type,
                "rerun_scope_type": execution.rerun_scope_type,
                "rerun_scope_ids": execution.rerun_scope_ids,
                "followup_executed": execution.followup_executed,
                "rerun_packet_ids": execution.rerun_packet_ids,
                "rerun_translation_run_ids": execution.rerun_translation_run_ids,
                "issue_resolved": execution.issue_resolved,
            },
            created_at=_utcnow(),
        )
        self.ops_repository.save_audits([audit])
        self.session.flush()

    def _record_review_auto_followup_stop(
        self,
        *,
        chapter_id: str,
        executions: list[ReviewAutoFollowupExecution],
        attempt_limit: int,
        stop_reason: str,
        issue_ids: list[str],
        followup_action_ids: list[str],
    ) -> None:
        audit = AuditEvent(
            id=stable_id(
                "audit",
                "chapter",
                chapter_id,
                "review.auto_followup.stopped",
                stop_reason,
                str(len(executions)),
            ),
            object_type="chapter",
            object_id=chapter_id,
            action="review.auto_followup.stopped",
            actor_type=ActorType.SYSTEM,
            actor_id="document-review-workflow",
            payload_json={
                "stop_reason": stop_reason,
                "attempt_count": len(executions),
                "attempt_limit": attempt_limit,
                "issue_ids": issue_ids,
                "followup_action_ids": followup_action_ids,
            },
            created_at=_utcnow(),
        )
        self.ops_repository.save_audits([audit])
        self.session.flush()

    def _record_document_blocker_repair_execution(
        self,
        *,
        document_id: str,
        chapter_id: str | None,
        execution: DocumentBlockerRepairExecution,
        attempt_index: int,
        round_index: int,
        round_limit: int,
    ) -> None:
        audit = AuditEvent(
            id=stable_id(
                "audit",
                "document",
                document_id,
                "document.blocker_repair.executed",
                execution.action_id,
                str(attempt_index),
            ),
            object_type="document",
            object_id=document_id,
            action="document.blocker_repair.executed",
            actor_type=ActorType.SYSTEM,
            actor_id="document-review-workflow",
            payload_json={
                "chapter_id": chapter_id,
                "attempt_index": attempt_index,
                "round_index": round_index,
                "round_limit": round_limit,
                "issue_id": execution.issue_id,
                "action_id": execution.action_id,
                "issue_type": execution.issue_type,
                "action_type": execution.action_type,
                "rerun_scope_type": execution.rerun_scope_type,
                "rerun_scope_ids": execution.rerun_scope_ids,
                "followup_executed": execution.followup_executed,
                "rerun_packet_ids": execution.rerun_packet_ids,
                "rerun_translation_run_ids": execution.rerun_translation_run_ids,
                "issue_resolved": execution.issue_resolved,
            },
            created_at=_utcnow(),
        )
        self.ops_repository.save_audits([audit])
        self.session.flush()

    def _record_document_blocker_repair_stop(
        self,
        *,
        document_id: str,
        executions: list[DocumentBlockerRepairExecution],
        round_limit: int,
        stop_reason: str,
        issue_ids: list[str],
        followup_action_ids: list[str],
    ) -> None:
        audit = AuditEvent(
            id=stable_id(
                "audit",
                "document",
                document_id,
                "document.blocker_repair.stopped",
                stop_reason,
                str(len(executions)),
            ),
            object_type="document",
            object_id=document_id,
            action="document.blocker_repair.stopped",
            actor_type=ActorType.SYSTEM,
            actor_id="document-review-workflow",
            payload_json={
                "stop_reason": stop_reason,
                "attempt_count": len(executions),
                "round_limit": round_limit,
                "issue_ids": issue_ids,
                "followup_action_ids": followup_action_ids,
            },
            created_at=_utcnow(),
        )
        self.ops_repository.save_audits([audit])
        self.session.flush()

    def _to_stored_quality_summary(
        self,
        summary: PersistedChapterQualitySummary | None,
    ) -> StoredChapterQualitySummary | None:
        if summary is None:
            return None
        return StoredChapterQualitySummary(
            issue_count=summary.issue_count,
            action_count=summary.action_count,
            resolved_issue_count=summary.resolved_issue_count,
            coverage_ok=summary.coverage_ok,
            alignment_ok=summary.alignment_ok,
            term_ok=summary.term_ok,
            format_ok=summary.format_ok,
            blocking_issue_count=summary.blocking_issue_count,
            low_confidence_count=summary.low_confidence_count,
            format_pollution_count=summary.format_pollution_count,
        )

    def _to_naturalness_summary(
        self,
        summary: ReviewNaturalnessSummary | None,
    ) -> NaturalnessSummarySnapshot | None:
        if summary is None:
            return None
        return NaturalnessSummarySnapshot(
            advisory_only=summary.advisory_only,
            style_drift_issue_count=summary.style_drift_issue_count,
            affected_packet_count=summary.affected_packet_count,
            dominant_style_rules=list(summary.dominant_style_rules),
            preferred_hints=list(summary.preferred_hints),
        )

    def _record_export_auto_followup_execution(
        self,
        *,
        chapter_id: str | None,
        document_id: str,
        export_type: ExportType,
        execution: ExportAutoFollowupExecution,
        attempt_index: int,
        attempt_limit: int,
    ) -> None:
        if chapter_id is None:
            return
        audit = AuditEvent(
            id=stable_id(
                "audit",
                "chapter",
                chapter_id,
                "export.auto_followup.executed",
                export_type.value,
                execution.action_id,
                str(attempt_index),
            ),
            object_type="chapter",
            object_id=chapter_id,
            action="export.auto_followup.executed",
            actor_type=ActorType.SYSTEM,
            actor_id="document-export-workflow",
            payload_json={
                "document_id": document_id,
                "export_type": export_type.value,
                "attempt_index": attempt_index,
                "attempt_limit": attempt_limit,
                "issue_id": execution.issue_id,
                "action_id": execution.action_id,
                "action_type": execution.action_type,
                "rerun_scope_type": execution.rerun_scope_type,
                "rerun_scope_ids": execution.rerun_scope_ids,
                "followup_executed": execution.followup_executed,
                "rerun_packet_ids": execution.rerun_packet_ids,
                "rerun_translation_run_ids": execution.rerun_translation_run_ids,
                "issue_resolved": execution.issue_resolved,
            },
            created_at=_utcnow(),
        )
        self.ops_repository.save_audits([audit])
        self.session.flush()

    def _record_export_auto_followup_stop(
        self,
        *,
        chapter_id: str | None,
        document_id: str,
        export_type: ExportType,
        executions: list[ExportAutoFollowupExecution],
        attempt_limit: int,
        stop_reason: str,
        issue_ids: list[str],
        followup_action_ids: list[str],
    ) -> None:
        if chapter_id is None:
            return
        audit = AuditEvent(
            id=stable_id(
                "audit",
                "chapter",
                chapter_id,
                "export.auto_followup.stopped",
                export_type.value,
                stop_reason,
                str(len(executions)),
            ),
            object_type="chapter",
            object_id=chapter_id,
            action="export.auto_followup.stopped",
            actor_type=ActorType.SYSTEM,
            actor_id="document-export-workflow",
            payload_json={
                "document_id": document_id,
                "export_type": export_type.value,
                "stop_reason": stop_reason,
                "attempt_count": len(executions),
                "attempt_limit": attempt_limit,
                "issue_ids": issue_ids,
                "followup_action_ids": followup_action_ids,
                "executions": [execution.to_export_gate_payload() for execution in executions],
            },
            created_at=_utcnow(),
        )
        self.ops_repository.save_audits([audit])
        self.session.flush()
