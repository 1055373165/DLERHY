"""Read-side document queries: summary, history, export dashboard and export detail."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from book_agent.application import analytics
from book_agent.application.issue_queries import IssueQueries
from book_agent.application.memory_proposals import ChapterMemoryProposalService
from book_agent.application.read_models import (
    ChapterSummary,
    DocumentExportDashboard,
    DocumentHistoryEntry,
    DocumentHistoryPage,
    DocumentSummary,
    ExportDetail,
)
from book_agent.domain.document_titles import (
    display_author_value,
    document_display_title,
    document_source_title,
)
from book_agent.domain.enums import (
    DocumentRunStatus,
    DocumentStatus,
    ExportStatus,
    ExportType,
    SourceType,
)
from book_agent.domain.models import (
    Chapter,
    Document,
    Sentence,
)
from book_agent.domain.models.ops import DocumentRun
from book_agent.domain.models.review import Export
from book_agent.domain.models.translation import TranslationPacket
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.infra.repositories.export import ExportRepository
from book_agent.infra.repositories.review import ReviewRepository
from book_agent.orchestrator.pipeline_stage_cache import read_cached_stages


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


class DocumentQueryService:
    """Answers the document summary, library history and export dashboard queries."""

    def __init__(
        self,
        session: Session,
        bootstrap_repository: BootstrapRepository,
        review_repository: ReviewRepository,
        export_repository: ExportRepository,
        issue_queries: IssueQueries,
        memory_proposals: ChapterMemoryProposalService,
    ) -> None:
        self.session = session
        self.bootstrap_repository = bootstrap_repository
        self.review_repository = review_repository
        self.export_repository = export_repository
        self.issue_queries = issue_queries
        self.memory_proposals = memory_proposals

    def get_document_summary(self, document_id: str) -> DocumentSummary:
        bundle = self.bootstrap_repository.load_document_bundle(document_id)
        open_issue_counts = self.issue_queries.open_issue_counts(document_id)
        quality_summary_map = self.review_repository.load_quality_summaries_for_document(document_id)
        chapter_export_map, merged_export_ready, latest_merged_export_at = self._chapter_export_status_map(document_id)
        latest_run = self.latest_document_run(document_id)
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
            author=display_author_value(bundle.document.author),
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
                    author=display_author_value(document.author),
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

    def latest_document_run(self, document_id: str) -> DocumentRun | None:
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
