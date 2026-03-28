from typing import Any, Literal

from pydantic import Field

from book_agent.schemas.common import BaseSchema


class BootstrapDocumentRequest(BaseSchema):
    source_path: str


class StoredChapterQualitySummaryResponse(BaseSchema):
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


class NaturalnessSummaryResponse(BaseSchema):
    advisory_only: bool
    style_drift_issue_count: int
    affected_packet_count: int
    dominant_style_rules: list[str] = Field(default_factory=list)
    preferred_hints: list[str] = Field(default_factory=list)


class ChapterSummaryResponse(BaseSchema):
    chapter_id: str
    ordinal: int
    title_src: str | None = None
    status: str
    risk_level: str | None = None
    parse_confidence: float | None = None
    structure_flags: list[str] = Field(default_factory=list)
    sentence_count: int
    packet_count: int
    open_issue_count: int = 0
    bilingual_export_ready: bool = False
    latest_bilingual_export_at: str | None = None
    pdf_image_summary: dict[str, Any] | None = None
    quality_summary: StoredChapterQualitySummaryResponse | None = None


class DocumentSummaryResponse(BaseSchema):
    document_id: str
    source_type: str
    status: str
    title: str | None = None
    title_src: str | None = None
    title_tgt: str | None = None
    author: str | None = None
    pdf_profile: dict[str, Any] | None = None
    pdf_page_evidence: dict[str, Any] | None = None
    pdf_image_summary: dict[str, Any] | None = None
    chapter_count: int
    block_count: int
    sentence_count: int
    packet_count: int
    open_issue_count: int = 0
    merged_export_ready: bool = False
    latest_merged_export_at: str | None = None
    chapter_bilingual_export_count: int = 0
    latest_run_id: str | None = None
    latest_run_status: str | None = None
    latest_run_current_stage: str | None = None
    latest_run_updated_at: str | None = None
    runtime_v2_context: dict[str, Any] | None = None
    chapters: list[ChapterSummaryResponse] = Field(default_factory=list)


class DocumentHistoryEntryResponse(BaseSchema):
    document_id: str
    source_type: str
    status: str
    title: str | None = None
    title_src: str | None = None
    title_tgt: str | None = None
    author: str | None = None
    source_path: str | None = None
    created_at: str
    updated_at: str
    chapter_count: int
    sentence_count: int
    packet_count: int
    merged_export_ready: bool = False
    latest_merged_export_at: str | None = None
    chapter_bilingual_export_count: int = 0
    latest_run_id: str | None = None
    latest_run_status: str | None = None
    latest_run_current_stage: str | None = None
    latest_run_completed_work_item_count: int | None = None
    latest_run_total_work_item_count: int | None = None


class DocumentHistoryPageResponse(BaseSchema):
    total_count: int
    record_count: int
    offset: int = 0
    limit: int | None = None
    has_more: bool = False
    entries: list[DocumentHistoryEntryResponse] = Field(default_factory=list)


class DocumentHistoryBackfillResponse(BaseSchema):
    imported_document_count: int


class TranslateDocumentRequest(BaseSchema):
    packet_ids: list[str] = Field(default_factory=list)


class TranslateDocumentResponse(BaseSchema):
    document_id: str
    translated_packet_count: int
    skipped_packet_ids: list[str] = Field(default_factory=list)
    translation_run_ids: list[str] = Field(default_factory=list)
    review_required_sentence_ids: list[str] = Field(default_factory=list)
    memory_commit_mode: str = "proposal_first"
    recorded_memory_proposal_count: int = 0


class ChapterMemoryProposalResponse(BaseSchema):
    proposal_id: str
    packet_id: str
    translation_run_id: str
    status: str
    base_snapshot_version: int | None = None
    committed_snapshot_id: str | None = None
    created_at: str
    updated_at: str


class ChapterMemoryProposalListResponse(BaseSchema):
    document_id: str
    chapter_id: str
    status_filter: Literal["proposed", "committed", "rejected"] | None = None
    proposal_count: int
    proposals: list[ChapterMemoryProposalResponse] = Field(default_factory=list)


class ChapterMemoryProposalDecisionResponse(BaseSchema):
    document_id: str
    chapter_id: str
    decision: Literal["approved", "rejected"]
    proposal: ChapterMemoryProposalResponse
    committed_snapshot_id: str | None = None
    committed_snapshot_version: int | None = None


