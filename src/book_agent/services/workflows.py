from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any

from sqlalchemy import and_, case, distinct, func, or_, select
from sqlalchemy.orm import Session

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
    MemoryProposalStatus,
    MemoryScopeType,
    MemoryStatus,
    PacketStatus,
    SnapshotType,
    SourceType,
)
from book_agent.domain.models import Chapter, ChapterMemoryProposal, ChapterWorklistAssignment, Document, MemorySnapshot, Sentence
from book_agent.domain.models.ops import AuditEvent, DocumentRun
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
from book_agent.infra.storage.blobs import blob_root_for_export_root, stamp_export_records
from book_agent.orchestrator.bootstrap import BootstrapOrchestrator
from book_agent.orchestrator.pipeline_stage_cache import read_cached_stages
from book_agent.orchestrator.rerun import build_rerun_plan, packet_scope_ids_for_issue
from book_agent.services.actions import ActionExecutionArtifacts, IssueActionExecutor
from book_agent.services.bootstrap import BootstrapArtifacts
from book_agent.services.chapter_concept_autolock import ChapterConceptAutoLockService, build_default_concept_resolver
from book_agent.services.export import ExportArtifacts, ExportGateError, ExportService
from book_agent.services.epub_structure_refresh import EpubStructureRefreshArtifacts, EpubStructureRefreshService
from book_agent.services.pdf_structure_refresh import PdfStructureRefreshArtifacts, PdfStructureRefreshService
from book_agent.services.realign import RealignService
from book_agent.services.rebuild import TargetedRebuildService
from book_agent.services.rerun import RerunExecutionArtifacts, RerunService
from book_agent.services.review import NaturalnessSummary as ReviewNaturalnessSummary, ReviewArtifacts, ReviewService
from book_agent.services.translation import TranslationExecutionArtifacts, TranslationService
from book_agent.workers.translator import TranslationWorker

from book_agent.application.read_models import (
    ChapterSummary,
    StoredChapterQualitySummary,
    NaturalnessSummarySnapshot,
    DocumentSummary,
    DocumentHistoryEntry,
    DocumentHistoryPage,
    DocumentTranslationResult,
    ChapterMemoryProposalSummary,
    ChapterMemoryProposalDecisionResult,
    ChapterMemoryProposalDecisionAuditSummary,
    ChapterMemoryProposalSurface,
    ChapterMemoryProposalQueueSummary,
    ChapterReviewResult,
    ChapterReviewSkip,
    DocumentReviewResult,
    DocumentBlockerRepairExecution,
    DocumentBlockerRepairResult,
    ReviewAutoFollowupExecution,
    ChapterExportResult,
    DocumentExportResult,
    ExportAutoFollowupSummary,
    ExportMisalignmentCountSummary,
    TranslationUsageSummary,
    TranslationUsageBreakdownEntry,
    TranslationUsageTimelineEntry,
    TranslationUsageHighlights,
    IssueHotspotEntry,
    IssueChapterPressureEntry,
    IssueChapterHighlights,
    IssueChapterBreakdownEntry,
    IssueChapterHeatmapEntry,
    IssueChapterQueueEntry,
    IssueActivityTimelineEntry,
    IssueActivityBreakdownEntry,
    IssueActivityHighlights,
    ExportIssueStatusSummary,
    ExportVersionEvidenceSummary,
    ExportRecordSummary,
    DocumentExportDashboard,
    DocumentChapterWorklist,
    ChapterWorklistAssignmentSummary,
    ChapterOwnerWorkloadSummary,
    ChapterWorklistIssue,
    ChapterWorklistAction,
    ChapterWorklistAssignmentHistoryEntry,
    ChapterWorklistTimelineEntry,
    DocumentChapterWorklistDetail,
    ExportDetail,
    ExportAutoFollowupExecution,
    ActionWorkflowResult,
)

from book_agent.application import analytics

from book_agent.application.memory_proposals import ChapterMemoryProposalService

from book_agent.application.issue_queries import IssueQueries


from book_agent.application.worklist import ChapterWorklistService


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


