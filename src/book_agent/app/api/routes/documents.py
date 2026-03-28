from dataclasses import dataclass
import mimetypes
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from book_agent.app.api.deps import get_db_session
from book_agent.core.config import get_settings
from book_agent.domain.document_titles import document_display_title, safe_title_for_filename
from book_agent.domain.enums import DocumentRunStatus, DocumentStatus, ExportStatus, ExportType, MemoryProposalStatus, SourceType
from book_agent.infra.db.legacy_backfill import backfill_legacy_history
from book_agent.schemas.document import DocumentContractResponse
from book_agent.schemas.workflow import (
    BootstrapDocumentRequest,
    ChapterMemoryProposalResponse,
    ChapterMemoryProposalDecisionAuditResponse,
    ChapterMemoryProposalDecisionRequest,
    ChapterMemoryProposalDecisionResponse,
    ChapterMemoryProposalListResponse,
    ChapterMemoryProposalSurfaceResponse,
    ChapterWorklistAssignmentClearRequest,
    ChapterWorklistAssignmentClearResponse,
    ChapterWorklistAssignmentRequest,
    ChapterWorklistAssignmentResponse,
    DocumentChapterWorklistResponse,
    DocumentChapterWorklistDetailResponse,
    DocumentHistoryBackfillResponse,
    DocumentExportDashboardResponse,
    DocumentHistoryPageResponse,
    ExportDetailResponse,
    DocumentSummaryResponse,
    ExportDocumentRequest,
    ExportDocumentResponse,
    ReviewDocumentResponse,
    TranslateDocumentRequest,
    TranslateDocumentResponse,
)
from book_agent.services.export import ExportGateError
from book_agent.services.workflows import (
    DocumentChapterWorklist,
    DocumentChapterWorklistDetail,
    DocumentExportDashboard,
    DocumentHistoryPage,
    ExportDetail,
    ChapterMemoryProposalDecisionResult,
    ChapterMemoryProposalDecisionAuditSummary,
    ChapterMemoryProposalSummary,
    DocumentExportResult,
    DocumentReviewResult,
    DocumentSummary,
    DocumentTranslationResult,
    DocumentWorkflowService,
)
from book_agent.workers.factory import build_translation_worker

router = APIRouter()
_ALLOWED_UPLOAD_SUFFIXES = {".epub", ".pdf"}


@dataclass(frozen=True, slots=True)
class ArchiveInput:
    path: Path
    archive_name: str | None = None


def _upload_root(request: Request) -> Path:
    configured = getattr(request.app.state, "upload_root", get_settings().upload_root)
    return Path(configured).resolve()


def _export_root(request: Request) -> Path:
    configured = getattr(request.app.state, "export_root", get_settings().export_root)
    return Path(configured).resolve()


def _artifact_roots(request: Request) -> tuple[Path, ...]:
    export_root = _export_root(request)
    artifact_root = export_root.parent.resolve()
    if artifact_root == export_root:
        return (export_root,)
    return (export_root, artifact_root)


def _safe_upload_filename(filename: str | None) -> str:
    candidate = Path(filename or "").name.strip()
    if not candidate or candidate in {".", ".."}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Upload a valid EPUB or PDF file.")
    if Path(candidate).suffix.lower() not in _ALLOWED_UPLOAD_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported file type. Upload an .epub or .pdf file.",
        )
    return candidate


def _cleanup_path(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    finally:
        parent = path.parent
        try:
            parent.rmdir()
        except OSError:
            pass


def _artifact_fallback_candidates(path: Path) -> list[Path]:
    if path.exists():
        return [path]
    candidates: list[Path] = []
    if path.suffix.lower() == ".html" and path.stem == "merged-document":
        candidates.extend(
            sorted(
                path.parent.glob("merged-document*.html"),
                key=lambda candidate: (len(candidate.name), candidate.name),
            )
        )
    return [candidate.resolve() for candidate in candidates if candidate.exists()]


def _is_within_any_root(path: Path, roots: tuple[Path, ...]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
        except ValueError:
            continue
        return True
    return False


def _resolve_artifact_path(candidate: str | Path, *, roots: tuple[Path, ...]) -> Path:
    resolved = Path(candidate).resolve()
    fallback_candidates = [resolved, *_artifact_fallback_candidates(resolved)]
    for fallback_path in fallback_candidates:
        if _is_within_any_root(fallback_path, roots) and fallback_path.exists():
            return fallback_path
    allowed_root_label = ", ".join(str(root) for root in roots)
    if any(_is_within_any_root(fallback_path, roots) for fallback_path in fallback_candidates):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Export artifact not found under allowed roots: {allowed_root_label}",
        )
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Export artifact is no longer available.",
    )


def _artifact_media_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _build_export_bundle_filename(
    document_id: str,
    export_type: ExportType,
    *,
    include_related_exports: bool = False,
) -> str:
    if include_related_exports and export_type == ExportType.MERGED_HTML:
        return f"{document_id}-analysis-bundle.zip"
    return f"{document_id}-{export_type.value}.zip"


def _export_sidecar_paths(file_path: Path) -> list[Path]:
    if file_path.suffix.lower() not in {".html", ".md"}:
        return []
    assets_dir = file_path.parent / "assets"
    if not assets_dir.is_dir():
        return []
    return sorted(path for path in assets_dir.rglob("*") if path.is_file())


def _build_export_archive(
    document_id: str,
    export_type: ExportType,
    files: list[ArchiveInput],
    *,
    include_related_exports: bool = False,
) -> Path:
    temp_file = tempfile.NamedTemporaryFile(
        prefix=f"book-agent-{document_id}-{export_type.value}-",
        suffix=".zip",
        delete=False,
    )
    archive_path = Path(temp_file.name)
    temp_file.close()
    folder_name = _build_export_bundle_filename(
        document_id,
        export_type,
        include_related_exports=include_related_exports,
    ).removesuffix(".zip")
    common_root = Path(os.path.commonpath([str(file.path) for file in files]))
    with zipfile.ZipFile(archive_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        seen_names: set[str] = set()
        for index, file in enumerate(files, start=1):
            file_path = file.path
            if file.archive_name:
                archive_name = file.archive_name
            else:
                try:
                    archive_name = file_path.relative_to(common_root).as_posix()
                except ValueError:
                    archive_name = file_path.name
            if archive_name in seen_names:
                archive_name = f"{file_path.stem}-{index}{file_path.suffix}"
            seen_names.add(archive_name)
            archive.write(file_path, arcname=f"{folder_name}/{archive_name}")
    return archive_path


def _preferred_archive_name(original_path: str | Path, resolved_path: Path) -> str | None:
    original_name = Path(original_path).name
    if not original_name or original_name == resolved_path.name:
        return None
    return original_name


def _document_export_download_filename(
    document,
    export_type: ExportType,
    *,
    file_suffix: str,
    include_related_exports: bool = False,
) -> str:
    book_title = safe_title_for_filename(document_display_title(document), wrap_book_quotes=True)
    if include_related_exports and export_type == ExportType.MERGED_HTML:
        return f"{book_title}-整书译制包.zip"
    label_map = {
        ExportType.MERGED_HTML: "中文阅读稿",
        ExportType.MERGED_MARKDOWN: "中文阅读稿-Markdown",
        ExportType.REBUILT_EPUB: "重建EPUB",
        ExportType.REBUILT_PDF: "重建PDF",
        ExportType.BILINGUAL_HTML: "双语章节包",
        ExportType.REVIEW_PACKAGE: "审校包",
    }
    label = label_map.get(export_type, export_type.value)
    return f"{book_title}-{label}{file_suffix}"


def _chapter_export_download_filename(
    document,
    chapter,
    export_type: ExportType,
    *,
    file_suffix: str,
    archive: bool = False,
) -> str:
    book_title = safe_title_for_filename(document_display_title(document), wrap_book_quotes=True)
    chapter_ordinal = getattr(chapter, "ordinal", None)
    chapter_title = safe_title_for_filename(
        getattr(chapter, "title_tgt", None) or getattr(chapter, "title_src", None),
        fallback="未命名章节",
    )
    chapter_prefix = f"第{chapter_ordinal}章" if isinstance(chapter_ordinal, int) and chapter_ordinal > 0 else "章节导出"
    label_map = {
        ExportType.BILINGUAL_HTML: "双语章节包",
        ExportType.REVIEW_PACKAGE: "审校包",
    }
    label = label_map.get(export_type, export_type.value)
    suffix = ".zip" if archive else file_suffix
    return f"{book_title}-{chapter_prefix}-{chapter_title}-{label}{suffix}"


def _append_archive_input(
    archive_inputs: list[ArchiveInput],
    seen_paths: set[str],
    file_path: Path,
    *,
    preferred_archive_name: str | None = None,
) -> None:
    for index, candidate in enumerate([file_path, *_export_sidecar_paths(file_path)]):
        resolved = str(candidate.resolve())
        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)
        archive_inputs.append(
            ArchiveInput(
                path=candidate,
                archive_name=(preferred_archive_name if index == 0 else None),
            )
        )


