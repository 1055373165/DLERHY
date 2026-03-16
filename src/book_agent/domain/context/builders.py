from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from book_agent.core.ids import stable_id
from book_agent.domain.block_rules import block_is_context_translatable
from book_agent.domain.enums import (
    BookType,
    JobScopeType,
    JobStatus,
    JobType,
    MemoryScopeType,
    MemoryStatus,
    PacketSentenceRole,
    PacketStatus,
    PacketType,
    SnapshotType,
)
from book_agent.domain.models import (
    Block,
    BookProfile,
    Chapter,
    Document,
    JobRun,
    MemorySnapshot,
    PacketSentenceMap,
    Sentence,
    TranslationPacket,
)
from book_agent.workers.contracts import ContextPacket, PacketBlock


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


MAX_REFERENCE_PACKET_SENTENCES = 24
MAX_GENERAL_PACKET_SENTENCES = 32


def _group_by(items: Iterable, key):
    grouped: dict[str, list] = {}
    for item in items:
        grouped.setdefault(key(item), []).append(item)
    return grouped


@dataclass(slots=True)
class BookProfileBuildResult:
    book_profile: BookProfile
    termbase_snapshot: MemorySnapshot
    entity_snapshot: MemorySnapshot
    job_run: JobRun
    seed_jobs: list[JobRun]


@dataclass(slots=True)
class ContextPacketBuildResult:
    translation_packets: list[TranslationPacket]
    packet_sentence_maps: list[PacketSentenceMap]
    job_runs: list[JobRun]


class BookProfileBuilder:
    def build(self, document: Document, chapters: list[Chapter], blocks: list[Block]) -> BookProfileBuildResult:
        now = _utcnow()
        book_type = self._infer_book_type(document, chapters, blocks)
        book_profile = BookProfile(
            id=stable_id("book-profile", document.id, 1),
            document_id=document.id,
            version=1,
            book_type=book_type,
            style_policy_json={
                "tone": "faithful-clear",
                "sentence_preference": "natural_cn",
                "preserve_structure": True,
            },
            quote_policy_json={"preserve_speaker_attribution": True},
            special_content_policy_json={
                "code": "protect",
                "table": "protect",
                "footnote": "translate",
            },
            created_by="system",
            created_at=now,
        )

        termbase_snapshot = MemorySnapshot(
            id=stable_id("snapshot", document.id, SnapshotType.TERMBASE.value, MemoryScopeType.GLOBAL.value, 1),
            document_id=document.id,
            scope_type=MemoryScopeType.GLOBAL,
            scope_id=None,
            snapshot_type=SnapshotType.TERMBASE,
            version=1,
            content_json={"terms": []},
            status=MemoryStatus.ACTIVE,
            created_at=now,
        )
        entity_snapshot = MemorySnapshot(
            id=stable_id(
                "snapshot",
                document.id,
                SnapshotType.ENTITY_REGISTRY.value,
                MemoryScopeType.GLOBAL.value,
                1,
            ),
            document_id=document.id,
            scope_type=MemoryScopeType.GLOBAL,
            scope_id=None,
            snapshot_type=SnapshotType.ENTITY_REGISTRY,
            version=1,
            content_json={"entities": []},
            status=MemoryStatus.ACTIVE,
            created_at=now,
        )

        profile_job = JobRun(
            id=stable_id("job", JobType.PROFILE.value, document.id, 1),
            job_type=JobType.PROFILE,
            scope_type=JobScopeType.DOCUMENT,
            scope_id=document.id,
            status=JobStatus.SUCCEEDED,
            started_at=now,
            ended_at=now,
            created_at=now,
        )
        seed_jobs = [
            JobRun(
                id=stable_id("job", JobType.PROFILE.value, document.id, snapshot.snapshot_type.value, 1),
                job_type=JobType.PROFILE,
                scope_type=JobScopeType.DOCUMENT,
                scope_id=document.id,
                status=JobStatus.SUCCEEDED,
                started_at=now,
                ended_at=now,
                created_at=now,
            )
            for snapshot in (termbase_snapshot, entity_snapshot)
        ]
        return BookProfileBuildResult(
            book_profile=book_profile,
            termbase_snapshot=termbase_snapshot,
            entity_snapshot=entity_snapshot,
            job_run=profile_job,
            seed_jobs=seed_jobs,
        )

    def _infer_book_type(self, document: Document, chapters: list[Chapter], blocks: list[Block]) -> BookType:
        haystack = " ".join(filter(None, [document.title or "", *(chapter.title_src or "" for chapter in chapters)]))
        lowered = haystack.lower()
        if any(keyword in lowered for keyword in ["strategy", "management", "business", "market"]):
            return BookType.BUSINESS
        if any(keyword in lowered for keyword in ["system", "engineering", "data", "code", "software"]):
            return BookType.TECH
        return BookType.NONFICTION