class ChapterMemoryProposalSurfaceResponse(BaseSchema):
    proposal_count: int
    pending_proposal_count: int
    counts_by_status: dict[str, int] = Field(default_factory=dict)
    latest_proposal_updated_at: str | None = None
    active_snapshot_version: int | None = None
    pending_proposals: list[ChapterMemoryProposalResponse] = Field(default_factory=list)


class ChapterReviewResultResponse(BaseSchema):
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
    naturalness_summary: NaturalnessSummaryResponse | None = None


class ReviewDocumentResponse(BaseSchema):
    document_id: str
    total_issue_count: int
    total_action_count: int
    chapter_results: list[ChapterReviewResultResponse] = Field(default_factory=list)


class ExportDocumentRequest(BaseSchema):
    export_type: Literal[
        "review_package",
        "bilingual_html",
        "merged_html",
        "merged_markdown",
        "rebuilt_epub",
        "rebuilt_pdf",
    ]
    auto_execute_followup_on_gate: bool = False
    max_auto_followup_attempts: int = Field(default=3, ge=1)


class ChapterExportResultResponse(BaseSchema):
    chapter_id: str | None = None
    export_id: str
    export_type: str
    status: str
    file_path: str
    manifest_path: str | None = None


class ExportAutoFollowupExecutionResponse(BaseSchema):
    action_id: str
    issue_id: str
    action_type: str
    rerun_scope_type: str
    rerun_scope_ids: list[str] = Field(default_factory=list)
    followup_executed: bool
    rerun_packet_ids: list[str] = Field(default_factory=list)
    rerun_translation_run_ids: list[str] = Field(default_factory=list)
    issue_resolved: bool | None = None


class ExportDocumentResponse(BaseSchema):
    document_id: str
    export_type: str
    document_status: str
    file_path: str | None = None
    manifest_path: str | None = None
    chapter_results: list[ChapterExportResultResponse] = Field(default_factory=list)
    auto_followup_requested: bool = False
    auto_followup_applied: bool = False
    auto_followup_attempt_count: int = 0
    auto_followup_attempt_limit: int | None = None
    auto_followup_executions: list[ExportAutoFollowupExecutionResponse] = Field(default_factory=list)
    runtime_v2_context: dict[str, Any] | None = None


class ExportAutoFollowupSummaryResponse(BaseSchema):
    event_count: int
    executed_event_count: int
    stop_event_count: int
    latest_event_at: str | None = None
    last_stop_reason: str | None = None


class ExportMisalignmentCountSummaryResponse(BaseSchema):
    missing_target_sentence_count: int
    inactive_only_sentence_count: int
    orphan_target_segment_count: int
    inactive_target_segment_with_edges_count: int


class TranslationUsageSummaryResponse(BaseSchema):
    run_count: int
    succeeded_run_count: int
    total_token_in: int
    total_token_out: int
    total_cost_usd: float
    total_latency_ms: int
    avg_latency_ms: float | None = None
    latest_run_at: str | None = None


class TranslationUsageBreakdownEntryResponse(BaseSchema):
    model_name: str
    worker_name: str | None = None
    provider: str | None = None
    run_count: int
    succeeded_run_count: int
    total_token_in: int
    total_token_out: int
    total_cost_usd: float
    total_latency_ms: int
    avg_latency_ms: float | None = None
    latest_run_at: str | None = None


class TranslationUsageTimelineEntryResponse(BaseSchema):
    bucket_start: str
    bucket_granularity: str
    run_count: int
    succeeded_run_count: int
    total_token_in: int
    total_token_out: int
    total_cost_usd: float
    total_latency_ms: int
    avg_latency_ms: float | None = None


class TranslationUsageHighlightsResponse(BaseSchema):
    top_cost_entry: TranslationUsageBreakdownEntryResponse | None = None
    top_latency_entry: TranslationUsageBreakdownEntryResponse | None = None
    top_volume_entry: TranslationUsageBreakdownEntryResponse | None = None


class IssueHotspotEntryResponse(BaseSchema):
    issue_type: str
    root_cause_layer: str
    issue_count: int
    open_issue_count: int
    triaged_issue_count: int
    resolved_issue_count: int
    wontfix_issue_count: int
    blocking_issue_count: int
    chapter_count: int
    latest_seen_at: str | None = None