def _serialize_translation_usage_summary(summary) -> dict | None:
    if summary is None:
        return None
    return {
        "run_count": summary.run_count,
        "succeeded_run_count": summary.succeeded_run_count,
        "total_token_in": summary.total_token_in,
        "total_token_out": summary.total_token_out,
        "total_cost_usd": summary.total_cost_usd,
        "total_latency_ms": summary.total_latency_ms,
        "avg_latency_ms": summary.avg_latency_ms,
        "latest_run_at": summary.latest_run_at,
    }


def _serialize_translation_usage_breakdown(entries) -> list[dict]:
    return [
        {
            "model_name": entry.model_name,
            "worker_name": entry.worker_name,
            "provider": entry.provider,
            "run_count": entry.run_count,
            "succeeded_run_count": entry.succeeded_run_count,
            "total_token_in": entry.total_token_in,
            "total_token_out": entry.total_token_out,
            "total_cost_usd": entry.total_cost_usd,
            "total_latency_ms": entry.total_latency_ms,
            "avg_latency_ms": entry.avg_latency_ms,
            "latest_run_at": entry.latest_run_at,
        }
        for entry in entries
    ]


def _serialize_translation_usage_breakdown_entry(entry) -> dict | None:
    if entry is None:
        return None
    return {
        "model_name": entry.model_name,
        "worker_name": entry.worker_name,
        "provider": entry.provider,
        "run_count": entry.run_count,
        "succeeded_run_count": entry.succeeded_run_count,
        "total_token_in": entry.total_token_in,
        "total_token_out": entry.total_token_out,
        "total_cost_usd": entry.total_cost_usd,
        "total_latency_ms": entry.total_latency_ms,
        "avg_latency_ms": entry.avg_latency_ms,
        "latest_run_at": entry.latest_run_at,
    }


def _serialize_translation_usage_timeline(entries) -> list[dict]:
    return [
        {
            "bucket_start": entry.bucket_start,
            "bucket_granularity": entry.bucket_granularity,
            "run_count": entry.run_count,
            "succeeded_run_count": entry.succeeded_run_count,
            "total_token_in": entry.total_token_in,
            "total_token_out": entry.total_token_out,
            "total_cost_usd": entry.total_cost_usd,
            "total_latency_ms": entry.total_latency_ms,
            "avg_latency_ms": entry.avg_latency_ms,
        }
        for entry in entries
    ]


def _serialize_issue_hotspots(entries) -> list[dict]:
    return [
        {
            "issue_type": entry.issue_type,
            "root_cause_layer": entry.root_cause_layer,
            "issue_count": entry.issue_count,
            "open_issue_count": entry.open_issue_count,
            "triaged_issue_count": entry.triaged_issue_count,
            "resolved_issue_count": entry.resolved_issue_count,
            "wontfix_issue_count": entry.wontfix_issue_count,
            "blocking_issue_count": entry.blocking_issue_count,
            "chapter_count": entry.chapter_count,
            "latest_seen_at": entry.latest_seen_at,
        }
        for entry in entries
    ]


def _serialize_issue_chapter_pressure(entries) -> list[dict]:
    return [
        {
            "chapter_id": entry.chapter_id,
            "ordinal": entry.ordinal,
            "title_src": entry.title_src,
            "chapter_status": entry.chapter_status,
            "issue_count": entry.issue_count,
            "open_issue_count": entry.open_issue_count,
            "triaged_issue_count": entry.triaged_issue_count,
            "resolved_issue_count": entry.resolved_issue_count,
            "blocking_issue_count": entry.blocking_issue_count,
            "latest_issue_at": entry.latest_issue_at,
        }
        for entry in entries
    ]


def _serialize_issue_chapter_pressure_entry(entry) -> dict | None:
    if entry is None:
        return None
    return {
        "chapter_id": entry.chapter_id,
        "ordinal": entry.ordinal,
        "title_src": entry.title_src,
        "chapter_status": entry.chapter_status,
        "issue_count": entry.issue_count,
        "open_issue_count": entry.open_issue_count,
        "triaged_issue_count": entry.triaged_issue_count,
        "resolved_issue_count": entry.resolved_issue_count,
        "blocking_issue_count": entry.blocking_issue_count,
        "latest_issue_at": entry.latest_issue_at,
    }


def _serialize_issue_chapter_breakdown(entries) -> list[dict]:
    return [
        {
            "chapter_id": entry.chapter_id,
            "ordinal": entry.ordinal,
            "title_src": entry.title_src,
            "chapter_status": entry.chapter_status,
            "issue_type": entry.issue_type,
            "root_cause_layer": entry.root_cause_layer,
            "issue_count": entry.issue_count,
            "open_issue_count": entry.open_issue_count,
            "triaged_issue_count": entry.triaged_issue_count,
            "resolved_issue_count": entry.resolved_issue_count,
            "blocking_issue_count": entry.blocking_issue_count,
            "active_blocking_issue_count": entry.active_blocking_issue_count,
            "latest_seen_at": entry.latest_seen_at,
        }
        for entry in entries
    ]


def _serialize_issue_chapter_heatmap(entries) -> list[dict]:
    return [
        {
            "chapter_id": entry.chapter_id,
            "ordinal": entry.ordinal,
            "title_src": entry.title_src,
            "chapter_status": entry.chapter_status,
            "issue_count": entry.issue_count,
            "open_issue_count": entry.open_issue_count,
            "triaged_issue_count": entry.triaged_issue_count,
            "resolved_issue_count": entry.resolved_issue_count,
            "blocking_issue_count": entry.blocking_issue_count,
            "active_blocking_issue_count": entry.active_blocking_issue_count,
            "issue_family_count": entry.issue_family_count,
            "dominant_issue_type": entry.dominant_issue_type,
            "dominant_root_cause_layer": entry.dominant_root_cause_layer,
            "dominant_issue_count": entry.dominant_issue_count,
            "latest_issue_at": entry.latest_issue_at,
            "heat_score": entry.heat_score,
            "heat_level": entry.heat_level,
        }
        for entry in entries
    ]