class ChapterBriefBuilder:
    def build_many(
        self,
        document: Document,
        chapters: list[Chapter],
        blocks: list[Block],
        sentences: list[Sentence],
        *,
        version: int = 1,
    ) -> list[MemorySnapshot]:
        blocks_by_chapter = _group_by(blocks, lambda block: block.chapter_id)
        sentences_by_chapter = _group_by(sentences, lambda sentence: sentence.chapter_id)
        return [
            self.build(
                document,
                chapter,
                blocks_by_chapter.get(chapter.id, []),
                sentences_by_chapter.get(chapter.id, []),
                version=version,
            )
            for chapter in chapters
        ]

    def build(
        self,
        document: Document,
        chapter: Chapter,
        blocks: list[Block],
        sentences: list[Sentence],
        *,
        version: int = 1,
    ) -> MemorySnapshot:
        now = _utcnow()
        summary_sentences = [
            sentence.normalized_text or sentence.source_text
            for sentence in sentences
            if sentence.translatable
        ][:3]
        summary = " ".join(summary_sentences)
        open_questions = []
        if chapter.title_src is None:
            open_questions.append("missing_chapter_title")

        return MemorySnapshot(
            id=stable_id(
                "snapshot",
                document.id,
                SnapshotType.CHAPTER_BRIEF.value,
                chapter.id,
                version,
            ),
            document_id=document.id,
            scope_type=MemoryScopeType.CHAPTER,
            scope_id=chapter.id,
            snapshot_type=SnapshotType.CHAPTER_BRIEF,
            version=version,
            content_json={
                "chapter_title": chapter.title_src,
                "heading_path": [chapter.title_src] if chapter.title_src else [],
                "summary": summary,
                "open_questions": open_questions,
                "block_count": len(blocks),
                "sentence_count": len(sentences),
            },
            status=MemoryStatus.ACTIVE,
            created_at=now,
        )


