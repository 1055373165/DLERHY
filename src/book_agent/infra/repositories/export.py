from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from book_agent.domain.enums import ExportStatus, ExportType, MemoryScopeType, MemoryStatus, SnapshotType
from book_agent.domain.enums import IssueStatus
from book_agent.domain.models import AuditEvent, Block, BookProfile, Chapter, Document, MemorySnapshot, Sentence
from book_agent.domain.models.review import ChapterQualitySummary, Export, ReviewIssue
from book_agent.domain.models.translation import AlignmentEdge, TargetSegment, TranslationPacket, TranslationRun


@dataclass(slots=True)
class ChapterExportBundle:
    chapter: Chapter
    document: Document
    book_profile: BookProfile | None
    blocks: list[Block]
    sentences: list[Sentence]
    packets: list[TranslationPacket]
    translation_runs: list[TranslationRun]
    target_segments: list[TargetSegment]
    alignment_edges: list[AlignmentEdge]
    review_issues: list[ReviewIssue]
    quality_summary: ChapterQualitySummary | None
    active_snapshots: list[MemorySnapshot]
    audit_events: list[AuditEvent]


@dataclass(slots=True)
class DocumentExportBundle:
    document: Document
    book_profile: BookProfile | None
    chapters: list[ChapterExportBundle]


class ExportRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_document(self, document_id: str) -> Document:
        document = self.session.get(Document, document_id)
        if document is None:
            raise ValueError(f"Document not found: {document_id}")
        return document

    def list_document_chapters(self, document_id: str) -> list[Chapter]:
        return self.session.scalars(
            select(Chapter).where(Chapter.document_id == document_id).order_by(Chapter.ordinal)
        ).all()

    def list_document_exports(self, document_id: str) -> list[Export]:
        return self.list_document_exports_filtered(document_id)

    def get_document_export(self, document_id: str, export_id: str) -> Export:
        self.get_document(document_id)
        export = self.session.get(Export, export_id)
        if export is None or export.document_id != document_id:
            raise ValueError(f"Export not found: {export_id}")
        return export

    def list_document_exports_filtered(
        self,
        document_id: str,
        *,
        export_type: ExportType | None = None,
        status: ExportStatus | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Export]:
        self.get_document(document_id)
        statement = self._document_exports_query(document_id, export_type=export_type, status=status)
        statement = statement.order_by(Export.created_at.desc(), Export.id.desc())
        if offset:
            statement = statement.offset(offset)
        if limit is not None:
            statement = statement.limit(limit)
        return self.session.scalars(statement).all()

    def count_document_exports(
        self,
        document_id: str,
        *,
        export_type: ExportType | None = None,
        status: ExportStatus | None = None,
    ) -> int:
        self.get_document(document_id)
        statement = select(func.count(Export.id)).select_from(Export)
        statement = self._apply_document_export_filters(
            statement,
            document_id,
            export_type=export_type,
            status=status,
        )
        return int(self.session.scalar(statement) or 0)

    def list_document_translation_runs(self, document_id: str) -> list[TranslationRun]:
        self.get_document(document_id)
        statement = (
            select(TranslationRun)
            .join(TranslationPacket, TranslationRun.packet_id == TranslationPacket.id)
            .join(Chapter, TranslationPacket.chapter_id == Chapter.id)
            .where(Chapter.document_id == document_id)
            .order_by(TranslationRun.created_at.desc(), TranslationRun.id.desc())
        )
        return self.session.scalars(statement).all()

    def _document_exports_query(
        self,
        document_id: str,
        *,
        export_type: ExportType | None = None,
        status: ExportStatus | None = None,
    ) -> Select[tuple[Export]]:
        statement = select(Export)
        return self._apply_document_export_filters(
            statement,
            document_id,
            export_type=export_type,
            status=status,
        )

    def _apply_document_export_filters(
        self,
        statement: Select,
        document_id: str,
        *,
        export_type: ExportType | None = None,
        status: ExportStatus | None = None,
    ) -> Select:
        statement = statement.where(Export.document_id == document_id)
        if export_type is not None:
            statement = statement.where(Export.export_type == export_type)
        if status is not None:
            statement = statement.where(Export.status == status)
        return statement

    def load_chapter_bundle(self, chapter_id: str) -> ChapterExportBundle:
        chapter = self.session.get(Chapter, chapter_id)
        if chapter is None:
            raise ValueError(f"Chapter not found: {chapter_id}")
        document = self.get_document(chapter.document_id)
        blocks = self.session.scalars(
            select(Block).where(Block.chapter_id == chapter_id).order_by(Block.ordinal)
        ).all()

        sentences = self.session.scalars(select(Sentence).where(Sentence.chapter_id == chapter_id)).all()
        packets = self.session.scalars(
            select(TranslationPacket).where(TranslationPacket.chapter_id == chapter_id).order_by(TranslationPacket.id)
        ).all()
        packet_ids = [packet.id for packet in packets]
        translation_runs = self.session.scalars(
            select(TranslationRun).where(TranslationRun.packet_id.in_(packet_ids))
        ).all() if packet_ids else []
        target_segments = self.session.scalars(
            select(TargetSegment).where(TargetSegment.chapter_id == chapter_id)
        ).all()
        target_ids = [segment.id for segment in target_segments]
        alignment_edges = self.session.scalars(
            select(AlignmentEdge).where(AlignmentEdge.target_segment_id.in_(target_ids))
        ).all() if target_ids else []
        review_issues = self.session.scalars(
            select(ReviewIssue).where(ReviewIssue.chapter_id == chapter_id)
        ).all()
        book_profile = self.session.scalars(
            select(BookProfile)
            .where(BookProfile.document_id == chapter.document_id)
            .order_by(BookProfile.version.desc())
        ).first()
        quality_summary = self.session.scalars(
            select(ChapterQualitySummary).where(ChapterQualitySummary.chapter_id == chapter_id)
        ).first()
        active_snapshots = self.session.scalars(
            select(MemorySnapshot).where(
                MemorySnapshot.document_id == chapter.document_id,
                MemorySnapshot.status == MemoryStatus.ACTIVE,
                (
                    (
                        (MemorySnapshot.scope_type == MemoryScopeType.CHAPTER)
                        & (MemorySnapshot.scope_id == chapter_id)
                        & (MemorySnapshot.snapshot_type == SnapshotType.CHAPTER_BRIEF)
                    )
                    | (
                        (MemorySnapshot.scope_type == MemoryScopeType.GLOBAL)
                        & (MemorySnapshot.scope_id.is_(None))
                        & (MemorySnapshot.snapshot_type.in_([SnapshotType.TERMBASE, SnapshotType.ENTITY_REGISTRY]))
                    )
                ),
            )
        ).all()
        audit_object_ids = [chapter.id, *[packet.id for packet in packets], *[snapshot.id for snapshot in active_snapshots]]
        audit_events = self.session.scalars(
            select(AuditEvent)
            .where(AuditEvent.object_id.in_(audit_object_ids))
            .order_by(AuditEvent.created_at.desc())
        ).all() if audit_object_ids else []
        return ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=book_profile,
            blocks=blocks,
            sentences=sentences,
            packets=packets,
            translation_runs=translation_runs,
            target_segments=target_segments,
            alignment_edges=alignment_edges,
            review_issues=review_issues,
            quality_summary=quality_summary,
            active_snapshots=active_snapshots,
            audit_events=audit_events,
        )

    def load_document_bundle(self, document_id: str) -> DocumentExportBundle:
        document = self.get_document(document_id)
        chapters = self.list_document_chapters(document_id)
        book_profile = self.session.scalars(
            select(BookProfile)
            .where(BookProfile.document_id == document_id)
            .order_by(BookProfile.version.desc())
        ).first()
        return DocumentExportBundle(
            document=document,
            book_profile=book_profile,
            chapters=[self.load_chapter_bundle(chapter.id) for chapter in chapters],
        )

    def has_open_blocking_issues(self, chapter_id: str) -> bool:
        issue = self.session.scalars(
            select(ReviewIssue.id).where(
                ReviewIssue.chapter_id == chapter_id,
                ReviewIssue.blocking.is_(True),
                ReviewIssue.status == IssueStatus.OPEN,
            )
        ).first()
        return issue is not None

    def save_export(self, export: Export) -> None:
        self.session.merge(export)
