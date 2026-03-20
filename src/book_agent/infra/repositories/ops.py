from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from book_agent.domain.enums import ChapterStatus, IssueStatus, PacketSentenceRole, PacketStatus
from book_agent.domain.models import ArtifactInvalidation, Chapter, Sentence
from book_agent.domain.models.ops import AuditEvent
from book_agent.domain.models.review import IssueAction, ReviewIssue
from book_agent.domain.models.translation import AlignmentEdge, PacketSentenceMap, TargetSegment, TranslationPacket, TranslationRun


@dataclass(slots=True)
class PacketInvalidationBundle:
    packet: TranslationPacket
    sentence_ids: list[str]
    translation_runs: list[TranslationRun]
    target_segments: list[TargetSegment]
    alignment_edges: list[AlignmentEdge]


class OpsRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_issue_action(self, action_id: str) -> IssueAction:
        action = self.session.get(IssueAction, action_id)
        if action is None:
            raise ValueError(f"Issue action not found: {action_id}")
        return action

    def get_issue(self, issue_id: str) -> ReviewIssue:
        issue = self.session.get(ReviewIssue, issue_id)
        if issue is None:
            raise ValueError(f"Review issue not found: {issue_id}")
        return issue

    def list_unresolved_issues_for_packet(
        self,
        packet_id: str,
        *,
        exclude_issue_id: str | None = None,
    ) -> list[ReviewIssue]:
        stmt = select(ReviewIssue).where(
            ReviewIssue.packet_id == packet_id,
            ReviewIssue.status.in_([IssueStatus.OPEN, IssueStatus.TRIAGED]),
        )
        if exclude_issue_id is not None:
            stmt = stmt.where(ReviewIssue.id != exclude_issue_id)
        return self.session.scalars(stmt.order_by(ReviewIssue.id)).all()

    def get_chapter(self, chapter_id: str) -> Chapter:
        chapter = self.session.get(Chapter, chapter_id)
        if chapter is None:
            raise ValueError(f"Chapter not found: {chapter_id}")
        return chapter

    def list_packets_for_chapter(self, chapter_id: str) -> list[TranslationPacket]:
        return self.session.scalars(
            select(TranslationPacket).where(TranslationPacket.chapter_id == chapter_id).order_by(TranslationPacket.id)
        ).all()

    def get_packet(self, packet_id: str) -> TranslationPacket:
        packet = self.session.get(TranslationPacket, packet_id)
        if packet is None:
            raise ValueError(f"Packet not found: {packet_id}")
        return packet

    def mark_issue_triaged(self, issue: ReviewIssue, note: str) -> None:
        issue.status = IssueStatus.TRIAGED
        issue.resolution_note = note
        self.session.merge(issue)

    def mark_packet_ready_for_rerun(self, packet_id: str) -> TranslationPacket:
        packet = self.get_packet(packet_id)
        packet.status = PacketStatus.BUILT
        self.session.merge(packet)
        chapter = self.get_chapter(packet.chapter_id)
        chapter.status = ChapterStatus.PACKET_BUILT
        self.session.merge(chapter)
        return packet

    def list_packet_bundles_for_chapter(self, chapter_id: str) -> list[PacketInvalidationBundle]:
        packets = self.session.scalars(
            select(TranslationPacket).where(TranslationPacket.chapter_id == chapter_id)
        ).all()
        return [self._build_packet_bundle(packet.id) for packet in packets]

    def get_packet_bundle(self, packet_id: str) -> PacketInvalidationBundle:
        return self._build_packet_bundle(packet_id)

    def save_invalidations(
        self,
        action: IssueAction,
        invalidations: list[ArtifactInvalidation],
        audits: list[AuditEvent],
    ) -> None:
        self.session.merge(action)
        for invalidation in invalidations:
            self.session.merge(invalidation)
        for audit in audits:
            self.session.merge(audit)

    def save_audits(self, audits: list[AuditEvent]) -> None:
        for audit in audits:
            self.session.merge(audit)

    def replace_alignment_edges(
        self,
        target_segment_ids: list[str],
        alignment_edges: list[AlignmentEdge],
    ) -> None:
        if target_segment_ids:
            self.session.execute(
                delete(AlignmentEdge).where(AlignmentEdge.target_segment_id.in_(target_segment_ids))
            )
        for edge in alignment_edges:
            self.session.merge(edge)
        self.session.flush()

    def _build_packet_bundle(self, packet_id: str) -> PacketInvalidationBundle:
        packet = self.session.get(TranslationPacket, packet_id)
        if packet is None:
            raise ValueError(f"Packet not found: {packet_id}")

        sentence_ids = self.session.scalars(
            select(PacketSentenceMap.sentence_id).where(
                PacketSentenceMap.packet_id == packet_id,
                PacketSentenceMap.role == PacketSentenceRole.CURRENT,
            )
        ).all()
        translation_runs = self.session.scalars(
            select(TranslationRun).where(TranslationRun.packet_id == packet_id)
        ).all()
        run_ids = [run.id for run in translation_runs]
        target_segments = self.session.scalars(
            select(TargetSegment).where(TargetSegment.translation_run_id.in_(run_ids))
        ).all() if run_ids else []
        target_ids = [segment.id for segment in target_segments]
        alignment_edges = self.session.scalars(
            select(AlignmentEdge).where(AlignmentEdge.target_segment_id.in_(target_ids))
        ).all() if target_ids else []
        return PacketInvalidationBundle(
            packet=packet,
            sentence_ids=sentence_ids,
            translation_runs=translation_runs,
            target_segments=target_segments,
            alignment_edges=alignment_edges,
        )
