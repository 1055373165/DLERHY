from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from book_agent.domain.enums import MemoryScopeType, MemoryStatus, PacketStatus, RunStatus, SnapshotType, TargetSegmentStatus
from book_agent.domain.models import Block, Chapter, MemorySnapshot
from book_agent.domain.models.translation import TargetSegment, TranslationPacket, TranslationRun
from book_agent.infra.repositories.chapter_memory import ChapterTranslationMemoryRepository
from book_agent.infra.repositories.translation import TranslationRepository
from book_agent.services.context_compile import ChapterContextCompiler
from book_agent.services.translation import TranslationExecutionArtifacts, TranslationService


@dataclass(slots=True)
class ChapterMemoryBackfillArtifacts:
    payload: dict[str, object]


class ChapterMemoryBackfillService:
    def __init__(
        self,
        repository: TranslationRepository,
        *,
        chapter_memory_repository: ChapterTranslationMemoryRepository | None = None,
        context_compiler: ChapterContextCompiler | None = None,
    ) -> None:
        self.repository = repository
        self.chapter_memory_repository = chapter_memory_repository or ChapterTranslationMemoryRepository(
            repository.session
        )
        self.context_compiler = context_compiler or ChapterContextCompiler()
        self.translation_service = TranslationService(
            repository,
            chapter_memory_repository=self.chapter_memory_repository,
            context_compiler=self.context_compiler,
        )

    def backfill_chapter(self, chapter_id: str) -> ChapterMemoryBackfillArtifacts:
        return self.backfill_chapter_with_options(chapter_id, reset_existing=False)

    def backfill_chapter_with_options(
        self,
        chapter_id: str,
        *,
        reset_existing: bool,
    ) -> ChapterMemoryBackfillArtifacts:
        chapter = self.repository.session.get(Chapter, chapter_id)
        if chapter is None:
            raise ValueError(f"Chapter not found: {chapter_id}")

        current_snapshot = self.chapter_memory_repository.load_latest(
            document_id=chapter.document_id,
            chapter_id=chapter_id,
        )
        seeded = False
        if reset_existing or current_snapshot is None:
            current_snapshot = self._seed_initial_snapshot(chapter, current_snapshot=current_snapshot)
            seeded = True

        translated_packets = self._list_translated_packets(chapter_id)
        replay_packets = self._packets_after_checkpoint(translated_packets, current_snapshot.content_json.get("last_packet_id"))
        replayed_packet_ids: list[str] = []

        for packet in replay_packets:
            latest_run = self._latest_successful_run(packet.id)
            if latest_run is None:
                continue
            target_segments = self._active_target_segments(latest_run.id)
            if not target_segments:
                continue

            bundle = self.repository.load_packet_bundle(packet.id)
            current_snapshot = self.chapter_memory_repository.load_latest(
                document_id=chapter.document_id,
                chapter_id=chapter_id,
            )
            compiled_context_packet = self.context_compiler.compile(
                bundle.context_packet,
                chapter_memory_snapshot=current_snapshot,
            )
            artifacts = TranslationExecutionArtifacts(
                translation_run=latest_run,
                target_segments=target_segments,
                alignment_edges=[],
                updated_sentences=[],
            )
            self.translation_service.write_chapter_memory(
                bundle=bundle,
                artifacts=artifacts,
                current_snapshot=current_snapshot,
                compiled_context_packet=compiled_context_packet,
            )
            self.repository.session.flush()
            replayed_packet_ids.append(packet.id)

        latest_snapshot = self.chapter_memory_repository.load_latest(
            document_id=chapter.document_id,
            chapter_id=chapter_id,
        )
        return ChapterMemoryBackfillArtifacts(
            payload={
                "chapter_id": chapter_id,
                "document_id": chapter.document_id,
                "reset_existing": reset_existing,
                "seeded_initial_snapshot": seeded,
                "translated_packet_count": len(translated_packets),
                "replayed_packet_count": len(replayed_packet_ids),
                "replayed_packet_ids": replayed_packet_ids,
                "latest_snapshot_version": latest_snapshot.version if latest_snapshot is not None else None,
                "latest_last_packet_id": latest_snapshot.content_json.get("last_packet_id") if latest_snapshot is not None else None,
                "latest_recent_translation_count": len(latest_snapshot.content_json.get("recent_accepted_translations", []))
                if latest_snapshot is not None
                else 0,
                "latest_concept_count": len(latest_snapshot.content_json.get("active_concepts", []))
                if latest_snapshot is not None
                else 0,
            }
        )

    def _seed_initial_snapshot(
        self,
        chapter: Chapter,
        *,
        current_snapshot: MemorySnapshot | None,
    ) -> MemorySnapshot:
        chapter_brief = self.repository.session.scalars(
            select(MemorySnapshot)
            .where(
                MemorySnapshot.document_id == chapter.document_id,
                MemorySnapshot.scope_type == MemoryScopeType.CHAPTER,
                MemorySnapshot.scope_id == chapter.id,
                MemorySnapshot.snapshot_type == SnapshotType.CHAPTER_BRIEF,
                MemorySnapshot.status == MemoryStatus.ACTIVE,
            )
            .order_by(MemorySnapshot.version.desc())
        ).first()
        brief_content = chapter_brief.content_json if chapter_brief is not None else {}
        seeded_snapshot = self.chapter_memory_repository.supersede_and_create_next(
            current_snapshot=current_snapshot,
            document_id=chapter.document_id,
            chapter_id=chapter.id,
            content_json={
                "schema_version": 1,
                "chapter_id": chapter.id,
                "chapter_title": chapter.title_src,
                "heading_path": brief_content.get("heading_path", [chapter.title_src] if chapter.title_src else []),
                "chapter_brief": brief_content.get("summary"),
                "chapter_brief_version": chapter_brief.version if chapter_brief is not None else None,
                "active_concepts": [],
                "recent_accepted_translations": [],
                "last_packet_id": None,
                "last_translation_run_id": None,
            },
        )
        self.repository.session.flush()
        return seeded_snapshot

    def _list_translated_packets(self, chapter_id: str) -> list[TranslationPacket]:
        return self.repository.session.scalars(
            select(TranslationPacket)
            .outerjoin(Block, Block.id == TranslationPacket.block_start_id)
            .where(
                TranslationPacket.chapter_id == chapter_id,
                TranslationPacket.status == PacketStatus.TRANSLATED,
            )
            .order_by(Block.ordinal.asc().nullslast(), TranslationPacket.created_at.asc(), TranslationPacket.id.asc())
        ).all()

    def _packets_after_checkpoint(
        self,
        packets: list[TranslationPacket],
        last_packet_id: str | None,
    ) -> list[TranslationPacket]:
        if not last_packet_id:
            return packets
        seen_checkpoint = False
        remaining: list[TranslationPacket] = []
        for packet in packets:
            if seen_checkpoint:
                remaining.append(packet)
                continue
            if packet.id == last_packet_id:
                seen_checkpoint = True
        return remaining if seen_checkpoint else packets

    def _latest_successful_run(self, packet_id: str) -> TranslationRun | None:
        return self.repository.session.scalars(
            select(TranslationRun)
            .where(
                TranslationRun.packet_id == packet_id,
                TranslationRun.status == RunStatus.SUCCEEDED,
            )
            .order_by(TranslationRun.attempt.desc(), TranslationRun.created_at.desc())
        ).first()

    def _active_target_segments(self, translation_run_id: str) -> list[TargetSegment]:
        return self.repository.session.scalars(
            select(TargetSegment)
            .where(
                TargetSegment.translation_run_id == translation_run_id,
                TargetSegment.final_status != TargetSegmentStatus.SUPERSEDED,
            )
            .order_by(TargetSegment.ordinal.asc())
        ).all()