class IssueChapterPressureEntryResponse(BaseSchema):
    chapter_id: str
    ordinal: int
    title_src: str | None = None
    chapter_status: str
    issue_count: int
    open_issue_count: int
    triaged_issue_count: int
    resolved_issue_count: int
    blocking_issue_count: int
    latest_issue_at: str | None = None


class IssueChapterHighlightsResponse(BaseSchema):
    top_open_chapter: IssueChapterPressureEntryResponse | None = None
    top_blocking_chapter: IssueChapterPressureEntryResponse | None = None
    top_resolved_chapter: IssueChapterPressureEntryResponse | None = None


class IssueChapterBreakdownEntryResponse(BaseSchema):
    chapter_id: str
    ordinal: int
    title_src: str | None = None
    chapter_status: str
    issue_type: str
    root_cause_layer: str
    issue_count: int
    open_issue_count: int
    triaged_issue_count: int
    resolved_issue_count: int
    blocking_issue_count: int
    active_blocking_issue_count: int
    latest_seen_at: str | None = None


class IssueChapterHeatmapEntryResponse(BaseSchema):
    chapter_id: str
    ordinal: int
    title_src: str | None = None
    chapter_status: str
    issue_count: int
    open_issue_count: int
    triaged_issue_count: int
    resolved_issue_count: int
    blocking_issue_count: int
    active_blocking_issue_count: int
    issue_family_count: int
    dominant_issue_type: str | None = None
    dominant_root_cause_layer: str | None = None
    dominant_issue_count: int
    latest_issue_at: str | None = None
    heat_score: int
    heat_level: str


class IssueChapterQueueEntryResponse(BaseSchema):
    chapter_id: str
    ordinal: int
    title_src: str | None = None
    chapter_status: str
    issue_count: int
    open_issue_count: int
    triaged_issue_count: int
    blocking_issue_count: int
    active_blocking_issue_count: int
    issue_family_count: int
    dominant_issue_type: str | None = None
    dominant_root_cause_layer: str | None = None
    dominant_issue_count: int
    latest_issue_at: str | None = None
    heat_score: int
    heat_level: str
    queue_rank: int
    queue_priority: str
    queue_driver: str
    needs_immediate_attention: bool
    oldest_active_issue_at: str | None = None
    age_hours: int | None = None
    age_bucket: str
    sla_target_hours: int | None = None
    sla_status: str
    owner_ready: bool
    owner_ready_reason: str
    is_assigned: bool = False
    assigned_owner_name: str | None = None
    assigned_at: str | None = None
    latest_activity_bucket_start: str | None = None
    latest_created_issue_count: int
    latest_resolved_issue_count: int
    latest_net_issue_delta: int
    regression_hint: str
    flapping_hint: bool
    memory_proposals: "ChapterMemoryProposalQueueSummaryResponse"


class ChapterMemoryProposalQueueSummaryResponse(BaseSchema):
    proposal_count: int
    pending_proposal_count: int
    counts_by_status: dict[str, int] = Field(default_factory=dict)
    latest_proposal_updated_at: str | None = None
    active_snapshot_version: int | None = None


class ChapterOwnerWorkloadResponse(BaseSchema):
    owner_name: str
    assigned_chapter_count: int
    immediate_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    breached_count: int = 0
    due_soon_count: int = 0
    on_track_count: int = 0
    owner_ready_count: int = 0
    total_open_issue_count: int = 0
    total_active_blocking_issue_count: int = 0
    oldest_active_issue_at: str | None = None
    latest_issue_at: str | None = None


class DocumentChapterWorklistResponse(BaseSchema):
    document_id: str
    worklist_count: int
    filtered_worklist_count: int
    entry_count: int
    offset: int = 0
    limit: int | None = None
    has_more: bool = False
    applied_queue_priority_filter: str | None = None
    applied_sla_status_filter: str | None = None
    applied_owner_ready_filter: bool | None = None
    applied_needs_immediate_attention_filter: bool | None = None
    applied_assigned_filter: bool | None = None
    applied_assigned_owner_filter: str | None = None
    queue_priority_counts: dict[str, int] = Field(default_factory=dict)
    sla_status_counts: dict[str, int] = Field(default_factory=dict)
    immediate_attention_count: int = 0
    owner_ready_count: int = 0
    assigned_count: int = 0
    owner_workload_summary: list[ChapterOwnerWorkloadResponse] = Field(default_factory=list)
    owner_workload_highlights: dict[str, ChapterOwnerWorkloadResponse | None] = Field(
        default_factory=dict
    )
    highlights: dict[str, IssueChapterQueueEntryResponse | None] = Field(default_factory=dict)
    entries: list[IssueChapterQueueEntryResponse] = Field(default_factory=list)


