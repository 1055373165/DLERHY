from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from book_agent.domain.enums import ChapterStatus
from book_agent.domain.models import Chapter, Sentence
from book_agent.domain.models.translation import (
    AlignmentEdge,
    PacketSentenceMap,
    TargetSegment,
    TranslationPacket,
    TranslationRun,
)
from book_agent.workers.contracts import ContextPacket


@dataclass(slots=True)
class TranslationPacketBundle:
    packet: TranslationPacket
    context_packet: ContextPacket
    current_sentences: list[Sentence]
    all_packet_sentences: list[Sentence]


class TranslationRepository:
    def __init__(self, session: Session):
        self.session = session

    def load_packet_bundle(self, packet_id: str) -> TranslationPacketBundle:
        packet = self.session.get(TranslationPacket, packet_id)
        if packet is None:
            raise ValueError(f"Translation packet not found: {packet_id}")

        mappings = self.session.scalars(
            select(PacketSentenceMap).where(PacketSentenceMap.packet_id == packet_id)
        ).all()
        sentence_ids = [mapping.sentence_id for mapping in mappings]
        sentences = self.session.scalars(
            select(Sentence).where(Sentence.id.in_(sentence_ids))
        ).all() if sentence_ids else []
        sentence_map = {sentence.id: sentence for sentence in sentences}

        current_sentences = [
            sentence_map[mapping.sentence_id]
            for mapping in mappings
            if mapping.role.value == "current" and mapping.sentence_id in sentence_map
        ]
        return TranslationPacketBundle(
            packet=packet,
            context_packet=ContextPacket.model_validate(packet.packet_json),
            current_sentences=current_sentences,
            all_packet_sentences=[sentence_map[mapping.sentence_id] for mapping in mappings if mapping.sentence_id in sentence_map],
        )

    def next_attempt(self, packet_id: str) -> int:
        attempts = self.session.scalar(
            select(func.max(TranslationRun.attempt)).where(TranslationRun.packet_id == packet_id)
        )
        return (attempts or 0) + 1

    def save_translation_artifacts(
        self,
        translation_run: TranslationRun,
        target_segments: list[TargetSegment],
        alignment_edges: list[AlignmentEdge],
        updated_sentences: list[Sentence],
        packet: TranslationPacket,
    ) -> None:
        self.session.add(translation_run)
        self.session.flush()

        self.session.add_all(target_segments)
        self.session.flush()

        self.session.add_all(alignment_edges)
        self.session.flush()

        self._merge_collection(updated_sentences)
        self.session.flush()

        chapter = self.session.get(Chapter, packet.chapter_id)
        if chapter is not None:
            all_packets = self.session.scalars(
                select(TranslationPacket).where(TranslationPacket.chapter_id == packet.chapter_id)
            ).all()
            if all_packets and all(item.status.value == "translated" for item in all_packets):
                chapter.status = ChapterStatus.TRANSLATED
                self.session.merge(chapter)

    def _merge_collection(self, collection: list[object]) -> None:
        for item in collection:
            self.session.merge(item)