def _serialize_issue_chapter_queue(entries) -> list[dict]:
    return [
        {
            "chapter_id": entry.chapter_id,
            "ordinal": entry.ordinal,
            "title_src": entry.title_src,
            "chapter_status": entry.chapter_status,
            "issue_count": entry.issue_count,
            "open_issue_count": entry.open_issue_count,
            "triaged_issue_count": entry.triaged_issue_count,
            "blocking_issue_count": entry.blocking_issue_count,
            "active_blocking_issue_count": entry.active_blocking_issue_count,
            "issue_family_count": entry.issue_family_count,
            "dominant_issue_type": entry.dominant_issue_type,
            "dominant_root_cause_layer": entry.dominant_root_cause_layer,
            "dominant_issue_count": entry.dominant_issue_count,
            "latest_issue_at": entry.latest_issue_at,
            "heat_score": entry.heat_score,
            "heat_level": entry.heat_level,
            "queue_rank": entry.queue_rank,
            "queue_priority": entry.queue_priority,
            "queue_driver": entry.queue_driver,
            "needs_immediate_attention": entry.needs_immediate_attention,
            "oldest_active_issue_at": entry.oldest_active_issue_at,
            "age_hours": entry.age_hours,
            "age_bucket": entry.age_bucket,
            "sla_target_hours": entry.sla_target_hours,
            "sla_status": entry.sla_status,
            "owner_ready": entry.owner_ready,
            "owner_ready_reason": entry.owner_ready_reason,
            "is_assigned": entry.is_assigned,
            "assigned_owner_name": entry.assigned_owner_name,
            "assigned_at": entry.assigned_at,
            "latest_activity_bucket_start": entry.latest_activity_bucket_start,
            "latest_created_issue_count": entry.latest_created_issue_count,
            "latest_resolved_issue_count": entry.latest_resolved_issue_count,
            "latest_net_issue_delta": entry.latest_net_issue_delta,
            "regression_hint": entry.regression_hint,
            "flapping_hint": entry.flapping_hint,
            "memory_proposals": {
                "proposal_count": entry.memory_proposals.proposal_count,
                "pending_proposal_count": entry.memory_proposals.pending_proposal_count,
                "counts_by_status": entry.memory_proposals.counts_by_status,
                "latest_proposal_updated_at": entry.memory_proposals.latest_proposal_updated_at,
                "active_snapshot_version": entry.memory_proposals.active_snapshot_version,
            },
        }
        for entry in entries
    ]


def _serialize_issue_chapter_queue_entry(entry) -> dict | None:
    if entry is None:
        return None
    return _serialize_issue_chapter_queue([entry])[0]


def _serialize_owner_workload_summary(entries) -> list[dict]:
    return [
        {
            "owner_name": entry.owner_name,
            "assigned_chapter_count": entry.assigned_chapter_count,
            "immediate_count": entry.immediate_count,
            "high_count": entry.high_count,
            "medium_count": entry.medium_count,
            "breached_count": entry.breached_count,
            "due_soon_count": entry.due_soon_count,
            "on_track_count": entry.on_track_count,
            "owner_ready_count": entry.owner_ready_count,
            "total_open_issue_count": entry.total_open_issue_count,
            "total_active_blocking_issue_count": entry.total_active_blocking_issue_count,
            "oldest_active_issue_at": entry.oldest_active_issue_at,
            "latest_issue_at": entry.latest_issue_at,
        }
        for entry in entries
    ]


def _serialize_owner_workload_entry(entry) -> dict | None:
    if entry is None:
        return None
    return _serialize_owner_workload_summary([entry])[0]


def _serialize_issue_activity_timeline(entries) -> list[dict]:
    return [
        {
            "bucket_start": entry.bucket_start,
            "bucket_granularity": entry.bucket_granularity,
            "created_issue_count": entry.created_issue_count,
            "resolved_issue_count": entry.resolved_issue_count,
            "wontfix_issue_count": entry.wontfix_issue_count,
            "blocking_created_issue_count": entry.blocking_created_issue_count,
            "net_issue_delta": entry.net_issue_delta,
            "estimated_open_issue_count": entry.estimated_open_issue_count,
        }
        for entry in entries
    ]


def _serialize_issue_activity_breakdown(entries) -> list[dict]:
    return [
        {
            "issue_type": entry.issue_type,
            "root_cause_layer": entry.root_cause_layer,
            "issue_count": entry.issue_count,
            "open_issue_count": entry.open_issue_count,
            "blocking_issue_count": entry.blocking_issue_count,
            "latest_seen_at": entry.latest_seen_at,
            "timeline": _serialize_issue_activity_timeline(entry.timeline),
        }
        for entry in entries
    ]


def _serialize_issue_activity_breakdown_entry(entry) -> dict | None:
    if entry is None:
        return None
    return {
        "issue_type": entry.issue_type,
        "root_cause_layer": entry.root_cause_layer,
        "issue_count": entry.issue_count,
        "open_issue_count": entry.open_issue_count,
        "blocking_issue_count": entry.blocking_issue_count,
        "latest_seen_at": entry.latest_seen_at,
        "timeline": _serialize_issue_activity_timeline(entry.timeline),
    }


def _workflow_service(request: Request, session: Session) -> DocumentWorkflowService:
    export_root = getattr(request.app.state, "export_root", "artifacts/exports")
    translation_worker = getattr(request.app.state, "translation_worker", None)
    if translation_worker is None:
        settings = get_settings()
        translation_worker = build_translation_worker(settings)
    return DocumentWorkflowService(
        session,
        export_root=export_root,
        translation_worker=translation_worker,
    )


def _to_document_summary_response(summary: DocumentSummary) -> DocumentSummaryResponse:
    return DocumentSummaryResponse(
        document_id=summary.document_id,
        source_type=summary.source_type,
        status=summary.status,
        title=summary.title,
        title_src=summary.title_src,
        title_tgt=summary.title_tgt,
        author=summary.author,
        pdf_profile=summary.pdf_profile,
        pdf_page_evidence=summary.pdf_page_evidence,
        pdf_image_summary=summary.pdf_image_summary,
        chapter_count=summary.chapter_count,
        block_count=summary.block_count,
        sentence_count=summary.sentence_count,
        packet_count=summary.packet_count,
        open_issue_count=summary.open_issue_count,
        merged_export_ready=summary.merged_export_ready,
        latest_merged_export_at=summary.latest_merged_export_at,
        chapter_bilingual_export_count=summary.chapter_bilingual_export_count,
        latest_run_id=summary.latest_run_id,
        latest_run_status=summary.latest_run_status,
        latest_run_current_stage=summary.latest_run_current_stage,
        latest_run_updated_at=summary.latest_run_updated_at,
        runtime_v2_context=summary.runtime_v2_context,
        chapters=[
            {
                "chapter_id": chapter.chapter_id,
                "ordinal": chapter.ordinal,
                "title_src": chapter.title_src,
                "status": chapter.status,
                "risk_level": chapter.risk_level,
                "parse_confidence": chapter.parse_confidence,
                "structure_flags": chapter.structure_flags,
                "sentence_count": chapter.sentence_count,
                "packet_count": chapter.packet_count,
                "open_issue_count": chapter.open_issue_count,
                "bilingual_export_ready": chapter.bilingual_export_ready,
                "latest_bilingual_export_at": chapter.latest_bilingual_export_at,
                "pdf_image_summary": chapter.pdf_image_summary,
                "quality_summary": (
                    {
                        "issue_count": chapter.quality_summary.issue_count,
                        "action_count": chapter.quality_summary.action_count,
                        "resolved_issue_count": chapter.quality_summary.resolved_issue_count,
                        "coverage_ok": chapter.quality_summary.coverage_ok,
                        "alignment_ok": chapter.quality_summary.alignment_ok,
                        "term_ok": chapter.quality_summary.term_ok,
                        "format_ok": chapter.quality_summary.format_ok,
                        "blocking_issue_count": chapter.quality_summary.blocking_issue_count,
                        "low_confidence_count": chapter.quality_summary.low_confidence_count,
                        "format_pollution_count": chapter.quality_summary.format_pollution_count,
                    }
                    if chapter.quality_summary is not None
                    else None
                ),
            }
            for chapter in summary.chapters
        ],
    )


