"""Pure aggregations behind the export dashboard and chapter worklist."""

from __future__ import annotations

from datetime import datetime, timezone

from book_agent.application.read_models import (
    ChapterMemoryProposalDecisionAuditSummary,
    ChapterMemoryProposalQueueSummary,
    ChapterOwnerWorkloadSummary,
    ChapterWorklistAction,
    ChapterWorklistAssignmentHistoryEntry,
    ChapterWorklistAssignmentSummary,
    ChapterWorklistTimelineEntry,
    ExportAutoFollowupSummary,
    ExportIssueStatusSummary,
    ExportMisalignmentCountSummary,
    ExportRecordSummary,
    ExportVersionEvidenceSummary,
    IssueActivityBreakdownEntry,
    IssueActivityHighlights,
    IssueActivityTimelineEntry,
    IssueChapterBreakdownEntry,
    IssueChapterHeatmapEntry,
    IssueChapterHighlights,
    IssueChapterPressureEntry,
    IssueChapterQueueEntry,
    NaturalnessSummarySnapshot,
    StoredChapterQualitySummary,
    TranslationUsageBreakdownEntry,
    TranslationUsageHighlights,
    TranslationUsageSummary,
    TranslationUsageTimelineEntry,
)
from book_agent.domain.enums import (
    IssueStatus,
    MemoryProposalStatus,
)
from book_agent.domain.models import (
    ChapterWorklistAssignment,
)
from book_agent.domain.models.review import ChapterQualitySummary as PersistedChapterQualitySummary
from book_agent.domain.models.review import (
    ReviewIssue,
)
from book_agent.services.review import NaturalnessSummary as ReviewNaturalnessSummary


