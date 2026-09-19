"""Manifest and review-package evidence payloads.

Quality summaries, PDF page and image evidence, version evidence, auto followup
telemetry and translation usage summaries.
"""


from __future__ import annotations

from pathlib import Path

from book_agent.domain.enums import (
    IssueStatus,
)
from book_agent.export.common import (
    _SPECIAL_PDF_PAGE_FAMILIES,
)
from book_agent.export.models import (
    ExportMisalignmentEvidence,
    MergedRenderBlock,
)
from book_agent.infra.repositories.export import ChapterExportBundle, DocumentExportBundle


def misalignment_evidence_payload(evidence: ExportMisalignmentEvidence) -> dict:
    return {
        "has_anomalies": evidence.has_anomalies,
        "missing_target_sentence_ids": evidence.missing_target_sentence_ids,
        "sentence_ids_with_only_inactive_targets": evidence.sentence_ids_with_only_inactive_targets,
        "orphan_target_segment_ids": evidence.orphan_target_segment_ids,
        "inactive_target_segment_ids_with_edges": evidence.inactive_target_segment_ids_with_edges,
    }


def quality_summary_payload(bundle: ChapterExportBundle) -> dict | None:
    if bundle.quality_summary is None:
        return None
    return {
        "issue_count": bundle.quality_summary.issue_count,
        "action_count": bundle.quality_summary.action_count,
        "resolved_issue_count": bundle.quality_summary.resolved_issue_count,
        "coverage_ok": bundle.quality_summary.coverage_ok,
        "alignment_ok": bundle.quality_summary.alignment_ok,
        "term_ok": bundle.quality_summary.term_ok,
        "format_ok": bundle.quality_summary.format_ok,
        "blocking_issue_count": bundle.quality_summary.blocking_issue_count,
        "low_confidence_count": bundle.quality_summary.low_confidence_count,
        "format_pollution_count": bundle.quality_summary.format_pollution_count,
    }


def pdf_page_evidence_payload(bundle: ChapterExportBundle) -> dict | None:
    metadata = bundle.document.metadata_json or {}
    evidence = metadata.get("pdf_page_evidence")
    if not isinstance(evidence, dict):
        return None

    pages = evidence.get("pdf_pages")
    outline_entries = evidence.get("pdf_outline_entries")
    if not isinstance(pages, list):
        return None
    if not isinstance(outline_entries, list):
        outline_entries = []

    chapter_metadata = bundle.chapter.metadata_json or {}
    page_start = chapter_metadata.get("source_page_start")
    page_end = chapter_metadata.get("source_page_end")
    if not isinstance(page_start, int) or not isinstance(page_end, int):
        return evidence

    page_range = {"start": page_start, "end": page_end}
    filtered_pages = [
        page
        for page in pages
        if isinstance(page, dict)
        and isinstance(page.get("page_number"), int)
        and page_start <= int(page["page_number"]) <= page_end
    ]
    filtered_outline_entries = [
        entry
        for entry in outline_entries
        if isinstance(entry, dict)
        and isinstance(entry.get("page_number"), int)
        and page_start <= int(entry["page_number"]) <= page_end
    ]
    return {
        "schema_version": evidence.get("schema_version"),
        "document_page_count": evidence.get("page_count"),
        "page_count": len(filtered_pages),
        "page_range": page_range,
        "pdf_pages": filtered_pages,
        "pdf_outline_entries": filtered_outline_entries,
    }


def pdf_image_evidence_payload(bundle: ChapterExportBundle) -> dict:
    block_by_id = {block.id: block for block in bundle.blocks}
    images_payload: list[dict[str, object]] = []
    caption_linked_count = 0
    for image in sorted(bundle.document_images, key=lambda item: (item.page_number, item.id)):
        storage_path = str(image.storage_path or "")
        storage_exists = bool(storage_path) and Path(storage_path).is_file()
        metadata = dict(image.metadata_json or {})
        linked_caption_block_id = metadata.get("linked_caption_block_id")
        linked_caption_block = (
            block_by_id.get(str(linked_caption_block_id))
            if isinstance(linked_caption_block_id, str)
            else None
        )
        linked_caption_text = (
            linked_caption_block.source_text
            if linked_caption_block is not None and linked_caption_block.source_text
            else None
        )
        caption_linked = bool(linked_caption_block_id)
        if caption_linked:
            caption_linked_count += 1
        images_payload.append(
            {
                "image_id": image.id,
                "block_id": image.block_id,
                "page_number": image.page_number,
                "image_type": image.image_type,
                "storage_path": storage_path,
                "storage_exists": storage_exists,
                "storage_status": (image.metadata_json or {}).get("storage_status"),
                "width_px": image.width_px,
                "height_px": image.height_px,
                "alt_text": image.alt_text,
                "caption_linked": caption_linked,
                "linked_caption_block_id": linked_caption_block_id,
                "linked_caption_text": linked_caption_text,
                "bbox_json": image.bbox_json,
                "metadata": metadata,
            }
        )
    return {
        "schema_version": 1,
        "image_count": len(images_payload),
        "caption_linked_count": caption_linked_count,
        "uncaptioned_image_count": len(images_payload) - caption_linked_count,
        "images": images_payload,
    }