def _to_document_history_page_response(page: DocumentHistoryPage) -> DocumentHistoryPageResponse:
    return DocumentHistoryPageResponse(
        total_count=page.total_count,
        record_count=page.record_count,
        offset=page.offset,
        limit=page.limit,
        has_more=page.has_more,
        entries=[
            {
                "document_id": entry.document_id,
                "source_type": entry.source_type,
                "status": entry.status,
                "title": entry.title,
                "title_src": entry.title_src,
                "title_tgt": entry.title_tgt,
                "author": entry.author,
                "source_path": entry.source_path,
                "created_at": entry.created_at,
                "updated_at": entry.updated_at,
                "chapter_count": entry.chapter_count,
                "sentence_count": entry.sentence_count,
                "packet_count": entry.packet_count,
                "merged_export_ready": entry.merged_export_ready,
                "latest_merged_export_at": entry.latest_merged_export_at,
                "chapter_bilingual_export_count": entry.chapter_bilingual_export_count,
                "latest_run_id": entry.latest_run_id,
                "latest_run_status": entry.latest_run_status,
                "latest_run_current_stage": entry.latest_run_current_stage,
                "latest_run_completed_work_item_count": entry.latest_run_completed_work_item_count,
                "latest_run_total_work_item_count": entry.latest_run_total_work_item_count,
            }
            for entry in page.entries
        ],
    )


def _to_translate_response(result: DocumentTranslationResult) -> TranslateDocumentResponse:
    return TranslateDocumentResponse(
        document_id=result.document_id,
        translated_packet_count=result.translated_packet_count,
        skipped_packet_ids=result.skipped_packet_ids,
        translation_run_ids=result.translation_run_ids,
        review_required_sentence_ids=result.review_required_sentence_ids,
        memory_commit_mode=result.memory_commit_mode,
        recorded_memory_proposal_count=result.recorded_memory_proposal_count,
    )


def _to_chapter_memory_proposal_response(
    proposal: ChapterMemoryProposalSummary,
) -> ChapterMemoryProposalResponse:
    return ChapterMemoryProposalResponse(
        proposal_id=proposal.proposal_id,
        packet_id=proposal.packet_id,
        translation_run_id=proposal.translation_run_id,
        status=proposal.status,
        base_snapshot_version=proposal.base_snapshot_version,
        committed_snapshot_id=proposal.committed_snapshot_id,
        created_at=proposal.created_at,
        updated_at=proposal.updated_at,
        last_decision=(
            _to_chapter_memory_proposal_decision_audit_response(proposal.last_decision)
            if proposal.last_decision is not None
            else None
        ),
    )


def _to_chapter_memory_proposal_decision_audit_response(
    audit: ChapterMemoryProposalDecisionAuditSummary,
) -> ChapterMemoryProposalDecisionAuditResponse:
    return ChapterMemoryProposalDecisionAuditResponse(
        proposal_id=audit.proposal_id,
        decision=audit.decision,  # type: ignore[arg-type]
        actor_type=audit.actor_type,
        actor_id=audit.actor_id,
        note=audit.note,
        created_at=audit.created_at,
    )


def _to_chapter_memory_proposal_list_response(
    *,
    document_id: str,
    chapter_id: str,
    status_filter: str | None,
    proposals: list[ChapterMemoryProposalSummary],
) -> ChapterMemoryProposalListResponse:
    return ChapterMemoryProposalListResponse(
        document_id=document_id,
        chapter_id=chapter_id,
        status_filter=status_filter,  # type: ignore[arg-type]
        proposal_count=len(proposals),
        proposals=[_to_chapter_memory_proposal_response(proposal) for proposal in proposals],
    )


def _to_chapter_memory_proposal_decision_response(
    result: ChapterMemoryProposalDecisionResult,
) -> ChapterMemoryProposalDecisionResponse:
    return ChapterMemoryProposalDecisionResponse(
        document_id=result.document_id,
        chapter_id=result.chapter_id,
        decision=result.decision,  # type: ignore[arg-type]
        proposal=_to_chapter_memory_proposal_response(result.proposal),
        committed_snapshot_id=result.committed_snapshot_id,
        committed_snapshot_version=result.committed_snapshot_version,
    )


def _proposal_http_exception(exc: ValueError) -> HTTPException:
    message = str(exc)
    lowered = message.casefold()
    if "not found" in lowered or "does not belong" in lowered:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)


def _to_review_response(result: DocumentReviewResult) -> ReviewDocumentResponse:
    return ReviewDocumentResponse(
        document_id=result.document_id,
        total_issue_count=result.total_issue_count,
        total_action_count=result.total_action_count,
        chapter_results=[
            {
                "chapter_id": chapter.chapter_id,
                "status": chapter.status,
                "issue_count": chapter.issue_count,
                "action_count": chapter.action_count,
                "blocking_issue_count": chapter.blocking_issue_count,
                "coverage_ok": chapter.coverage_ok,
                "alignment_ok": chapter.alignment_ok,
                "term_ok": chapter.term_ok,
                "format_ok": chapter.format_ok,
                "low_confidence_count": chapter.low_confidence_count,
                "format_pollution_count": chapter.format_pollution_count,
                "resolved_issue_count": chapter.resolved_issue_count,
                "naturalness_summary": (
                    {
                        "advisory_only": chapter.naturalness_summary.advisory_only,
                        "style_drift_issue_count": chapter.naturalness_summary.style_drift_issue_count,
                        "affected_packet_count": chapter.naturalness_summary.affected_packet_count,
                        "dominant_style_rules": list(chapter.naturalness_summary.dominant_style_rules),
                        "preferred_hints": list(chapter.naturalness_summary.preferred_hints),
                    }
                    if chapter.naturalness_summary is not None
                    else None
                ),
            }
            for chapter in result.chapter_results
        ],
    )


def _to_export_response(result: DocumentExportResult) -> ExportDocumentResponse:
    return ExportDocumentResponse(
        document_id=result.document_id,
        export_type=result.export_type,
        document_status=result.document_status,
        file_path=result.file_path,
        manifest_path=result.manifest_path,
        chapter_results=[
            {
                "chapter_id": chapter.chapter_id,
                "export_id": chapter.export_id,
                "export_type": chapter.export_type,
                "status": chapter.status,
                "file_path": chapter.file_path,
                "manifest_path": chapter.manifest_path,
            }
            for chapter in result.chapter_results
        ],
        auto_followup_requested=result.auto_followup_requested,
        auto_followup_applied=result.auto_followup_applied,
        auto_followup_attempt_count=result.auto_followup_attempt_count,
        auto_followup_attempt_limit=result.auto_followup_attempt_limit,
        auto_followup_executions=[
            {
                "action_id": execution.action_id,
                "issue_id": execution.issue_id,
                "action_type": execution.action_type,
                "rerun_scope_type": execution.rerun_scope_type,
                "rerun_scope_ids": execution.rerun_scope_ids,
                "followup_executed": execution.followup_executed,
                "rerun_packet_ids": execution.rerun_packet_ids,
                "rerun_translation_run_ids": execution.rerun_translation_run_ids,
                "issue_resolved": execution.issue_resolved,
            }
            for execution in (result.auto_followup_executions or [])
        ],
        runtime_v2_context=result.runtime_v2_context,
    )


