"""Result types returned by the document workflow use cases and queries."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from book_agent.services.actions import ActionExecutionArtifacts
from book_agent.services.rerun import RerunExecutionArtifacts


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
    memory_commit_mode: str
    recorded_memory_proposal_count: int


@dataclass(slots=True)
class ChapterMemoryProposalSummary:
    proposal_id: str
    packet_id: str
    translation_run_id: str
    status: str
    base_snapshot_version: int | None
    committed_snapshot_id: str | None
    created_at: str
    updated_at: str
    last_decision: "ChapterMemoryProposalDecisionAuditSummary | None" = None


@dataclass(slots=True)
class ChapterMemoryProposalDecisionResult:
    document_id: str
    chapter_id: str
    decision: str
    proposal: ChapterMemoryProposalSummary
    committed_snapshot_id: str | None = None
    committed_snapshot_version: int | None = None


@dataclass(slots=True)
class ChapterMemoryProposalDecisionAuditSummary:
    proposal_id: str
    decision: str
    actor_type: str
    actor_id: str | None
    note: str | None
    created_at: str


@dataclass(slots=True)
class ChapterMemoryProposalSurface:
    proposal_count: int
    pending_proposal_count: int
    counts_by_status: dict[str, int]
    latest_proposal_updated_at: str | None
    active_snapshot_version: int | None
    pending_proposals: list[ChapterMemoryProposalSummary]
    recent_decisions: list[ChapterMemoryProposalDecisionAuditSummary]


@dataclass(slots=True)
class ChapterMemoryProposalQueueSummary:
    proposal_count: int
    pending_proposal_count: int
    counts_by_status: dict[str, int]
    latest_proposal_updated_at: str | None
    active_snapshot_version: int | None


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
class ChapterReviewSkip:
    """Why a chapter was excluded from review.

    Phase 3 (state-consistency refactor) made the silent ``continue`` in
    ``_review_document_impl`` explicit: every skipped chapter now surfaces
    its reason and the packet-level counts so the UI can render a
    ``partial`` review state rather than pretending the chapter was
    ``succeeded`` with zero issues.
    """

    chapter_id: str
    reason: str
    pending_packet_count: int
    failed_packet_count: int


@dataclass(slots=True)
class DocumentReviewResult:
    document_id: str
    total_issue_count: int
    total_action_count: int
    chapter_results: list[ChapterReviewResult]
    skipped_chapters: list[ChapterReviewSkip] = field(default_factory=list)
    total_chapter_count: int = 0
    auto_followup_requested: bool = False
    auto_followup_applied: bool = False
    auto_followup_attempt_count: int = 0
    auto_followup_attempt_limit: int | None = None
    auto_followup_executions: list["ReviewAutoFollowupExecution"] | None = None

    @property
    def examined_chapter_count(self) -> int:
        return len(self.chapter_results)

    @property
    def skipped_chapter_count(self) -> int:
        return len(self.skipped_chapters)


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
    memory_proposals: ChapterMemoryProposalQueueSummary


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
class ChapterWorklistTimelineEntry:
    event_id: str
    source_kind: str
    event_kind: str
    created_at: str
    actor_name: str | None = None
    note: str | None = None
    issue_id: str | None = None
    issue_type: str | None = None
    action_id: str | None = None
    action_type: str | None = None
    scope_type: str | None = None
    scope_id: str | None = None
    status: str | None = None
    proposal_id: str | None = None
    decision: str | None = None
    owner_name: str | None = None


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
    memory_proposals: ChapterMemoryProposalSurface
    timeline: list[ChapterWorklistTimelineEntry]


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
