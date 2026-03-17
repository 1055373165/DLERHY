from __future__ import annotations

from dataclasses import dataclass

from book_agent.domain.models import MemorySnapshot
from book_agent.workers.contracts import ConceptCandidate, ContextPacket, TranslatedContextBlock

MAX_CHAPTER_MEMORY_TRANSLATIONS = 4


@dataclass(frozen=True, slots=True)
class ChapterContextCompileOptions:
    include_memory_blocks: bool = True
    include_chapter_concepts: bool = True
    prefer_memory_chapter_brief: bool = True


def _dedupe_translated_blocks(blocks: list[TranslatedContextBlock]) -> list[TranslatedContextBlock]:
    deduped: list[TranslatedContextBlock] = []
    seen: set[tuple[str, str]] = set()
    for block in blocks:
        key = (block.block_id, block.target_excerpt)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(block)
    return deduped


def _memory_blocks(snapshot: MemorySnapshot | None) -> list[TranslatedContextBlock]:
    if snapshot is None:
        return []
    items = snapshot.content_json.get("recent_accepted_translations", [])
    if not isinstance(items, list):
        return []
    blocks: list[TranslatedContextBlock] = []
    for item in items[-MAX_CHAPTER_MEMORY_TRANSLATIONS:]:
        if not isinstance(item, dict):
            continue
        source_excerpt = str(item.get("source_excerpt") or "").strip()
        target_excerpt = str(item.get("target_excerpt") or "").strip()
        block_id = str(item.get("block_id") or "").strip()
        if not source_excerpt or not target_excerpt or not block_id:
            continue
        blocks.append(
            TranslatedContextBlock(
                block_id=block_id,
                source_excerpt=source_excerpt,
                target_excerpt=target_excerpt,
                source_sentence_ids=list(item.get("source_sentence_ids") or []),
            )
        )
    return blocks


def _memory_concepts(snapshot: MemorySnapshot | None) -> list[ConceptCandidate]:
    if snapshot is None:
        return []
    items = snapshot.content_json.get("active_concepts", [])
    if not isinstance(items, list):
        return []
    concepts: list[ConceptCandidate] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        source_term = str(item.get("source_term") or "").strip()
        if not source_term:
            continue
        concepts.append(
            ConceptCandidate(
                source_term=source_term,
                canonical_zh=str(item.get("canonical_zh")) if item.get("canonical_zh") else None,
                status=str(item.get("status") or "candidate"),
                confidence=float(item.get("confidence")) if item.get("confidence") is not None else None,
                first_seen_packet_id=str(item.get("first_seen_packet_id")) if item.get("first_seen_packet_id") else None,
                last_seen_packet_id=str(item.get("last_seen_packet_id")) if item.get("last_seen_packet_id") else None,
                times_seen=int(item.get("times_seen") or 1),
            )
        )
    return concepts


@dataclass(slots=True)
class ChapterContextCompiler:
    compile_version: str = "v1.chapter-memory"

    def compile(
        self,
        packet: ContextPacket,
        *,
        chapter_memory_snapshot: MemorySnapshot | None,
        options: ChapterContextCompileOptions | None = None,
    ) -> ContextPacket:
        compile_options = options or ChapterContextCompileOptions()
        memory_blocks = _memory_blocks(chapter_memory_snapshot) if compile_options.include_memory_blocks else []
        memory_concepts = _memory_concepts(chapter_memory_snapshot) if compile_options.include_chapter_concepts else []
        merged_previous = _dedupe_translated_blocks([*memory_blocks, *packet.prev_translated_blocks])
        compiled_brief = packet.chapter_brief
        if chapter_memory_snapshot is not None and compile_options.prefer_memory_chapter_brief:
            compiled_brief = (
                chapter_memory_snapshot.content_json.get("chapter_brief") or packet.chapter_brief
            )
        return packet.model_copy(
            update={
                "chapter_brief": compiled_brief,
                "chapter_concepts": memory_concepts,
                "prev_translated_blocks": merged_previous,
            }
        )