def pdf_page_preserve_policy(page_contract: dict[str, object]) -> str:
    page_family = str(page_contract.get("page_family") or "body")
    block_count = int(page_contract.get("block_count") or 0)
    preserved_block_count = int(page_contract.get("preserved_block_count") or 0)
    source_only_block_count = int(page_contract.get("source_only_block_count") or 0)
    if block_count > 0 and source_only_block_count == block_count:
        return "source_only"
    if source_only_block_count > 0:
        return "mixed_source_only"
    if preserved_block_count > 0:
        return "preserved_artifacts"
    if page_family == "toc" and block_count == 0:
        return "filtered_noise_only"
    if page_family == "backmatter":
        return "source_only_expected"
    if page_family in _SPECIAL_PDF_PAGE_FAMILIES:
        return "family_only"
    return "none"


def version_evidence_payload(bundle: ChapterExportBundle) -> dict:
    return {
        "document": {
            "document_id": bundle.document.id,
            "parser_version": bundle.document.parser_version,
            "segmentation_version": bundle.document.segmentation_version,
            "active_book_profile_version": bundle.document.active_book_profile_version,
        },
        "chapter": {
            "chapter_id": bundle.chapter.id,
            "status": bundle.chapter.status.value,
            "summary_version": bundle.chapter.summary_version,
        },
        "book_profile": (
            {
                "profile_id": bundle.book_profile.id,
                "version": bundle.book_profile.version,
            }
            if bundle.book_profile is not None
            else None
        ),
        "active_snapshots": [
            {
                "snapshot_id": snapshot.id,
                "snapshot_type": snapshot.snapshot_type.value,
                "scope_type": snapshot.scope_type.value,
                "scope_id": snapshot.scope_id,
                "version": snapshot.version,
            }
            for snapshot in sorted(bundle.active_snapshots, key=lambda item: (item.snapshot_type.value, item.version))
        ],
        "packet_context_versions": [
            {
                "packet_id": packet.id,
                "packet_type": packet.packet_type.value,
                "status": packet.status.value,
                "book_profile_version": packet.book_profile_version,
                "chapter_brief_version": packet.chapter_brief_version,
                "termbase_version": packet.termbase_version,
                "entity_snapshot_version": packet.entity_snapshot_version,
                "style_snapshot_version": packet.style_snapshot_version,
            }
            for packet in bundle.packets
        ],
    }


def recent_repair_events_payload(bundle: ChapterExportBundle) -> list[dict]:
    return [
        {
            "audit_id": event.id,
            "object_type": event.object_type,
            "object_id": event.object_id,
            "action": event.action,
            "actor_id": event.actor_id,
            "created_at": event.created_at.isoformat(),
            "payload": event.payload_json,
        }
        for event in repair_audits(bundle)
    ]


def repair_audits(bundle: ChapterExportBundle) -> list[object]:
    repair_actions = {"snapshot.rebuilt", "packet.rebuilt", "packet.realigned"}
    events = [event for event in bundle.audit_events if event.action in repair_actions]
    return events[:20]


def export_auto_followup_evidence_payload(bundle: ChapterExportBundle) -> dict:
    events = export_auto_followup_audits(bundle)
    executed_events = [event for event in events if event.action == "export.auto_followup.executed"]
    stopped_events = [event for event in events if event.action == "export.auto_followup.stopped"]
    return {
        "event_count": len(events),
        "executed_event_count": len(executed_events),
        "stop_event_count": len(stopped_events),
        "events": [
            {
                "audit_id": event.id,
                "object_type": event.object_type,
                "object_id": event.object_id,
                "action": event.action,
                "actor_id": event.actor_id,
                "created_at": event.created_at.isoformat(),
                "payload": event.payload_json,
            }
            for event in events
        ],
    }


def export_auto_followup_summary(bundle: ChapterExportBundle) -> dict:
    events = export_auto_followup_audits(bundle)
    executed_events = [event for event in events if event.action == "export.auto_followup.executed"]
    stopped_events = [event for event in events if event.action == "export.auto_followup.stopped"]
    latest_event = events[0] if events else None
    latest_stop_event = stopped_events[0] if stopped_events else None
    return {
        "event_count": len(events),
        "executed_event_count": len(executed_events),
        "stop_event_count": len(stopped_events),
        "latest_event_at": latest_event.created_at.isoformat() if latest_event is not None else None,
        "last_stop_reason": (
            latest_stop_event.payload_json.get("stop_reason")
            if latest_stop_event is not None
            else None
        ),
    }


