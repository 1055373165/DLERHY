from __future__ import annotations

from dataclasses import dataclass

from book_agent.domain.models import MemorySnapshot
from book_agent.workers.contracts import ContextPacket, TranslatedContextBlock

MAX_CHAPTER_MEMORY_TRANSLATIONS = 4


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


@dataclass(slots=True)
class ChapterContextCompiler:
    compile_version: str = "v1.chapter-memory"

    def compile(
        self,
        packet: ContextPacket,
        *,
        chapter_memory_snapshot: MemorySnapshot | None,
    ) -> ContextPacket:
        memory_blocks = _memory_blocks(chapter_memory_snapshot)
        merged_previous = _dedupe_translated_blocks([*memory_blocks, *packet.prev_translated_blocks])
        compiled_brief = packet.chapter_brief
        if chapter_memory_snapshot is not None:
            compiled_brief = (
                chapter_memory_snapshot.content_json.get("chapter_brief") or packet.chapter_brief
            )
        return packet.model_copy(
            update={
                "chapter_brief": compiled_brief,
                "prev_translated_blocks": merged_previous,
            }
        )