class ChapterWorklistIssueResponse(BaseSchema):
    issue_id: str
    issue_type: str
    root_cause_layer: str
    severity: str
    status: str
    blocking: bool
    detector: str
    suggested_action: str | None = None
    created_at: str
    updated_at: str


class ChapterWorklistActionResponse(BaseSchema):
    action_id: str
    issue_id: str
    issue_type: str
    action_type: str
    scope_type: str
    scope_id: str | None = None
    status: str
    created_by: str
    created_at: str
    updated_at: str


class ChapterWorklistAssignmentRequest(BaseSchema):
    owner_name: str
    assigned_by: str
    note: str | None = None


class ChapterWorklistAssignmentClearRequest(BaseSchema):
    cleared_by: str
    note: str | None = None


class ChapterWorklistAssignmentResponse(BaseSchema):
    assignment_id: str
    document_id: str
    chapter_id: str
    owner_name: str
    assigned_by: str
    note: str | None = None
    assigned_at: str
    created_at: str
    updated_at: str


class ChapterWorklistAssignmentClearResponse(BaseSchema):
    document_id: str
    chapter_id: str
    cleared: bool
    cleared_by: str
    note: str | None = None
    cleared_assignment_id: str


class ChapterWorklistAssignmentHistoryEntryResponse(BaseSchema):
    event_id: str
    event_type: str
    owner_name: str | None = None
    performed_by: str | None = None
    note: str | None = None
    created_at: str


class DocumentChapterWorklistDetailResponse(BaseSchema):
    document_id: str
    chapter_id: str
    ordinal: int
    title_src: str | None = None
    chapter_status: str
    packet_count: int
    translated_packet_count: int
    current_issue_count: int
    current_open_issue_count: int
    current_triaged_issue_count: int
    current_active_blocking_issue_count: int
    assignment: ChapterWorklistAssignmentResponse | None = None
    queue_entry: IssueChapterQueueEntryResponse | None = None
    quality_summary: StoredChapterQualitySummaryResponse | None = None
    issue_family_breakdown: list[IssueChapterBreakdownEntryResponse] = Field(default_factory=list)
    recent_issues: list[ChapterWorklistIssueResponse] = Field(default_factory=list)
    recent_actions: list[ChapterWorklistActionResponse] = Field(default_factory=list)
    assignment_history: list[ChapterWorklistAssignmentHistoryEntryResponse] = Field(
        default_factory=list
    )
    memory_proposals: ChapterMemoryProposalSurfaceResponse


class IssueActivityTimelineEntryResponse(BaseSchema):
    bucket_start: str
    bucket_granularity: str
    created_issue_count: int
    resolved_issue_count: int
    wontfix_issue_count: int
    blocking_created_issue_count: int
    net_issue_delta: int
    estimated_open_issue_count: int


class IssueActivityBreakdownEntryResponse(BaseSchema):
    issue_type: str
    root_cause_layer: str
    issue_count: int
    open_issue_count: int
    blocking_issue_count: int
    latest_seen_at: str | None = None
    timeline: list[IssueActivityTimelineEntryResponse] = Field(default_factory=list)


class IssueActivityHighlightsResponse(BaseSchema):
    top_regressing_entry: IssueActivityBreakdownEntryResponse | None = None
    top_resolving_entry: IssueActivityBreakdownEntryResponse | None = None
    top_blocking_entry: IssueActivityBreakdownEntryResponse | None = None


class ExportIssueStatusSummaryResponse(BaseSchema):
    issue_count: int
    open_issue_count: int
    resolved_issue_count: int
    blocking_issue_count: int


class ExportVersionEvidenceSummaryResponse(BaseSchema):
    document_parser_version: int | None = None
    document_segmentation_version: int | None = None
    book_profile_version: int | None = None
    chapter_summary_version: int | None = None
    active_snapshot_versions: dict[str, int] = Field(default_factory=dict)