def _to_export_dashboard_response(result: DocumentExportDashboard) -> DocumentExportDashboardResponse:
    return DocumentExportDashboardResponse(
        document_id=result.document_id,
        export_count=result.export_count,
        successful_export_count=result.successful_export_count,
        filtered_export_count=result.filtered_export_count,
        record_count=result.record_count,
        offset=result.offset,
        limit=result.limit,
        has_more=result.has_more,
        applied_export_type_filter=result.applied_export_type_filter,
        applied_status_filter=result.applied_status_filter,
        latest_export_at=result.latest_export_at,
        export_counts_by_type=result.export_counts_by_type,
        latest_export_ids_by_type=result.latest_export_ids_by_type,
        total_auto_followup_executed_count=result.total_auto_followup_executed_count,
        translation_usage_summary=_serialize_translation_usage_summary(result.translation_usage_summary),
        translation_usage_breakdown=_serialize_translation_usage_breakdown(result.translation_usage_breakdown),
        translation_usage_timeline=_serialize_translation_usage_timeline(result.translation_usage_timeline),
        translation_usage_highlights={
            "top_cost_entry": _serialize_translation_usage_breakdown_entry(
                result.translation_usage_highlights.top_cost_entry
            ),
            "top_latency_entry": _serialize_translation_usage_breakdown_entry(
                result.translation_usage_highlights.top_latency_entry
            ),
            "top_volume_entry": _serialize_translation_usage_breakdown_entry(
                result.translation_usage_highlights.top_volume_entry
            ),
        },
        issue_hotspots=_serialize_issue_hotspots(result.issue_hotspots),
        issue_chapter_pressure=_serialize_issue_chapter_pressure(result.issue_chapter_pressure),
        issue_chapter_highlights={
            "top_open_chapter": _serialize_issue_chapter_pressure_entry(
                result.issue_chapter_highlights.top_open_chapter
            ),
            "top_blocking_chapter": _serialize_issue_chapter_pressure_entry(
                result.issue_chapter_highlights.top_blocking_chapter
            ),
            "top_resolved_chapter": _serialize_issue_chapter_pressure_entry(
                result.issue_chapter_highlights.top_resolved_chapter
            ),
        },
        issue_chapter_breakdown=_serialize_issue_chapter_breakdown(result.issue_chapter_breakdown),
        issue_chapter_heatmap=_serialize_issue_chapter_heatmap(result.issue_chapter_heatmap),
        issue_chapter_queue=_serialize_issue_chapter_queue(result.issue_chapter_queue),
        issue_activity_timeline=_serialize_issue_activity_timeline(result.issue_activity_timeline),
        issue_activity_breakdown=_serialize_issue_activity_breakdown(result.issue_activity_breakdown),
        issue_activity_highlights={
            "top_regressing_entry": _serialize_issue_activity_breakdown_entry(
                result.issue_activity_highlights.top_regressing_entry
            ),
            "top_resolving_entry": _serialize_issue_activity_breakdown_entry(
                result.issue_activity_highlights.top_resolving_entry
            ),
            "top_blocking_entry": _serialize_issue_activity_breakdown_entry(
                result.issue_activity_highlights.top_blocking_entry
            ),
        },
        records=[
            {
                "export_id": record.export_id,
                "export_type": record.export_type,
                "status": record.status,
                "file_path": record.file_path,
                "manifest_path": record.manifest_path,
                "chapter_id": record.chapter_id,
                "chapter_summary_version": record.chapter_summary_version,
                "created_at": record.created_at,
                "updated_at": record.updated_at,
                "translation_usage_summary": _serialize_translation_usage_summary(
                    record.translation_usage_summary
                ),
                "translation_usage_breakdown": _serialize_translation_usage_breakdown(
                    record.translation_usage_breakdown or []
                ),
                "translation_usage_timeline": _serialize_translation_usage_timeline(
                    record.translation_usage_timeline or []
                ),
                "translation_usage_highlights": (
                    {
                        "top_cost_entry": _serialize_translation_usage_breakdown_entry(
                            record.translation_usage_highlights.top_cost_entry
                        ),
                        "top_latency_entry": _serialize_translation_usage_breakdown_entry(
                            record.translation_usage_highlights.top_latency_entry
                        ),
                        "top_volume_entry": _serialize_translation_usage_breakdown_entry(
                            record.translation_usage_highlights.top_volume_entry
                        ),
                    }
                    if record.translation_usage_highlights is not None
                    else None
                ),
                "export_auto_followup_summary": (
                    {
                        "event_count": record.export_auto_followup_summary.event_count,
                        "executed_event_count": record.export_auto_followup_summary.executed_event_count,
                        "stop_event_count": record.export_auto_followup_summary.stop_event_count,
                        "latest_event_at": record.export_auto_followup_summary.latest_event_at,
                        "last_stop_reason": record.export_auto_followup_summary.last_stop_reason,
                    }
                    if record.export_auto_followup_summary is not None
                    else None
                ),
                "export_time_misalignment_counts": (
                    {
                        "missing_target_sentence_count": (
                            record.export_time_misalignment_counts.missing_target_sentence_count
                        ),
                        "inactive_only_sentence_count": (
                            record.export_time_misalignment_counts.inactive_only_sentence_count
                        ),
                        "orphan_target_segment_count": (
                            record.export_time_misalignment_counts.orphan_target_segment_count
                        ),
                        "inactive_target_segment_with_edges_count": (
                            record.export_time_misalignment_counts.inactive_target_segment_with_edges_count
                        ),
                    }
                    if record.export_time_misalignment_counts is not None
                    else None
                ),
            }
            for record in result.records
        ],
    )


def _to_chapter_worklist_response(result: DocumentChapterWorklist) -> DocumentChapterWorklistResponse:
    return DocumentChapterWorklistResponse(
        document_id=result.document_id,
        worklist_count=result.worklist_count,
        filtered_worklist_count=result.filtered_worklist_count,
        entry_count=result.entry_count,
        offset=result.offset,
        limit=result.limit,
        has_more=result.has_more,
        applied_queue_priority_filter=result.applied_queue_priority_filter,
        applied_sla_status_filter=result.applied_sla_status_filter,
        applied_owner_ready_filter=result.applied_owner_ready_filter,
        applied_needs_immediate_attention_filter=result.applied_needs_immediate_attention_filter,
        applied_assigned_filter=result.applied_assigned_filter,
        applied_assigned_owner_filter=result.applied_assigned_owner_filter,
        queue_priority_counts=result.queue_priority_counts,
        sla_status_counts=result.sla_status_counts,
        immediate_attention_count=result.immediate_attention_count,
        owner_ready_count=result.owner_ready_count,
        assigned_count=result.assigned_count,
        owner_workload_summary=_serialize_owner_workload_summary(result.owner_workload_summary),
        owner_workload_highlights={
            "top_loaded_owner": _serialize_owner_workload_entry(
                result.owner_workload_highlights.get("top_loaded_owner")
            ),
            "top_breached_owner": _serialize_owner_workload_entry(
                result.owner_workload_highlights.get("top_breached_owner")
            ),
            "top_blocking_owner": _serialize_owner_workload_entry(
                result.owner_workload_highlights.get("top_blocking_owner")
            ),
            "top_immediate_owner": _serialize_owner_workload_entry(
                result.owner_workload_highlights.get("top_immediate_owner")
            ),
        },
        highlights={
            "top_breached_entry": _serialize_issue_chapter_queue_entry(
                result.highlights.get("top_breached_entry")
            ),
            "top_due_soon_entry": _serialize_issue_chapter_queue_entry(
                result.highlights.get("top_due_soon_entry")
            ),
            "top_oldest_entry": _serialize_issue_chapter_queue_entry(
                result.highlights.get("top_oldest_entry")
            ),
            "top_immediate_entry": _serialize_issue_chapter_queue_entry(
                result.highlights.get("top_immediate_entry")
            ),
        },
        entries=_serialize_issue_chapter_queue(result.entries),
    )