class ContextPacketBuilder:
    def build_many(
        self,
        document: Document,
        chapters: list[Chapter],
        blocks: list[Block],
        sentences: list[Sentence],
        book_profile: BookProfile,
        chapter_briefs: list[MemorySnapshot],
        termbase_snapshot: MemorySnapshot,
        entity_snapshot: MemorySnapshot,
    ) -> ContextPacketBuildResult:
        blocks_by_chapter = _group_by(blocks, lambda block: block.chapter_id)
        sentences_by_block = _group_by(sentences, lambda sentence: sentence.block_id)
        briefs_by_scope = {brief.scope_id: brief for brief in chapter_briefs}

        packets: list[TranslationPacket] = []
        maps: list[PacketSentenceMap] = []
        jobs: list[JobRun] = []
        now = _utcnow()

        for chapter in chapters:
            chapter_blocks = sorted(blocks_by_chapter.get(chapter.id, []), key=lambda block: block.ordinal)
            translatable_blocks = [block for block in chapter_blocks if block_is_context_translatable(block)]
            if not translatable_blocks:
                continue
            chapter_brief = briefs_by_scope[chapter.id]
            for index, block in enumerate(translatable_blocks):
                prev_blocks = translatable_blocks[max(0, index - 2):index]
                next_blocks = translatable_blocks[index + 1:index + 3]
                current_sentences = sentences_by_block.get(block.id, [])
                for chunk_index, sentence_window in enumerate(
                    self._packet_sentence_windows(chapter, current_sentences),
                    start=1,
                ):
                    packet_id = None
                    current_block_text = None
                    if len(sentence_window) != len(current_sentences):
                        packet_id = stable_id(
                            "packet",
                            document.id,
                            chapter.id,
                            block.id,
                            sentence_window[0].id,
                            sentence_window[-1].id,
                            chapter_brief.version,
                            termbase_snapshot.version,
                            entity_snapshot.version,
                            chunk_index,
                        )
                        current_block_text = " ".join(
                            sentence.normalized_text or sentence.source_text
                            for sentence in sentence_window
                        )
                    packet, packet_maps = self._build_packet(
                        document=document,
                        chapter=chapter,
                        block=block,
                        prev_blocks=prev_blocks,
                        next_blocks=next_blocks,
                        sentences_by_block=sentences_by_block,
                        book_profile=book_profile,
                        chapter_brief=chapter_brief,
                        termbase_snapshot=termbase_snapshot,
                        entity_snapshot=entity_snapshot,
                        now=now,
                        packet_id=packet_id,
                        current_sentences=sentence_window,
                        current_block_text=current_block_text,
                    )
                    packets.append(packet)
                    maps.extend(packet_maps)

            jobs.append(
                JobRun(
                    id=stable_id("job", JobType.PACKET.value, document.id, chapter.id, 1),
                    job_type=JobType.PACKET,
                    scope_type=JobScopeType.CHAPTER,
                    scope_id=chapter.id,
                    status=JobStatus.SUCCEEDED,
                    started_at=now,
                    ended_at=now,
                    created_at=now,
                )
            )

        return ContextPacketBuildResult(
            translation_packets=packets,
            packet_sentence_maps=maps,
            job_runs=jobs,
        )

    def build_packet(
        self,
        *,
        document: Document,
        chapter: Chapter,
        block: Block,
        prev_blocks: list[Block],
        next_blocks: list[Block],
        sentences_by_block: dict[str, list[Sentence]],
        book_profile: BookProfile,
        chapter_brief: MemorySnapshot,
        termbase_snapshot: MemorySnapshot,
        entity_snapshot: MemorySnapshot,
        packet_id: str,
        packet_type: PacketType = PacketType.RETRANSLATE,
        created_at: datetime | None = None,
    ) -> tuple[TranslationPacket, list[PacketSentenceMap]]:
        now = _utcnow()
        return self._build_packet(
            document=document,
            chapter=chapter,
            block=block,
            prev_blocks=prev_blocks,
            next_blocks=next_blocks,
            sentences_by_block=sentences_by_block,
            book_profile=book_profile,
            chapter_brief=chapter_brief,
            termbase_snapshot=termbase_snapshot,
            entity_snapshot=entity_snapshot,
            now=now,
            packet_id=packet_id,
            packet_type=packet_type,
            created_at=created_at or now,
        )

    def _build_packet(
        self,
        document: Document,
        chapter: Chapter,
        block: Block,
        prev_blocks: list[Block],
        next_blocks: list[Block],
        sentences_by_block: dict[str, list[Sentence]],
        book_profile: BookProfile,
        chapter_brief: MemorySnapshot,
        termbase_snapshot: MemorySnapshot,
        entity_snapshot: MemorySnapshot,
        now: datetime,
        packet_id: str | None = None,
        packet_type: PacketType = PacketType.TRANSLATE,
        created_at: datetime | None = None,
        current_sentences: list[Sentence] | None = None,
        current_block_text: str | None = None,
    ) -> tuple[TranslationPacket, list[PacketSentenceMap]]:
        current_sentences = current_sentences or sentences_by_block.get(block.id, [])
        current_block_text = current_block_text or block.source_text
        packet_id = packet_id or stable_id(
            "packet",
            document.id,
            chapter.id,
            block.id,
            chapter_brief.version,
            termbase_snapshot.version,
            entity_snapshot.version,
        )
        context_text = " ".join(
            filter(
                None,
                [
                    *(item.source_text for item in prev_blocks),
                    current_block_text,
                    *(item.source_text for item in next_blocks),
                ],
            )
        )
        context_packet = ContextPacket(
            packet_id=packet_id,
            document_id=document.id,
            chapter_id=chapter.id,
            packet_type=packet_type.value,
            book_profile_version=book_profile.version,
            chapter_brief_version=chapter_brief.version,
            heading_path=chapter_brief.content_json.get("heading_path", []),
            current_blocks=[self._to_packet_block(block, current_sentences, text_override=current_block_text)],
            prev_blocks=[self._to_packet_block(item, sentences_by_block.get(item.id, [])) for item in prev_blocks],
            next_blocks=[self._to_packet_block(item, sentences_by_block.get(item.id, [])) for item in next_blocks],
            relevant_terms=self._match_terms(context_text, termbase_snapshot),
            relevant_entities=self._match_entities(context_text, entity_snapshot),
            protected_spans=[],
            chapter_brief=chapter_brief.content_json.get("summary"),
            style_constraints=book_profile.style_policy_json,
            open_questions=chapter_brief.content_json.get("open_questions", []),
            budget_hint={"max_input_tokens": 6000, "max_output_tokens": 2500},
        )
        packet = TranslationPacket(
            id=packet_id,
            chapter_id=chapter.id,
            block_start_id=block.id,
            block_end_id=block.id,
            packet_type=packet_type,
            book_profile_version=book_profile.version,
            chapter_brief_version=chapter_brief.version,
            termbase_version=termbase_snapshot.version,
            entity_snapshot_version=entity_snapshot.version,
            style_snapshot_version=book_profile.version,
            packet_json=context_packet.model_dump(mode="json"),
            risk_score=0.1,
            status=PacketStatus.BUILT,
            created_at=created_at or now,
            updated_at=now,
        )
        packet_maps = []
        for source_sentence in current_sentences:
            packet_maps.append(
                PacketSentenceMap(
                    packet_id=packet.id,
                    sentence_id=source_sentence.id,
                    role=PacketSentenceRole.CURRENT,
                )
            )
        for source_block in prev_blocks:
            for source_sentence in sentences_by_block.get(source_block.id, []):
                packet_maps.append(
                    PacketSentenceMap(
                        packet_id=packet.id,
                        sentence_id=source_sentence.id,
                        role=PacketSentenceRole.PREV_CONTEXT,
                    )
                )
        for source_block in next_blocks:
            for source_sentence in sentences_by_block.get(source_block.id, []):
                packet_maps.append(
                    PacketSentenceMap(
                        packet_id=packet.id,
                        sentence_id=source_sentence.id,
                        role=PacketSentenceRole.NEXT_CONTEXT,
                    )
        )
        return packet, packet_maps

    def _to_packet_block(
        self,
        block: Block,
        sentences: list[Sentence],
        *,
        text_override: str | None = None,
    ) -> PacketBlock:
        return PacketBlock(
            block_id=block.id,
            block_type=block.block_type.value,
            sentence_ids=[sentence.id for sentence in sentences],
            text=text_override or block.normalized_text or block.source_text,
        )

    def _packet_sentence_windows(self, chapter: Chapter, sentences: list[Sentence]) -> list[list[Sentence]]:
        max_sentences = self._max_packet_sentences(chapter, sentences)
        if max_sentences is None:
            return [sentences]
        return [
            sentences[index : index + max_sentences]
            for index in range(0, len(sentences), max_sentences)
        ]

    def _max_packet_sentences(self, chapter: Chapter, sentences: list[Sentence]) -> int | None:
        section_family = str(chapter.metadata_json.get("pdf_section_family") or "").strip().casefold()
        if section_family == "references":
            return MAX_REFERENCE_PACKET_SENTENCES if len(sentences) > MAX_REFERENCE_PACKET_SENTENCES else None

        title = " ".join((chapter.title_src or "").split()).casefold()
        if title in {"references", "bibliography", "works cited"}:
            return MAX_REFERENCE_PACKET_SENTENCES if len(sentences) > MAX_REFERENCE_PACKET_SENTENCES else None

        if len(sentences) > MAX_GENERAL_PACKET_SENTENCES:
            return MAX_GENERAL_PACKET_SENTENCES
        return None

    def _match_terms(self, text: str, termbase_snapshot: MemorySnapshot) -> list[dict[str, str]]:
        lowered = text.lower()
        matched = []
        seen: set[str] = set()
        for term in termbase_snapshot.content_json.get("terms", []):
            source_term = term.get("source_term", "").lower()
            if source_term and source_term in lowered and source_term not in seen:
                matched.append(term)
                seen.add(source_term)
        return matched

    def _match_entities(self, text: str, entity_snapshot: MemorySnapshot) -> list[dict[str, str]]:
        lowered = text.lower()
        matched = []
        seen: set[str] = set()
        for entity in entity_snapshot.content_json.get("entities", []):
            name = entity.get("name", "").lower()
            aliases = [alias.lower() for alias in entity.get("aliases", [])]
            if name and (name in lowered or any(alias in lowered for alias in aliases)) and name not in seen:
                matched.append(entity)
                seen.add(name)
        return matched
