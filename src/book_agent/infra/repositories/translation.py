from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from book_agent.domain.enums import ChapterStatus, PacketSentenceRole, TargetSegmentStatus
from book_agent.domain.models import Block, Chapter, Sentence
from book_agent.domain.models.translation import (
    AlignmentEdge,
    PacketSentenceMap,
    TargetSegment,
    TranslationPacket,
    TranslationRun,
)
from book_agent.workers.contracts import ContextPacket, TranslatedContextBlock


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
            select(PacketSentenceMap)
            .join(Sentence, PacketSentenceMap.sentence_id == Sentence.id)
            .join(Block, Sentence.block_id == Block.id)
            .where(PacketSentenceMap.packet_id == packet_id)
            .order_by(Block.ordinal.asc(), Sentence.ordinal_in_block.asc())
        ).all()
        sentence_ids = [mapping.sentence_id for mapping in mappings]
        sentences = self.session.scalars(
            select(Sentence).where(Sentence.id.in_(sentence_ids))
        ).all() if sentence_ids else []
        sentence_map = {sentence.id: sentence for sentence in sentences}

        current_sentences = [
            sentence_map[mapping.sentence_id]
            for mapping in mappings
            if mapping.role == PacketSentenceRole.CURRENT and mapping.sentence_id in sentence_map
        ]
        prev_context_sentence_ids = [
            mapping.sentence_id
            for mapping in mappings
            if mapping.role == PacketSentenceRole.PREV_CONTEXT and mapping.sentence_id in sentence_map
        ]
        context_packet = ContextPacket.model_validate(packet.packet_json)
        prev_translated_blocks = self._build_prev_translated_blocks(prev_context_sentence_ids, sentence_map)
        return TranslationPacketBundle(
            packet=packet,
            context_packet=context_packet.model_copy(
                update={"prev_translated_blocks": prev_translated_blocks}
            ),
            current_sentences=current_sentences,
            all_packet_sentences=[sentence_map[mapping.sentence_id] for mapping in mappings if mapping.sentence_id in sentence_map],
        )

    def _build_prev_translated_blocks(
        self,
        sentence_ids: list[str],
        sentence_map: dict[str, Sentence],
    ) -> list[TranslatedContextBlock]:
        if not sentence_ids:
            return []

        rows = self.session.execute(
            select(
                AlignmentEdge.sentence_id,
                TargetSegment.text_zh,
                TargetSegment.ordinal,
            )
            .join(TargetSegment, TargetSegment.id == AlignmentEdge.target_segment_id)
            .where(
                AlignmentEdge.sentence_id.in_(sentence_ids),
                TargetSegment.final_status != TargetSegmentStatus.SUPERSEDED,
            )
            .order_by(TargetSegment.ordinal.asc())
        ).all()

        translated_by_sentence: dict[str, list[str]] = {}
        for sentence_id, text_zh, _ordinal in rows:
            if not text_zh:
                continue
            translated_by_sentence.setdefault(sentence_id, []).append(str(text_zh))

        blocks: dict[str, dict[str, object]] = {}
        for sentence_id in sentence_ids:
            sentence = sentence_map.get(sentence_id)
            if sentence is None:
                continue
            target_chunks = translated_by_sentence.get(sentence_id, [])
            if not target_chunks:
                continue
            block_bucket = blocks.setdefault(
                sentence.block_id,
                {
                    "source_excerpt_parts": [],
                    "target_excerpt_parts": [],
                    "source_sentence_ids": [],
                },
            )
            source_excerpt = (sentence.normalized_text or sentence.source_text or "").strip()
            if source_excerpt:
                block_bucket["source_excerpt_parts"].append(source_excerpt)
            block_bucket["target_excerpt_parts"].append(" ".join(target_chunks).strip())
            block_bucket["source_sentence_ids"].append(sentence.id)

        translated_blocks: list[TranslatedContextBlock] = []
        for block_id, bucket in blocks.items():
            source_excerpt = " ".join(bucket["source_excerpt_parts"]).strip()
            target_excerpt = " ".join(bucket["target_excerpt_parts"]).strip()
            if not source_excerpt or not target_excerpt:
                continue
            translated_blocks.append(
                TranslatedContextBlock(
                    block_id=block_id,
                    source_excerpt=source_excerpt,
                    target_excerpt=target_excerpt,
                    source_sentence_ids=list(bucket["source_sentence_ids"]),
                )
            )
        return translated_blocks

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