def _to_chapter_worklist_detail_response(
    result: DocumentChapterWorklistDetail,
) -> DocumentChapterWorklistDetailResponse:
    return DocumentChapterWorklistDetailResponse(
        document_id=result.document_id,
        chapter_id=result.chapter_id,
        ordinal=result.ordinal,
        title_src=result.title_src,
        chapter_status=result.chapter_status,
        packet_count=result.packet_count,
        translated_packet_count=result.translated_packet_count,
        current_issue_count=result.current_issue_count,
        current_open_issue_count=result.current_open_issue_count,
        current_triaged_issue_count=result.current_triaged_issue_count,
        current_active_blocking_issue_count=result.current_active_blocking_issue_count,
        assignment=(
            {
                "assignment_id": result.assignment.assignment_id,
                "document_id": result.assignment.document_id,
                "chapter_id": result.assignment.chapter_id,
                "owner_name": result.assignment.owner_name,
                "assigned_by": result.assignment.assigned_by,
                "note": result.assignment.note,
                "assigned_at": result.assignment.assigned_at,
                "created_at": result.assignment.created_at,
                "updated_at": result.assignment.updated_at,
            }
            if result.assignment is not None
            else None
        ),
        queue_entry=_serialize_issue_chapter_queue_entry(result.queue_entry),
        quality_summary=(
            {
                "issue_count": result.quality_summary.issue_count,
                "action_count": result.quality_summary.action_count,
                "resolved_issue_count": result.quality_summary.resolved_issue_count,
                "coverage_ok": result.quality_summary.coverage_ok,
                "alignment_ok": result.quality_summary.alignment_ok,
                "term_ok": result.quality_summary.term_ok,
                "format_ok": result.quality_summary.format_ok,
                "blocking_issue_count": result.quality_summary.blocking_issue_count,
                "low_confidence_count": result.quality_summary.low_confidence_count,
                "format_pollution_count": result.quality_summary.format_pollution_count,
            }
            if result.quality_summary is not None
            else None
        ),
        issue_family_breakdown=_serialize_issue_chapter_breakdown(result.issue_family_breakdown),
        recent_issues=[
            {
                "issue_id": issue.issue_id,
                "issue_type": issue.issue_type,
                "root_cause_layer": issue.root_cause_layer,
                "severity": issue.severity,
                "status": issue.status,
                "blocking": issue.blocking,
                "detector": issue.detector,
                "suggested_action": issue.suggested_action,
                "created_at": issue.created_at,
                "updated_at": issue.updated_at,
            }
            for issue in result.recent_issues
        ],
        recent_actions=[
            {
                "action_id": action.action_id,
                "issue_id": action.issue_id,
                "issue_type": action.issue_type,
                "action_type": action.action_type,
                "scope_type": action.scope_type,
                "scope_id": action.scope_id,
                "status": action.status,
                "created_by": action.created_by,
                "created_at": action.created_at,
                "updated_at": action.updated_at,
            }
            for action in result.recent_actions
        ],
        assignment_history=[
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "owner_name": event.owner_name,
                "performed_by": event.performed_by,
                "note": event.note,
                "created_at": event.created_at,
            }
            for event in result.assignment_history
        ],
        memory_proposals=ChapterMemoryProposalSurfaceResponse(
            proposal_count=result.memory_proposals.proposal_count,
            pending_proposal_count=result.memory_proposals.pending_proposal_count,
            counts_by_status=result.memory_proposals.counts_by_status,
            latest_proposal_updated_at=result.memory_proposals.latest_proposal_updated_at,
            active_snapshot_version=result.memory_proposals.active_snapshot_version,
            pending_proposals=[
                _to_chapter_memory_proposal_response(proposal)
                for proposal in result.memory_proposals.pending_proposals
            ],
            recent_decisions=[
                _to_chapter_memory_proposal_decision_audit_response(audit)
                for audit in result.memory_proposals.recent_decisions
            ],
        ),
    )


def _to_export_detail_response(result: ExportDetail) -> ExportDetailResponse:
    return ExportDetailResponse(
        document_id=result.document_id,
        export_id=result.export_id,
        export_type=result.export_type,
        status=result.status,
        file_path=result.file_path,
        manifest_path=result.manifest_path,
        chapter_id=result.chapter_id,
        sentence_count=result.sentence_count,
        target_segment_count=result.target_segment_count,
        created_at=result.created_at,
        updated_at=result.updated_at,
        translation_usage_summary=_serialize_translation_usage_summary(result.translation_usage_summary),
        translation_usage_breakdown=_serialize_translation_usage_breakdown(
            result.translation_usage_breakdown or []
        ),
        translation_usage_timeline=_serialize_translation_usage_timeline(
            result.translation_usage_timeline or []
        ),
        translation_usage_highlights=(
            {
                "top_cost_entry": _serialize_translation_usage_breakdown_entry(
                    result.translation_usage_highlights.top_cost_entry
                ),
                "top_latency_entry": _serialize_translation_usage_breakdown_entry(
                    result.translation_usage_highlights.top_latency_entry
                ),
                "top_volume_entry": _serialize_translation_usage_breakdown_entry(
                    result.translation_usage_highlights.top_volume_entry
                ),
            }
            if result.translation_usage_highlights is not None
            else None
        ),
        issue_status_summary=(
            {
                "issue_count": result.issue_status_summary.issue_count,
                "open_issue_count": result.issue_status_summary.open_issue_count,
                "resolved_issue_count": result.issue_status_summary.resolved_issue_count,
                "blocking_issue_count": result.issue_status_summary.blocking_issue_count,
            }
            if result.issue_status_summary is not None
            else None
        ),
        export_auto_followup_summary=(
            {
                "event_count": result.export_auto_followup_summary.event_count,
                "executed_event_count": result.export_auto_followup_summary.executed_event_count,
                "stop_event_count": result.export_auto_followup_summary.stop_event_count,
                "latest_event_at": result.export_auto_followup_summary.latest_event_at,
                "last_stop_reason": result.export_auto_followup_summary.last_stop_reason,
            }
            if result.export_auto_followup_summary is not None
            else None
        ),
        export_time_misalignment_counts=(
            {
                "missing_target_sentence_count": result.export_time_misalignment_counts.missing_target_sentence_count,
                "inactive_only_sentence_count": result.export_time_misalignment_counts.inactive_only_sentence_count,
                "orphan_target_segment_count": result.export_time_misalignment_counts.orphan_target_segment_count,
                "inactive_target_segment_with_edges_count": (
                    result.export_time_misalignment_counts.inactive_target_segment_with_edges_count
                ),
            }
            if result.export_time_misalignment_counts is not None
            else None
        ),
        version_evidence_summary={
            "document_parser_version": result.version_evidence_summary.document_parser_version,
            "document_segmentation_version": result.version_evidence_summary.document_segmentation_version,
            "book_profile_version": result.version_evidence_summary.book_profile_version,
            "chapter_summary_version": result.version_evidence_summary.chapter_summary_version,
            "active_snapshot_versions": result.version_evidence_summary.active_snapshot_versions,
        },
        runtime_v2_context=result.runtime_v2_context,
    )