def issue_chapter_worklist_highlights(
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


def chapter_worklist_timeline(
    *,
    recent_actions: list[ChapterWorklistAction],
    assignment_history: list[ChapterWorklistAssignmentHistoryEntry],
    memory_decisions: list[ChapterMemoryProposalDecisionAuditSummary],
    limit: int = 20,
) -> list[ChapterWorklistTimelineEntry]:
    timeline: list[ChapterWorklistTimelineEntry] = [
        ChapterWorklistTimelineEntry(
            event_id=action.action_id,
            source_kind="action",
            event_kind="issue_action",
            created_at=action.updated_at,
            actor_name=action.created_by,
            issue_id=action.issue_id,
            issue_type=action.issue_type,
            action_id=action.action_id,
            action_type=action.action_type,
            scope_type=action.scope_type,
            scope_id=action.scope_id,
            status=action.status,
        )
        for action in recent_actions
    ]
    timeline.extend(
        ChapterWorklistTimelineEntry(
            event_id=event.event_id,
            source_kind="assignment",
            event_kind=event.event_type,
            created_at=event.created_at,
            actor_name=event.performed_by,
            note=event.note,
            owner_name=event.owner_name,
        )
        for event in assignment_history
    )
    timeline.extend(
        ChapterWorklistTimelineEntry(
            event_id=audit.proposal_id,
            source_kind="memory_proposal",
            event_kind=audit.decision,
            created_at=audit.created_at,
            actor_name=audit.actor_id,
            note=audit.note,
            proposal_id=audit.proposal_id,
            decision=audit.decision,
        )
        for audit in memory_decisions
    )
    timeline.sort(key=lambda entry: timeline_sort_key(entry.created_at), reverse=True)
    return timeline[:limit]


def timeline_sort_key(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def export_record_summary(export) -> ExportRecordSummary:
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
        translation_usage_summary=translation_usage_summary_from_json(
            bundle.get("translation_usage_summary")
        ),
        translation_usage_breakdown=translation_usage_breakdown_from_json(
            bundle.get("translation_usage_breakdown")
        ),
        translation_usage_timeline=translation_usage_timeline_from_json(
            bundle.get("translation_usage_timeline")
        ),
        translation_usage_highlights=translation_usage_highlights_from_json(
            bundle.get("translation_usage_highlights")
        ),
        export_auto_followup_summary=export_auto_followup_summary(export),
        export_time_misalignment_counts=export_misalignment_summary(export),
    )


def export_auto_followup_summary(export) -> ExportAutoFollowupSummary | None:
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


def export_misalignment_summary(export) -> ExportMisalignmentCountSummary | None:
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


def translation_usage_summary_from_runs(
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


def translation_usage_breakdown_from_runs(
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


def translation_usage_timeline_from_runs(
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


def translation_usage_highlights_from_runs(
    translation_runs,
) -> TranslationUsageHighlights:
    breakdown = translation_usage_breakdown_from_runs(translation_runs)
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


def translation_usage_summary_from_json(payload: dict | None) -> TranslationUsageSummary | None:
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


def translation_usage_breakdown_from_json(
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


def translation_usage_breakdown_entry_from_json(
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


def translation_usage_timeline_from_json(
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


def translation_usage_highlights_from_json(
    payload: dict | None,
) -> TranslationUsageHighlights:
    if not payload:
        return TranslationUsageHighlights(
            top_cost_entry=None,
            top_latency_entry=None,
            top_volume_entry=None,
        )
    return TranslationUsageHighlights(
        top_cost_entry=translation_usage_breakdown_entry_from_json(
            payload.get("top_cost_entry")
        ),
        top_latency_entry=translation_usage_breakdown_entry_from_json(
            payload.get("top_latency_entry")
        ),
        top_volume_entry=translation_usage_breakdown_entry_from_json(
            payload.get("top_volume_entry")
        ),
    )


def export_issue_status_summary(payload: dict | None) -> ExportIssueStatusSummary | None:
    if not payload:
        return None
    return ExportIssueStatusSummary(
        issue_count=payload.get("issue_count", 0),
        open_issue_count=payload.get("open_issue_count", 0),
        resolved_issue_count=payload.get("resolved_issue_count", 0),
        blocking_issue_count=payload.get("blocking_issue_count", 0),
    )


def export_version_evidence_summary(export) -> ExportVersionEvidenceSummary:
    bundle = export.input_version_bundle_json or {}
    return ExportVersionEvidenceSummary(
        document_parser_version=bundle.get("document_parser_version"),
        document_segmentation_version=bundle.get("document_segmentation_version"),
        book_profile_version=bundle.get("book_profile_version"),
        chapter_summary_version=bundle.get("chapter_summary_version"),
        active_snapshot_versions=bundle.get("active_snapshot_versions") or {},
    )


def issue_chapter_highlights(
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


def issue_chapter_heatmap(
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


def issue_chapter_queue(
    heatmap: list[IssueChapterHeatmapEntry],
    chapter_activity: dict[str, list[IssueActivityTimelineEntry]],
    chapter_worklist_meta: dict[str, dict[str, object]],
    chapter_assignment_map: dict[str, ChapterWorklistAssignmentSummary],
    chapter_memory_proposal_map: dict[str, ChapterMemoryProposalQueueSummary],
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
                memory_proposals=chapter_memory_proposal_map.get(
                    entry.chapter_id,
                    ChapterMemoryProposalQueueSummary(
                        proposal_count=0,
                        pending_proposal_count=0,
                        counts_by_status={
                            MemoryProposalStatus.PROPOSED.value: 0,
                            MemoryProposalStatus.COMMITTED.value: 0,
                            MemoryProposalStatus.REJECTED.value: 0,
                        },
                        latest_proposal_updated_at=None,
                        active_snapshot_version=None,
                    ),
                ),
            )
        )(
            _priority(entry),
            chapter_worklist_meta.get(entry.chapter_id, {}),
            chapter_assignment_map.get(entry.chapter_id),
        )
        for index, entry in enumerate(actionable_entries, start=1)
    ]


def owner_workload_summary(
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


def owner_workload_highlights(
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


def assignment_summary(
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


def issue_activity_highlights(
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


def build_issue_activity_timeline(issues: list[ReviewIssue]) -> list[IssueActivityTimelineEntry]:
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


def stored_quality_summary(
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


def naturalness_summary(
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
