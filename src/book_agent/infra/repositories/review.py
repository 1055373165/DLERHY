from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.domain.enums import ArtifactStatus, IssueStatus, MemoryScopeType, MemoryStatus, SnapshotType, TermStatus
from book_agent.domain.models import Block, Chapter, Document, MemorySnapshot, Sentence
from book_agent.domain.models.review import ChapterQualitySummary, IssueAction, ReviewIssue
from book_agent.domain.models.translation import AlignmentEdge, TargetSegment, TermEntry, TranslationPacket, TranslationRun


@dataclass(slots=True)
class ChapterReviewBundle:
    document: Document
    chapter: Chapter
    blocks: list[Block]
    sentences: list[Sentence]
    packets: list[TranslationPacket]
    chapter_brief: MemorySnapshot | None
    chapter_translation_memory: MemorySnapshot | None
    translation_runs: list[TranslationRun]
    target_segments: list[TargetSegment]
    alignment_edges: list[AlignmentEdge]
    term_entries: list[TermEntry]
    existing_issues: list[ReviewIssue]


class ReviewRepository:
    def __init__(self, session: Session):
        self.session = session

    def load_chapter_bundle(self, chapter_id: str) -> ChapterReviewBundle:
        chapter = self.session.get(Chapter, chapter_id)
        if chapter is None:
            raise ValueError(f"Chapter not found: {chapter_id}")
        document = self.session.get(Document, chapter.document_id)
        if document is None:
            raise ValueError(f"Document not found: {chapter.document_id}")

        sentences = self.session.scalars(
            select(Sentence).where(Sentence.chapter_id == chapter_id)
        ).all()
        blocks = self.session.scalars(
            select(Block)
            .where(
                Block.chapter_id == chapter_id,
                Block.status == ArtifactStatus.ACTIVE,
            )
            .order_by(Block.ordinal)
        ).all()
        packets = self.session.scalars(
            select(TranslationPacket).where(TranslationPacket.chapter_id == chapter_id)
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
        term_entries = self.session.scalars(
            select(TermEntry).where(
                TermEntry.document_id == chapter.document_id,
                TermEntry.status == TermStatus.ACTIVE,
            )
        ).all()
        chapter_brief = self.session.scalar(
            select(MemorySnapshot).where(
                MemorySnapshot.document_id == chapter.document_id,
                MemorySnapshot.scope_type == MemoryScopeType.CHAPTER,
                MemorySnapshot.scope_id == chapter_id,
                MemorySnapshot.snapshot_type == SnapshotType.CHAPTER_BRIEF,
                MemorySnapshot.status == MemoryStatus.ACTIVE,
            )
        )
        chapter_translation_memory = self.session.scalar(
            select(MemorySnapshot).where(
                MemorySnapshot.document_id == chapter.document_id,
                MemorySnapshot.scope_type == MemoryScopeType.CHAPTER,
                MemorySnapshot.scope_id == chapter_id,
                MemorySnapshot.snapshot_type == SnapshotType.CHAPTER_TRANSLATION_MEMORY,
                MemorySnapshot.status == MemoryStatus.ACTIVE,
            )
        )
        existing_issues = self.session.scalars(
            select(ReviewIssue).where(ReviewIssue.chapter_id == chapter_id)
        ).all()

        return ChapterReviewBundle(
            document=document,
            chapter=chapter,
            blocks=blocks,
            sentences=sentences,
            packets=packets,
            chapter_brief=chapter_brief,
            chapter_translation_memory=chapter_translation_memory,
            translation_runs=translation_runs,
            target_segments=target_segments,
            alignment_edges=alignment_edges,
            term_entries=term_entries,
            existing_issues=existing_issues,
        )

    def save_review_artifacts(
        self,
        review_issues: list[ReviewIssue],
        issue_actions: list[IssueAction],
        chapter: Chapter,
        chapter_quality_summary: ChapterQualitySummary | None = None,
    ) -> None:
        self._merge_collection(review_issues)
        self.session.flush()

        self._merge_collection(issue_actions)
        self.session.merge(chapter)
        if chapter_quality_summary is not None:
            self.session.merge(chapter_quality_summary)
        self.session.flush()

    def load_quality_summaries_for_document(self, document_id: str) -> dict[str, ChapterQualitySummary]:
        summaries = self.session.scalars(
            select(ChapterQualitySummary).where(ChapterQualitySummary.document_id == document_id)
        ).all()
        return {summary.chapter_id: summary for summary in summaries}

    def upsert_chapter_quality_summary(
        self,
        *,
        document_id: str,
        chapter_id: str,
        issue_count: int,
        action_count: int,
        resolved_issue_count: int,
        coverage_ok: bool,
        alignment_ok: bool,
        term_ok: bool,
        format_ok: bool,
        blocking_issue_count: int,
        low_confidence_count: int,
        format_pollution_count: int,
    ) -> ChapterQualitySummary:
        summary = self.session.scalar(
            select(ChapterQualitySummary).where(ChapterQualitySummary.chapter_id == chapter_id)
        )
        if summary is None:
            summary = ChapterQualitySummary(
                document_id=document_id,
                chapter_id=chapter_id,
            )
        summary.document_id = document_id
        summary.chapter_id = chapter_id
        summary.issue_count = issue_count
        summary.action_count = action_count
        summary.resolved_issue_count = resolved_issue_count
        summary.coverage_ok = coverage_ok
        summary.alignment_ok = alignment_ok
        summary.term_ok = term_ok
        summary.format_ok = format_ok
        summary.blocking_issue_count = blocking_issue_count
        summary.low_confidence_count = low_confidence_count
        summary.format_pollution_count = format_pollution_count
        return summary

    def resolve_missing_issues(self, chapter_id: str, active_issue_ids: set[str], resolution_note: str) -> list[ReviewIssue]:
        existing = self.session.scalars(
            select(ReviewIssue).where(
                ReviewIssue.chapter_id == chapter_id,
                ReviewIssue.status.in_([IssueStatus.OPEN, IssueStatus.TRIAGED]),
            )
        ).all()
        resolved: list[ReviewIssue] = []
        for issue in existing:
            if issue.id in active_issue_ids:
                continue
            issue.status = IssueStatus.RESOLVED
            issue.resolution_note = resolution_note
            resolved.append(issue)
            self.session.merge(issue)
        return resolved

    def _merge_collection(self, collection: list[object]) -> None:
        deduped: dict[str, object] = {}
        passthrough: list[object] = []
        for item in collection:
            item_id = getattr(item, "id", None)
            if item_id is None:
                passthrough.append(item)
                continue
            deduped[str(item_id)] = item
        for item in [*deduped.values(), *passthrough]:
            self.session.merge(item)