def _to_assignment_response(result) -> ChapterWorklistAssignmentResponse:
    return ChapterWorklistAssignmentResponse(
        assignment_id=result.assignment_id,
        document_id=result.document_id,
        chapter_id=result.chapter_id,
        owner_name=result.owner_name,
        assigned_by=result.assigned_by,
        note=result.note,
        assigned_at=result.assigned_at,
        created_at=result.created_at,
        updated_at=result.updated_at,
    )


@router.get("/contract", response_model=DocumentContractResponse)
def document_contract() -> DocumentContractResponse:
    return DocumentContractResponse(
        supported_source_types=["epub", "pdf_text"],
        current_phase="p1_text_pdf_bootstrap",
        notes=[
            "P1-A supports EPUB plus low-risk text PDFs with geometry-aware provenance.",
            "OCR-required PDFs and unsupported high-risk layouts are rejected before translation; short academic papers may enter a medium-risk recovery lane.",
            "Sentence coverage, packet-based context, provenance, and rerunability remain hard requirements.",
        ],
    )


@router.post("/bootstrap", response_model=DocumentSummaryResponse, status_code=status.HTTP_201_CREATED)
def bootstrap_document(
    payload: BootstrapDocumentRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> DocumentSummaryResponse:
    source_path = Path(payload.source_path)
    if not source_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source file not found: {source_path}",
        )
    try:
        summary = _workflow_service(request, session).bootstrap_document(source_path)
        # Commit before returning so a follow-up read from the web UI can resolve
        # the newly created document immediately.
        session.commit()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_document_summary_response(summary)


@router.post("/bootstrap-upload", response_model=DocumentSummaryResponse, status_code=status.HTTP_201_CREATED)
def bootstrap_uploaded_document(
    request: Request,
    source_file: UploadFile = File(...),
    session: Session = Depends(get_db_session),
) -> DocumentSummaryResponse:
    filename = _safe_upload_filename(source_file.filename)
    upload_root = _upload_root(request)
    upload_root.mkdir(parents=True, exist_ok=True)
    target_dir = upload_root / uuid4().hex
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / filename
    try:
        with target_path.open("wb") as buffer:
            shutil.copyfileobj(source_file.file, buffer)
        summary = _workflow_service(request, session).bootstrap_document(target_path)
        # Commit before returning so a follow-up read from the web UI can resolve
        # the newly created document immediately.
        session.commit()
    except ValueError as exc:
        _cleanup_path(target_path)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception:
        _cleanup_path(target_path)
        raise
    finally:
        source_file.file.close()
    return _to_document_summary_response(summary)