class DocumentBusyError(RuntimeError):
    """Raised when a destructive operation targets a document whose run
    is still in the orchestrator's hot active set (RUNNING/DRAINING)."""


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
    stages = dict(read_cached_stages(pipeline) or {})
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


class DocumentWorkflowService:
    def __init__(
        self,
        session: Session,
        export_root: str | Path = "artifacts/exports",
        translation_worker: TranslationWorker | None = None,
        translation_auto_commit_memory: bool = False,
    ):
        self.session = session
        self.bootstrap_repository = BootstrapRepository(session)
        self.review_repository = ReviewRepository(session)
        self.export_repository = ExportRepository(session)
        self.run_control_repository = RunControlRepository(session)
        self.translation_service = TranslationService(
            TranslationRepository(session),
            worker=translation_worker,
            default_auto_commit_memory=translation_auto_commit_memory,
        )
        self.memory_service = self.translation_service.memory_service
        self.review_service = ReviewService(self.review_repository)
        self.export_service = ExportService(
            self.export_repository,
            output_root=export_root,
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
        self.memory_proposals = ChapterMemoryProposalService(session, self.memory_service)
        self.issue_queries = IssueQueries(session)
        self.worklist = ChapterWorklistService(
            session,
            self.bootstrap_repository,
            self.review_repository,
            self.ops_repository,
            self.issue_queries,
            self.memory_proposals,
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
        open_issue_counts = self.issue_queries.open_issue_counts(document_id)
        quality_summary_map = self.review_repository.load_quality_summaries_for_document(document_id)
        chapter_export_map, merged_export_ready, latest_merged_export_at = self._chapter_export_status_map(document_id)
        latest_run = self._latest_document_run(document_id)
        latest_run_current_stage, _, _ = _history_run_progress(latest_run)
        chapter_pdf_image_summary_map = self._chapter_pdf_image_summary_map(bundle)

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
                    quality_summary=analytics.stored_quality_summary(
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
            chapters=chapter_summaries,
        )

    def delete_document(self, document_id: str) -> None:
        """Hard-delete a document and all dependent rows via FK CASCADE.

        Refuses to delete while a run is in the hot active set
        ({RUNNING, DRAINING}) to avoid yanking the rug under the
        orchestrator. Terminal and dormant states (QUEUED, PAUSED,
        SUCCEEDED, FAILED, …) are safe to clean up.
        """
        document = self.session.get(Document, document_id)
        if document is None:
            raise ValueError(f"Document not found: {document_id}")
        latest_run = self._latest_document_run(document_id)
        if latest_run is not None and latest_run.status in {
            DocumentRunStatus.RUNNING,
            DocumentRunStatus.DRAINING,
        }:
            raise DocumentBusyError(
                f"Document {document_id} has an active run ({latest_run.status.value}); "
                "pause or cancel it before deletion."
            )
        self.session.delete(document)
        self.session.flush()

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
        recorded_memory_proposal_count = 0
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
            recorded_memory_proposal_count += 1
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
            memory_commit_mode=(
                "eager_commit" if self.translation_service.default_auto_commit_memory else "proposal_first"
            ),
            recorded_memory_proposal_count=recorded_memory_proposal_count,
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
        skipped_chapters: list[ChapterReviewSkip] = []

        for chapter_bundle in bundle.chapters:
            if chapter_bundle.translation_packets and not all(
                packet.status == PacketStatus.TRANSLATED for packet in chapter_bundle.translation_packets
            ):
                pending = sum(
                    1 for p in chapter_bundle.translation_packets
                    if p.status != PacketStatus.TRANSLATED and p.status != PacketStatus.FAILED
                )
                failed = sum(
                    1 for p in chapter_bundle.translation_packets
                    if p.status == PacketStatus.FAILED
                )
                skipped_chapters.append(
                    ChapterReviewSkip(
                        chapter_id=chapter_bundle.chapter.id,
                        reason=(
                            "translate_incomplete" if failed == 0
                            else "translate_failed"
                        ),
                        pending_packet_count=pending,
                        failed_packet_count=failed,
                    )
                )
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
                    naturalness_summary=analytics.naturalness_summary(artifacts.summary.naturalness_summary),
                )
            )

        return DocumentReviewResult(
            document_id=bundle.document.id,
            total_issue_count=total_issue_count,
            total_action_count=total_action_count,
            chapter_results=chapter_results,
            skipped_chapters=skipped_chapters,
            total_chapter_count=len(bundle.chapters),
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

    def list_chapter_memory_proposals(
        self,
        document_id: str,
        chapter_id: str,
        *,
        status: str | None = None,
    ) -> list[ChapterMemoryProposalSummary]:
        return self.memory_proposals.list_chapter_memory_proposals(document_id, chapter_id, status=status)

    def approve_chapter_memory_proposal(
        self,
        document_id: str,
        chapter_id: str,
        proposal_id: str,
        *,
        actor_name: str | None = None,
        note: str | None = None,
    ) -> ChapterMemoryProposalDecisionResult:
        return self.memory_proposals.approve_chapter_memory_proposal(
            document_id,
            chapter_id,
            proposal_id,
            actor_name=actor_name,
            note=note,
        )

    def reject_chapter_memory_proposal(
        self,
        document_id: str,
        chapter_id: str,
        proposal_id: str,
        *,
        actor_name: str | None = None,
        note: str | None = None,
    ) -> ChapterMemoryProposalDecisionResult:
        return self.memory_proposals.reject_chapter_memory_proposal(
            document_id,
            chapter_id,
            proposal_id,
            actor_name=actor_name,
            note=note,
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
            resolver=build_default_concept_resolver(translation_worker=self.translation_service.worker),
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
        result = self._write_document_export(
            document_id,
            export_type,
            auto_execute_followup_on_gate=auto_execute_followup_on_gate,
            max_auto_followup_attempts=max_auto_followup_attempts,
        )
        self._stamp_export_blobs(document_id, export_type)
        return result

    def _stamp_export_blobs(self, document_id: str, export_type: ExportType) -> None:
        # Every export writer path (API, run executor, CLI) goes through
        # export_document, so content-address the fresh files here.
        self.session.flush()
        records = self.export_repository.list_document_exports_filtered(
            document_id,
            export_type=export_type,
            status=ExportStatus.SUCCEEDED,
        )
        stamp_export_records(
            self.session,
            records,
            blob_root=blob_root_for_export_root(self.export_service.output_root),
        )

    def _write_document_export(
        self,
        document_id: str,
        export_type: ExportType,
        *,
        auto_execute_followup_on_gate: bool,
        max_auto_followup_attempts: int,
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
                    try:
                        result = self.execute_action(
                            followup_action.action_id,
                            run_followup=followup_action.suggested_run_followup,
                        )
                    except ValueError:
                        # Skip actions that are not applicable to this document type
                        # (e.g. PDF structure refresh on EPUB documents)
                        continue
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
            )
        if export_type == ExportType.MERGED_MARKDOWN:
            artifacts = self.export_service.export_document_merged_markdown(document_id)
            document = self.session.get(type(bundle.document), document_id) or bundle.document
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
            )
        if export_type == ExportType.REBUILT_EPUB:
            artifacts = self.export_service.export_document_rebuilt_epub(document_id)
            document = self.session.get(type(bundle.document), document_id) or bundle.document
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
            )
        if export_type == ExportType.ZH_EPUB:
            artifacts = self.export_service.export_document_zh_epub(document_id)
            document = self.session.get(type(bundle.document), document_id) or bundle.document
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
            )
        if export_type == ExportType.REBUILT_PDF:
            artifacts = self.export_service.export_document_rebuilt_pdf(document_id)
            document = self.session.get(type(bundle.document), document_id) or bundle.document
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
            auto_followup_summary = analytics.export_auto_followup_summary(export)
            if auto_followup_summary is not None:
                total_auto_followup_executed_count += auto_followup_summary.executed_event_count

        records = [analytics.export_record_summary(export) for export in filtered_exports]
        successful_export_count = sum(1 for export in exports if export.status.value == "succeeded")
        latest_export_at = exports[0].created_at.isoformat() if exports else None
        record_count = len(records)
        has_more = (offset + record_count) < filtered_export_count
        issue_chapter_pressure = self.issue_queries.chapter_pressure(document_id)
        issue_chapter_breakdown = self.issue_queries.chapter_breakdown(document_id)
        issue_chapter_heatmap = analytics.issue_chapter_heatmap(issue_chapter_breakdown)
        issue_chapter_activity = self.issue_queries.chapter_activity_map(document_id)
        issue_chapter_worklist_meta = self.issue_queries.chapter_worklist_meta(document_id)
        chapter_assignment_map = self.issue_queries.chapter_assignment_map(document_id)
        chapter_memory_proposal_map = self.memory_proposals.proposal_queue_map(document_id)
        issue_activity_breakdown = self.issue_queries.activity_breakdown(document_id)
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
            translation_usage_summary=analytics.translation_usage_summary_from_runs(document_translation_runs),
            translation_usage_breakdown=analytics.translation_usage_breakdown_from_runs(document_translation_runs),
            translation_usage_timeline=analytics.translation_usage_timeline_from_runs(document_translation_runs),
            translation_usage_highlights=analytics.translation_usage_highlights_from_runs(document_translation_runs),
            issue_hotspots=self.issue_queries.issue_hotspots(document_id),
            issue_chapter_pressure=issue_chapter_pressure,
            issue_chapter_highlights=analytics.issue_chapter_highlights(issue_chapter_pressure),
            issue_chapter_breakdown=issue_chapter_breakdown,
            issue_chapter_heatmap=issue_chapter_heatmap,
            issue_chapter_queue=analytics.issue_chapter_queue(
                issue_chapter_heatmap,
                issue_chapter_activity,
                issue_chapter_worklist_meta,
                chapter_assignment_map,
                chapter_memory_proposal_map,
            ),
            issue_activity_timeline=self.issue_queries.activity_timeline(document_id),
            issue_activity_breakdown=issue_activity_breakdown,
            issue_activity_highlights=analytics.issue_activity_highlights(issue_activity_breakdown),
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
            translation_usage_summary=analytics.translation_usage_summary_from_json(
                bundle.get("translation_usage_summary")
            ),
            translation_usage_breakdown=analytics.translation_usage_breakdown_from_json(
                bundle.get("translation_usage_breakdown")
            ),
            translation_usage_timeline=analytics.translation_usage_timeline_from_json(
                bundle.get("translation_usage_timeline")
            ),
            translation_usage_highlights=analytics.translation_usage_highlights_from_json(
                bundle.get("translation_usage_highlights")
            ),
            issue_status_summary=analytics.export_issue_status_summary(bundle.get("issue_status_summary")),
            export_auto_followup_summary=analytics.export_auto_followup_summary(export),
            export_time_misalignment_counts=analytics.export_misalignment_summary(export),
            version_evidence_summary=analytics.export_version_evidence_summary(export),
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
        return self.worklist.get_document_chapter_worklist(
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

    def get_document_chapter_worklist_detail(
        self,
        document_id: str,
        chapter_id: str,
    ) -> DocumentChapterWorklistDetail:
        return self.worklist.get_document_chapter_worklist_detail(document_id, chapter_id)

    def assign_document_chapter_worklist_owner(
        self,
        document_id: str,
        chapter_id: str,
        *,
        owner_name: str,
        assigned_by: str,
        note: str | None = None,
    ) -> ChapterWorklistAssignmentSummary:
        return self.worklist.assign_document_chapter_worklist_owner(
            document_id,
            chapter_id,
            owner_name=owner_name,
            assigned_by=assigned_by,
            note=note,
        )

    def clear_document_chapter_worklist_owner(
        self,
        document_id: str,
        chapter_id: str,
        *,
        cleared_by: str,
        note: str | None = None,
    ) -> ChapterWorklistAssignmentSummary:
        return self.worklist.clear_document_chapter_worklist_owner(
            document_id,
            chapter_id,
            cleared_by=cleared_by,
            note=note,
        )

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
