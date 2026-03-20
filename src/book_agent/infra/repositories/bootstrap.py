from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from book_agent.domain.enums import ArtifactStatus
from book_agent.domain.models import (
    Block,
    BookProfile,
    Chapter,
    Document,
    DocumentImage,
    JobRun,
    MemorySnapshot,
    Sentence,
)
from book_agent.domain.models.translation import PacketSentenceMap, TranslationPacket
from book_agent.services.bootstrap import BootstrapArtifacts


@dataclass(slots=True)
class PersistedChapterBundle:
    chapter: Chapter
    blocks: list[Block]
    sentences: list[Sentence]
    chapter_brief: MemorySnapshot | None
    translation_packets: list[TranslationPacket]
    packet_sentence_maps: list[PacketSentenceMap]


@dataclass(slots=True)
class PersistedDocumentBundle:
    document: Document
    chapters: list[PersistedChapterBundle]
    book_profile: BookProfile | None
    memory_snapshots: list[MemorySnapshot]
    job_runs: list[JobRun]
    document_images: list[DocumentImage]


class BootstrapRepository:
    def __init__(self, session: Session):
        self.session = session

    def _document_images_table_available(self) -> bool:
        bind = self.session.get_bind()
        if bind is None:
            return False
        return bool(inspect(bind).has_table(DocumentImage.__tablename__))

    def save(self, artifacts: BootstrapArtifacts) -> None:
        self.session.merge(artifacts.document)
        self.session.flush()

        self._merge_collection(artifacts.chapters)
        self.session.flush()

        self._merge_collection(artifacts.blocks)
        self.session.flush()

        self._merge_collection(artifacts.sentences)
        self.session.flush()

        if artifacts.book_profile is not None:
            self.session.merge(artifacts.book_profile)
            self.session.flush()

        self._merge_collection(artifacts.memory_snapshots)
        self.session.flush()

        self._merge_collection(artifacts.translation_packets)
        self.session.flush()

        self._merge_collection(artifacts.packet_sentence_maps)
        self._merge_collection(artifacts.job_runs)
        self._merge_collection(artifacts.document_images)
        self.session.flush()

    def _merge_collection(self, collection: list[object]) -> None:
        for item in collection:
            self.session.merge(item)

    def load_document_bundle(self, document_id: str) -> PersistedDocumentBundle:
        document = self.session.get(Document, document_id)
        if document is None:
            raise ValueError(f"Document not found: {document_id}")

        chapters = self.session.scalars(
            select(Chapter).where(Chapter.document_id == document_id).order_by(Chapter.ordinal)
        ).all()
        blocks = self.session.scalars(
            select(Block)
            .join(Chapter, Block.chapter_id == Chapter.id)
            .where(
                Chapter.document_id == document_id,
                Block.status == ArtifactStatus.ACTIVE,
            )
        ).all()
        sentences = self.session.scalars(
            select(Sentence).where(Sentence.document_id == document_id)
        ).all()
        memory_snapshots = self.session.scalars(
            select(MemorySnapshot).where(MemorySnapshot.document_id == document_id)
        ).all()
        document_images = (
            self.session.scalars(
                select(DocumentImage)
                .where(DocumentImage.document_id == document_id)
                .order_by(DocumentImage.page_number, DocumentImage.id)
            ).all()
            if self._document_images_table_available()
            else []
        )
        packets = self.session.scalars(
            select(TranslationPacket)
            .join(Chapter, TranslationPacket.chapter_id == Chapter.id)
            .where(Chapter.document_id == document_id)
        ).all()
        packet_maps = self.session.scalars(
            select(PacketSentenceMap)
            .join(TranslationPacket, PacketSentenceMap.packet_id == TranslationPacket.id)
            .join(Chapter, TranslationPacket.chapter_id == Chapter.id)
            .where(Chapter.document_id == document_id)
        ).all()
        job_runs = self.session.scalars(select(JobRun).where(JobRun.scope_id == document_id)).all()
        book_profile = self.session.scalars(
            select(BookProfile).where(BookProfile.document_id == document_id).order_by(BookProfile.version.desc())
        ).first()

        blocks_by_chapter: dict[str, list[Block]] = {}
        for block in blocks:
            blocks_by_chapter.setdefault(block.chapter_id, []).append(block)
        sentences_by_chapter: dict[str, list[Sentence]] = {}
        for sentence in sentences:
            sentences_by_chapter.setdefault(sentence.chapter_id, []).append(sentence)
        briefs_by_chapter = {
            snapshot.scope_id: snapshot
            for snapshot in memory_snapshots
            if snapshot.scope_id and snapshot.snapshot_type.value == "chapter_brief"
        }
        packets_by_chapter: dict[str, list[TranslationPacket]] = {}
        for packet in packets:
            packets_by_chapter.setdefault(packet.chapter_id, []).append(packet)
        packet_maps_by_packet: dict[str, list[PacketSentenceMap]] = {}
        for mapping in packet_maps:
            packet_maps_by_packet.setdefault(mapping.packet_id, []).append(mapping)

        chapter_bundles = [
            PersistedChapterBundle(
                chapter=chapter,
                blocks=sorted(blocks_by_chapter.get(chapter.id, []), key=lambda item: item.ordinal),
                sentences=sorted(
                    sentences_by_chapter.get(chapter.id, []),
                    key=lambda item: (item.block_id, item.ordinal_in_block),
                ),
                chapter_brief=briefs_by_chapter.get(chapter.id),
                translation_packets=packets_by_chapter.get(chapter.id, []),
                packet_sentence_maps=[
                    mapping
                    for packet in packets_by_chapter.get(chapter.id, [])
                    for mapping in packet_maps_by_packet.get(packet.id, [])
                ],
            )
            for chapter in chapters
        ]
        return PersistedDocumentBundle(
            document=document,
            chapters=chapter_bundles,
            book_profile=book_profile,
            memory_snapshots=memory_snapshots,
            job_runs=job_runs,
            document_images=document_images,
        )