def export_auto_followup_audits(bundle: ChapterExportBundle) -> list[object]:
    auto_followup_actions = {"export.auto_followup.executed", "export.auto_followup.stopped"}
    events = [event for event in bundle.audit_events if event.action in auto_followup_actions]
    return events[:20]


def merged_render_summary(
    visible_chapters: list[tuple[int, ChapterExportBundle, list[MergedRenderBlock], str | None]],
) -> dict[str, object]:
    render_mode_counts: dict[str, int] = {}
    expected_source_only_count = 0
    for _visible_ordinal, _chapter_bundle, render_blocks, _title_text in visible_chapters:
        for block in render_blocks:
            render_mode_counts[block.render_mode] = render_mode_counts.get(block.render_mode, 0) + 1
            if block.is_expected_source_only:
                expected_source_only_count += 1
    return {
        "chapter_count": len(visible_chapters),
        "render_mode_counts": render_mode_counts,
        "expected_source_only_block_count": expected_source_only_count,
    }


def pdf_image_summary_payload(bundle: DocumentExportBundle) -> dict:
    image_type_counts: dict[str, int] = {}
    chapter_image_counts: dict[str, int] = {}
    image_count = 0
    stored_asset_count = 0
    caption_linked_count = 0
    for chapter_bundle in bundle.chapters:
        chapter_count = len(chapter_bundle.document_images)
        if chapter_count:
            chapter_image_counts[chapter_bundle.chapter.id] = chapter_count
        for image in chapter_bundle.document_images:
            image_count += 1
            image_type_counts[image.image_type] = image_type_counts.get(image.image_type, 0) + 1
            storage_path = str(image.storage_path or "")
            if storage_path and Path(storage_path).is_file():
                stored_asset_count += 1
            if (image.metadata_json or {}).get("linked_caption_block_id"):
                caption_linked_count += 1
    return {
        "schema_version": 1,
        "image_count": image_count,
        "stored_asset_count": stored_asset_count,
        "caption_linked_count": caption_linked_count,
        "uncaptioned_image_count": image_count - caption_linked_count,
        "image_type_counts": image_type_counts,
        "chapter_image_counts": chapter_image_counts,
    }


def issue_status_summary(bundle: ChapterExportBundle) -> dict[str, int]:
    blocking_issue_count = sum(1 for issue in bundle.review_issues if issue.blocking)
    open_issue_count = sum(1 for issue in bundle.review_issues if issue.status == IssueStatus.OPEN)
    resolved_issue_count = sum(1 for issue in bundle.review_issues if issue.status == IssueStatus.RESOLVED)
    return {
        "issue_count": len(bundle.review_issues),
        "open_issue_count": open_issue_count,
        "resolved_issue_count": resolved_issue_count,
        "blocking_issue_count": blocking_issue_count,
    }


def document_issue_status_summary(bundle: DocumentExportBundle) -> dict[str, int]:
    all_issues = [issue for chapter in bundle.chapters for issue in chapter.review_issues]
    blocking_issue_count = sum(1 for issue in all_issues if issue.blocking)
    open_issue_count = sum(1 for issue in all_issues if issue.status == IssueStatus.OPEN)
    resolved_issue_count = sum(1 for issue in all_issues if issue.status == IssueStatus.RESOLVED)
    return {
        "issue_count": len(all_issues),
        "open_issue_count": open_issue_count,
        "resolved_issue_count": resolved_issue_count,
        "blocking_issue_count": blocking_issue_count,
    }


def collect_document_translation_runs(bundle: "DocumentExportBundle") -> list[object]:
    return [
        run
        for chapter_bundle in bundle.chapters
        for run in chapter_bundle.translation_runs
    ]


def translation_usage_summary(bundle: ChapterExportBundle) -> dict[str, object]:
    return translation_usage_summary_from_runs(bundle.translation_runs)


def translation_usage_summary_from_runs(runs: list[object]) -> dict[str, object]:
    run_count = len(runs)
    succeeded_run_count = sum(1 for run in runs if run.status.value == "succeeded")
    total_token_in = sum(run.token_in or 0 for run in runs)
    total_token_out = sum(run.token_out or 0 for run in runs)
    total_cost_usd = round(sum(float(run.cost_usd or 0) for run in runs), 6)
    latency_values = [run.latency_ms for run in runs if run.latency_ms is not None]
    total_latency_ms = sum(latency_values)
    avg_latency_ms = round(total_latency_ms / len(latency_values), 3) if latency_values else None
    latest_run_at = max(run.created_at for run in runs).isoformat() if runs else None
    return {
        "run_count": run_count,
        "succeeded_run_count": succeeded_run_count,
        "total_token_in": total_token_in,
        "total_token_out": total_token_out,
        "total_cost_usd": total_cost_usd,
        "total_latency_ms": total_latency_ms,
        "avg_latency_ms": avg_latency_ms,
        "latest_run_at": latest_run_at,
    }


