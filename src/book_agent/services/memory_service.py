from __future__ import annotations

from dataclasses import dataclass

from book_agent.domain.models import MemorySnapshot
from book_agent.infra.repositories.chapter_memory import ChapterTranslationMemoryRepository
from book_agent.services.context_compile import ChapterContextCompileOptions, ChapterContextCompiler
from book_agent.workers.contracts import CompiledTranslationContext, ContextPacket


@dataclass(slots=True)
class CompiledContextLoadResult:
    context: CompiledTranslationContext
    chapter_memory_snapshot: MemorySnapshot | None


@dataclass(slots=True)
class MemoryService:
    chapter_memory_repository: ChapterTranslationMemoryRepository
    context_compiler: ChapterContextCompiler

    def load_latest_chapter_memory(self, *, document_id: str, chapter_id: str) -> MemorySnapshot | None:
        return self.chapter_memory_repository.load_latest(
            document_id=document_id,
            chapter_id=chapter_id,
        )

    def load_compiled_context(
        self,
        *,
        packet: ContextPacket,
        options: ChapterContextCompileOptions | None = None,
        rerun_hints: tuple[str, ...] = (),
    ) -> CompiledContextLoadResult:
        chapter_memory_snapshot = self.load_latest_chapter_memory(
            document_id=packet.document_id,
            chapter_id=packet.chapter_id,
        )
        compiled_packet = self.context_compiler.compile(
            packet,
            chapter_memory_snapshot=chapter_memory_snapshot,
            options=options,
        )
        merged_open_questions = list(compiled_packet.open_questions)
        for hint in rerun_hints:
            if hint and hint not in merged_open_questions:
                merged_open_questions.append(hint)
        if merged_open_questions != list(compiled_packet.open_questions):
            compiled_packet = compiled_packet.model_copy(update={"open_questions": merged_open_questions})

        compiled_context = compiled_packet.model_copy(
            update={
                "compile_metadata": {
                    **dict(compiled_packet.compile_metadata),
                    "chapter_memory_available": chapter_memory_snapshot is not None,
                    "rerun_hint_count": len([hint for hint in rerun_hints if hint]),
                }
            }
        )
        return CompiledContextLoadResult(
            context=compiled_context,
            chapter_memory_snapshot=chapter_memory_snapshot,
        )