@router.get("/history", response_model=DocumentHistoryPageResponse)
def list_document_history(
    request: Request,
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    query: str | None = Query(default=None, min_length=1, max_length=200),
    source_type: SourceType | None = Query(default=None),
    status: DocumentStatus | None = Query(default=None),
    latest_run_status: DocumentRunStatus | None = Query(default=None),
    merged_export_ready: bool | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> DocumentHistoryPageResponse:
    page = _workflow_service(request, session).list_document_history(
        limit=limit,
        offset=offset,
        query=query,
        source_type=source_type,
        status=status,
        latest_run_status=latest_run_status,
        merged_export_ready=merged_export_ready,
    )
    return _to_document_history_page_response(page)


@router.post("/history/backfill", response_model=DocumentHistoryBackfillResponse)
def backfill_document_history(
    session: Session = Depends(get_db_session),
) -> DocumentHistoryBackfillResponse:
    bind = session.get_bind()
    database_url = str(bind.url) if bind is not None else get_settings().database_url
    session.close()
    return DocumentHistoryBackfillResponse(
        imported_document_count=backfill_legacy_history(database_url),
    )


@router.get("/{document_id}", response_model=DocumentSummaryResponse)
def get_document(
    document_id: str,
    request: Request,
    session: Session = Depends(get_db_session),
) -> DocumentSummaryResponse:
    try:
        summary = _workflow_service(request, session).get_document_summary(document_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _to_document_summary_response(summary)


@router.get("/{document_id}/exports", response_model=DocumentExportDashboardResponse)
def get_document_exports(
    document_id: str,
    request: Request,
    export_type: ExportType | None = Query(default=None),
    export_status: ExportStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db_session),
) -> DocumentExportDashboardResponse:
    try:
        dashboard = _workflow_service(request, session).get_document_export_dashboard(
            document_id,
            export_type=export_type,
            status=export_status,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _to_export_dashboard_response(dashboard)


@router.get("/{document_id}/chapters/worklist", response_model=DocumentChapterWorklistResponse)
def get_document_chapter_worklist(
    document_id: str,
    request: Request,
    queue_priority: Literal["immediate", "high", "medium"] | None = Query(default=None),
    sla_status: Literal["on_track", "due_soon", "breached", "unknown"] | None = Query(default=None),
    owner_ready: bool | None = Query(default=None),
    needs_immediate_attention: bool | None = Query(default=None),
    assigned: bool | None = Query(default=None),
    assigned_owner_name: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db_session),
) -> DocumentChapterWorklistResponse:
    try:
        worklist = _workflow_service(request, session).get_document_chapter_worklist(
            document_id,
            queue_priority=queue_priority,
            sla_status=sla_status,
            owner_ready=owner_ready,
            needs_immediate_attention=needs_immediate_attention,
            assigned=assigned,
            assigned_owner_name=assigned_owner_name,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _to_chapter_worklist_response(worklist)


@router.get(
    "/{document_id}/chapters/{chapter_id}/worklist",
    response_model=DocumentChapterWorklistDetailResponse,
)
def get_document_chapter_worklist_detail(
    document_id: str,
    chapter_id: str,
    request: Request,
    session: Session = Depends(get_db_session),
) -> DocumentChapterWorklistDetailResponse:
    try:
        detail = _workflow_service(request, session).get_document_chapter_worklist_detail(
            document_id,
            chapter_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _to_chapter_worklist_detail_response(detail)


@router.put(
    "/{document_id}/chapters/{chapter_id}/worklist/assignment",
    response_model=ChapterWorklistAssignmentResponse,
)
def assign_document_chapter_worklist(
    document_id: str,
    chapter_id: str,
    payload: ChapterWorklistAssignmentRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> ChapterWorklistAssignmentResponse:
    try:
        assignment = _workflow_service(request, session).assign_document_chapter_worklist_owner(
            document_id,
            chapter_id,
            owner_name=payload.owner_name,
            assigned_by=payload.assigned_by,
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _to_assignment_response(assignment)


@router.post(
    "/{document_id}/chapters/{chapter_id}/worklist/assignment/clear",
    response_model=ChapterWorklistAssignmentClearResponse,
)
def clear_document_chapter_worklist_assignment(
    document_id: str,
    chapter_id: str,
    payload: ChapterWorklistAssignmentClearRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> ChapterWorklistAssignmentClearResponse:
    try:
        assignment = _workflow_service(request, session).clear_document_chapter_worklist_owner(
            document_id,
            chapter_id,
            cleared_by=payload.cleared_by,
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ChapterWorklistAssignmentClearResponse(
        document_id=document_id,
        chapter_id=chapter_id,
        cleared=True,
        cleared_by=payload.cleared_by,
        note=payload.note,
        cleared_assignment_id=assignment.assignment_id,
    )


@router.get("/{document_id}/chapters/{chapter_id}/exports/download")
def download_document_chapter_export(
    document_id: str,
    chapter_id: str,
    request: Request,
    export_type: ExportType = Query(default=ExportType.BILINGUAL_HTML),
    session: Session = Depends(get_db_session),
) -> FileResponse:
    workflow = _workflow_service(request, session)
    chapter_bundle = workflow.export_repository.load_chapter_bundle(chapter_id)
    try:
        records = workflow.export_repository.list_document_exports_filtered(
            document_id,
            export_type=export_type,
            status=ExportStatus.SUCCEEDED,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    chapter_record = next(
        (
            record
            for record in records
            if str((record.input_version_bundle_json or {}).get("chapter_id") or "") == chapter_id
        ),
        None,
    )
    if chapter_record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No successful {export_type.value} export is available for chapter {chapter_id}.",
        )

    artifact_roots = _artifact_roots(request)
    file_path = _resolve_artifact_path(chapter_record.file_path, roots=artifact_roots)
    archive_inputs = [
        ArchiveInput(path=file_path, archive_name=_preferred_archive_name(chapter_record.file_path, file_path)),
        *[ArchiveInput(path=sidecar_path) for sidecar_path in _export_sidecar_paths(file_path)],
    ]
    if len(archive_inputs) == 1:
        return FileResponse(
            path=file_path,
            media_type=_artifact_media_type(file_path),
            filename=_chapter_export_download_filename(
                chapter_bundle.document,
                chapter_bundle.chapter,
                export_type,
                file_suffix=file_path.suffix or "",
            ),
        )

    archive_path = _build_export_archive(document_id, export_type, archive_inputs)
    return FileResponse(
        path=archive_path,
        media_type="application/zip",
        filename=_chapter_export_download_filename(
            chapter_bundle.document,
            chapter_bundle.chapter,
            export_type,
            file_suffix=".zip",
            archive=True,
        ),
        background=BackgroundTask(_cleanup_path, archive_path),
    )


@router.get("/{document_id}/exports/download")
def download_document_export(
    document_id: str,
    request: Request,
    export_type: ExportType = Query(...),
    session: Session = Depends(get_db_session),
) -> FileResponse:
    workflow = _workflow_service(request, session)
    document = workflow.export_repository.get_document(document_id)
    try:
        primary_records = workflow.export_repository.list_document_exports_filtered(
            document_id,
            export_type=export_type,
            status=ExportStatus.SUCCEEDED,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    if not primary_records:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No successful {export_type.value} exports are available for download.",
        )

    related_records = []
    include_related_exports = export_type == ExportType.MERGED_HTML
    if include_related_exports:
        related_records = workflow.export_repository.list_document_exports_filtered(
            document_id,
            export_type=ExportType.BILINGUAL_HTML,
            status=ExportStatus.SUCCEEDED,
        )

    artifact_roots = _artifact_roots(request)
    files = [
        _resolve_artifact_path(record.file_path, roots=artifact_roots)
        for record in [*primary_records, *related_records]
    ]
    archive_inputs: list[ArchiveInput] = []
    seen_paths: set[str] = set()
    for record, file_path in zip([*primary_records, *related_records], files, strict=False):
        _append_archive_input(
            archive_inputs,
            seen_paths,
            file_path,
            preferred_archive_name=_preferred_archive_name(record.file_path, file_path),
        )
    if len(files) == 1 and len(archive_inputs) == 1 and not include_related_exports:
        file_path = files[0]
        return FileResponse(
            path=file_path,
            media_type=_artifact_media_type(file_path),
            filename=_document_export_download_filename(
                document,
                export_type,
                file_suffix=file_path.suffix or "",
            ),
        )

    archive_path = _build_export_archive(
        document_id,
        export_type,
        archive_inputs,
        include_related_exports=include_related_exports,
    )
    return FileResponse(
        path=archive_path,
        media_type="application/zip",
        filename=_document_export_download_filename(
            document,
            export_type,
            file_suffix=".zip",
            include_related_exports=include_related_exports,
        ),
        background=BackgroundTask(_cleanup_path, archive_path),
    )


@router.get("/{document_id}/exports/{export_id}", response_model=ExportDetailResponse)
def get_document_export_detail(
    document_id: str,
    export_id: str,
    request: Request,
    session: Session = Depends(get_db_session),
) -> ExportDetailResponse:
    try:
        detail = _workflow_service(request, session).get_document_export_detail(document_id, export_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _to_export_detail_response(detail)


@router.get(
    "/{document_id}/chapters/{chapter_id}/memory-proposals",
    response_model=ChapterMemoryProposalListResponse,
)
def list_chapter_memory_proposals(
    document_id: str,
    chapter_id: str,
    request: Request,
    proposal_status: MemoryProposalStatus | None = Query(default=None, alias="status"),
    session: Session = Depends(get_db_session),
) -> ChapterMemoryProposalListResponse:
    try:
        proposals = _workflow_service(request, session).list_chapter_memory_proposals(
            document_id,
            chapter_id,
            status=proposal_status.value if proposal_status is not None else None,
        )
    except ValueError as exc:
        raise _proposal_http_exception(exc) from exc
    return _to_chapter_memory_proposal_list_response(
        document_id=document_id,
        chapter_id=chapter_id,
        status_filter=proposal_status.value if proposal_status is not None else None,
        proposals=proposals,
    )


@router.post(
    "/{document_id}/chapters/{chapter_id}/memory-proposals/{proposal_id}/approve",
    response_model=ChapterMemoryProposalDecisionResponse,
)
def approve_chapter_memory_proposal(
    document_id: str,
    chapter_id: str,
    proposal_id: str,
    request: Request,
    payload: ChapterMemoryProposalDecisionRequest | None = None,
    session: Session = Depends(get_db_session),
) -> ChapterMemoryProposalDecisionResponse:
    try:
        result = _workflow_service(request, session).approve_chapter_memory_proposal(
            document_id,
            chapter_id,
            proposal_id,
            actor_name=(payload.actor_name if payload is not None else None),
            note=(payload.note if payload is not None else None),
        )
    except ValueError as exc:
        raise _proposal_http_exception(exc) from exc
    return _to_chapter_memory_proposal_decision_response(result)


@router.post(
    "/{document_id}/chapters/{chapter_id}/memory-proposals/{proposal_id}/reject",
    response_model=ChapterMemoryProposalDecisionResponse,
)
def reject_chapter_memory_proposal(
    document_id: str,
    chapter_id: str,
    proposal_id: str,
    request: Request,
    payload: ChapterMemoryProposalDecisionRequest | None = None,
    session: Session = Depends(get_db_session),
) -> ChapterMemoryProposalDecisionResponse:
    try:
        result = _workflow_service(request, session).reject_chapter_memory_proposal(
            document_id,
            chapter_id,
            proposal_id,
            actor_name=(payload.actor_name if payload is not None else None),
            note=(payload.note if payload is not None else None),
        )
    except ValueError as exc:
        raise _proposal_http_exception(exc) from exc
    return _to_chapter_memory_proposal_decision_response(result)


@router.post("/{document_id}/translate", response_model=TranslateDocumentResponse)
def translate_document(
    document_id: str,
    payload: TranslateDocumentRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> TranslateDocumentResponse:
    try:
        result = _workflow_service(request, session).translate_document(document_id, payload.packet_ids)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _to_translate_response(result)


@router.post("/{document_id}/review", response_model=ReviewDocumentResponse)
def review_document(
    document_id: str,
    request: Request,
    session: Session = Depends(get_db_session),
) -> ReviewDocumentResponse:
    try:
        result = _workflow_service(request, session).review_document(document_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _to_review_response(result)


@router.post("/{document_id}/export", response_model=ExportDocumentResponse)
def export_document(
    document_id: str,
    payload: ExportDocumentRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> ExportDocumentResponse:
    try:
        result = _workflow_service(request, session).export_document(
            document_id,
            ExportType(payload.export_type),
            auto_execute_followup_on_gate=payload.auto_execute_followup_on_gate,
            max_auto_followup_attempts=payload.max_auto_followup_attempts,
        )
    except ExportGateError as exc:
        session.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.to_http_detail()) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _to_export_response(result)