def translation_usage_breakdown(bundle: ChapterExportBundle) -> list[dict[str, object]]:
    return translation_usage_breakdown_from_runs(bundle.translation_runs)


def translation_usage_breakdown_from_runs(runs: list[object]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str | None, str | None], list] = {}
    for run in runs:
        model_config = run.model_config_json or {}
        key = (
            run.model_name,
            model_config.get("worker"),
            model_config.get("provider"),
        )
        grouped.setdefault(key, []).append(run)

    breakdown: list[dict[str, object]] = []
    for (model_name, worker_name, provider), runs in grouped.items():
        latency_values = [run.latency_ms for run in runs if run.latency_ms is not None]
        total_latency_ms = sum(latency_values)
        avg_latency_ms = round(total_latency_ms / len(latency_values), 3) if latency_values else None
        breakdown.append(
            {
                "model_name": model_name,
                "worker_name": worker_name,
                "provider": provider,
                "run_count": len(runs),
                "succeeded_run_count": sum(1 for run in runs if run.status.value == "succeeded"),
                "total_token_in": sum(run.token_in or 0 for run in runs),
                "total_token_out": sum(run.token_out or 0 for run in runs),
                "total_cost_usd": round(sum(float(run.cost_usd or 0) for run in runs), 6),
                "total_latency_ms": total_latency_ms,
                "avg_latency_ms": avg_latency_ms,
                "latest_run_at": max(run.created_at for run in runs).isoformat(),
            }
        )

    breakdown.sort(
        key=lambda entry: (
            -float(entry["total_cost_usd"]),
            -int(entry["run_count"]),
            str(entry["model_name"]),
            str(entry["worker_name"] or ""),
        )
    )
    return breakdown


def translation_usage_timeline(bundle: ChapterExportBundle) -> list[dict[str, object]]:
    return translation_usage_timeline_from_runs(bundle.translation_runs)


def translation_usage_timeline_from_runs(runs: list[object]) -> list[dict[str, object]]:
    grouped: dict[str, list] = {}
    for run in runs:
        bucket_start = run.created_at.date().isoformat()
        grouped.setdefault(bucket_start, []).append(run)

    timeline: list[dict[str, object]] = []
    for bucket_start, runs in grouped.items():
        latency_values = [run.latency_ms for run in runs if run.latency_ms is not None]
        total_latency_ms = sum(latency_values)
        avg_latency_ms = round(total_latency_ms / len(latency_values), 3) if latency_values else None
        timeline.append(
            {
                "bucket_start": bucket_start,
                "bucket_granularity": "day",
                "run_count": len(runs),
                "succeeded_run_count": sum(1 for run in runs if run.status.value == "succeeded"),
                "total_token_in": sum(run.token_in or 0 for run in runs),
                "total_token_out": sum(run.token_out or 0 for run in runs),
                "total_cost_usd": round(sum(float(run.cost_usd or 0) for run in runs), 6),
                "total_latency_ms": total_latency_ms,
                "avg_latency_ms": avg_latency_ms,
            }
        )

    timeline.sort(key=lambda entry: str(entry["bucket_start"]), reverse=True)
    return timeline


def translation_usage_highlights(bundle: ChapterExportBundle) -> dict[str, object]:
    return translation_usage_highlights_from_runs(bundle.translation_runs)


def translation_usage_highlights_from_runs(runs: list[object]) -> dict[str, object]:
    breakdown = translation_usage_breakdown_from_runs(runs)
    if not breakdown:
        return {
            "top_cost_entry": None,
            "top_latency_entry": None,
            "top_volume_entry": None,
        }

    top_cost_entry = max(
        breakdown,
        key=lambda entry: (
            float(entry["total_cost_usd"]),
            int(entry["run_count"]),
            str(entry["model_name"]),
            str(entry["worker_name"] or ""),
        ),
    )
    top_latency_entry = max(
        breakdown,
        key=lambda entry: (
            float(entry["avg_latency_ms"] or 0.0),
            int(entry["total_latency_ms"]),
            str(entry["model_name"]),
            str(entry["worker_name"] or ""),
        ),
    )
    top_volume_entry = max(
        breakdown,
        key=lambda entry: (
            int(entry["run_count"]),
            int(entry["total_token_out"]),
            str(entry["model_name"]),
            str(entry["worker_name"] or ""),
        ),
    )
    return {
        "top_cost_entry": top_cost_entry,
        "top_latency_entry": top_latency_entry,
        "top_volume_entry": top_volume_entry,
    }