class ExportRecordSummaryResponse(BaseSchema):
    export_id: str
    export_type: str
    status: str
    file_path: str
    manifest_path: str | None = None
    chapter_id: str | None = None
    chapter_summary_version: int | None = None
    created_at: str
    updated_at: str
    translation_usage_summary: TranslationUsageSummaryResponse | None = None
    translation_usage_breakdown: list[TranslationUsageBreakdownEntryResponse] = Field(default_factory=list)
    translation_usage_timeline: list[TranslationUsageTimelineEntryResponse] = Field(default_factory=list)
    translation_usage_highlights: TranslationUsageHighlightsResponse | None = None
    export_auto_followup_summary: ExportAutoFollowupSummaryResponse | None = None
    export_time_misalignment_counts: ExportMisalignmentCountSummaryResponse | None = None


class DocumentExportDashboardResponse(BaseSchema):
    document_id: str
    export_count: int
    successful_export_count: int
    filtered_export_count: int
    record_count: int
    offset: int = 0
    limit: int | None = None
    has_more: bool = False
    applied_export_type_filter: str | None = None
    applied_status_filter: str | None = None
    latest_export_at: str | None = None
    export_counts_by_type: dict[str, int] = Field(default_factory=dict)
    latest_export_ids_by_type: dict[str, str] = Field(default_factory=dict)
    total_auto_followup_executed_count: int = 0
    translation_usage_summary: TranslationUsageSummaryResponse | None = None
    translation_usage_breakdown: list[TranslationUsageBreakdownEntryResponse] = Field(default_factory=list)
    translation_usage_timeline: list[TranslationUsageTimelineEntryResponse] = Field(default_factory=list)
    translation_usage_highlights: TranslationUsageHighlightsResponse
    issue_hotspots: list[IssueHotspotEntryResponse] = Field(default_factory=list)
    issue_chapter_pressure: list[IssueChapterPressureEntryResponse] = Field(default_factory=list)
    issue_chapter_highlights: IssueChapterHighlightsResponse
    issue_chapter_breakdown: list[IssueChapterBreakdownEntryResponse] = Field(default_factory=list)
    issue_chapter_heatmap: list[IssueChapterHeatmapEntryResponse] = Field(default_factory=list)
    issue_chapter_queue: list[IssueChapterQueueEntryResponse] = Field(default_factory=list)
    issue_activity_timeline: list[IssueActivityTimelineEntryResponse] = Field(default_factory=list)
    issue_activity_breakdown: list[IssueActivityBreakdownEntryResponse] = Field(default_factory=list)
    issue_activity_highlights: IssueActivityHighlightsResponse
    records: list[ExportRecordSummaryResponse] = Field(default_factory=list)


class ExportDetailResponse(BaseSchema):
    document_id: str
    export_id: str
    export_type: str
    status: str
    file_path: str
    manifest_path: str | None = None
    chapter_id: str | None = None
    sentence_count: int
    target_segment_count: int
    created_at: str
    updated_at: str
    translation_usage_summary: TranslationUsageSummaryResponse | None = None
    translation_usage_breakdown: list[TranslationUsageBreakdownEntryResponse] = Field(default_factory=list)
    translation_usage_timeline: list[TranslationUsageTimelineEntryResponse] = Field(default_factory=list)
    translation_usage_highlights: TranslationUsageHighlightsResponse | None = None
    issue_status_summary: ExportIssueStatusSummaryResponse | None = None
    export_auto_followup_summary: ExportAutoFollowupSummaryResponse | None = None
    export_time_misalignment_counts: ExportMisalignmentCountSummaryResponse | None = None
    version_evidence_summary: ExportVersionEvidenceSummaryResponse
    runtime_v2_context: dict[str, Any] | None = None


class RebuiltSnapshotEvidenceResponse(BaseSchema):
    snapshot_id: str
    snapshot_type: str
    version: int


class ExecuteActionResponse(BaseSchema):
    action_id: str
    status: str
    invalidation_count: int
    rerun_scope_type: str
    rerun_scope_ids: list[str] = Field(default_factory=list)
    followup_executed: bool = False
    rebuild_applied: bool = False
    rebuilt_packet_ids: list[str] = Field(default_factory=list)
    rebuilt_snapshot_ids: list[str] = Field(default_factory=list)
    rebuilt_snapshots: list[RebuiltSnapshotEvidenceResponse] = Field(default_factory=list)
    chapter_brief_version: int | None = None
    termbase_version: int | None = None
    entity_snapshot_version: int | None = None
    rerun_packet_ids: list[str] = Field(default_factory=list)
    rerun_translation_run_ids: list[str] = Field(default_factory=list)
    issue_resolved: bool | None = None
    recheck_issue_count: int | None = None
