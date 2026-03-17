from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from book_agent.core.ids import stable_id
from book_agent.domain.models import MemorySnapshot, Sentence
from book_agent.domain.enums import ActorType, PacketStatus, RelationType, RunStatus, SegmentType, SentenceStatus, TargetSegmentStatus
from book_agent.domain.models.translation import AlignmentEdge, TargetSegment, TranslationRun
from book_agent.infra.repositories.chapter_memory import ChapterTranslationMemoryRepository
from book_agent.infra.repositories.translation import TranslationPacketBundle, TranslationRepository
from book_agent.services.context_compile import ChapterContextCompiler
from book_agent.workers.contracts import TranslationUsage, TranslationWorkerOutput, TranslationWorkerResult
from book_agent.workers.translator import EchoTranslationWorker, TranslationTask, TranslationWorker


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_segment_type(value: str) -> SegmentType:
    normalized = (value or "").strip().lower()
    mapping = {
        "sentence": SegmentType.SENTENCE,
        "translation": SegmentType.SENTENCE,
        "text": SegmentType.SENTENCE,
        "merged_sentence": SegmentType.MERGED_SENTENCE,
        "merged-sentence": SegmentType.MERGED_SENTENCE,
        "paragraph": SegmentType.MERGED_SENTENCE,
        "heading": SegmentType.HEADING,
        "title": SegmentType.HEADING,
        "footnote": SegmentType.FOOTNOTE,
        "caption": SegmentType.CAPTION,
        "protected": SegmentType.PROTECTED,
    }
    return mapping.get(normalized, SegmentType.SENTENCE)


def _normalize_relation_type(value: str) -> RelationType:
    normalized = (value or "").strip().lower()
    mapping = {
        "1:1": RelationType.ONE_TO_ONE,
        "one_to_one": RelationType.ONE_TO_ONE,
        "one-to-one": RelationType.ONE_TO_ONE,
        "1-1": RelationType.ONE_TO_ONE,
        "1:n": RelationType.ONE_TO_MANY,
        "one_to_many": RelationType.ONE_TO_MANY,
        "one-to-many": RelationType.ONE_TO_MANY,
        "1-n": RelationType.ONE_TO_MANY,
        "n:1": RelationType.MANY_TO_ONE,
        "many_to_one": RelationType.MANY_TO_ONE,
        "many-to-one": RelationType.MANY_TO_ONE,
        "n-1": RelationType.MANY_TO_ONE,
        "protected": RelationType.PROTECTED,
    }
    return mapping.get(normalized, RelationType.ONE_TO_ONE)


@dataclass(slots=True)
class TranslationExecutionArtifacts:
    translation_run: TranslationRun
    target_segments: list[TargetSegment]
    alignment_edges: list[AlignmentEdge]
    updated_sentences: list[Sentence]


class TranslationService:
    def __init__(
        self,
        repository: TranslationRepository,
        worker: TranslationWorker | None = None,
        chapter_memory_repository: ChapterTranslationMemoryRepository | None = None,
        context_compiler: ChapterContextCompiler | None = None,
    ):
        self.repository = repository
        self.worker = worker or EchoTranslationWorker()
        self.chapter_memory_repository = chapter_memory_repository or ChapterTranslationMemoryRepository(
            repository.session
        )
        self.context_compiler = context_compiler or ChapterContextCompiler()

    def execute_packet(self, packet_id: str) -> TranslationExecutionArtifacts:
        bundle = self.repository.load_packet_bundle(packet_id)
        chapter_memory_snapshot = self.chapter_memory_repository.load_latest(
            document_id=bundle.context_packet.document_id,
            chapter_id=bundle.context_packet.chapter_id,
        )
        compiled_context_packet = self.context_compiler.compile(
            bundle.context_packet,
            chapter_memory_snapshot=chapter_memory_snapshot,
        )
        worker_result = self._coerce_worker_result(
            self.worker.translate(
                TranslationTask(
                    context_packet=compiled_context_packet,
                    current_sentences=bundle.current_sentences,
                )
            )
        )
        artifacts = self._build_artifacts(bundle, worker_result, chapter_memory_snapshot)
        self.repository.save_translation_artifacts(
            translation_run=artifacts.translation_run,
            target_segments=artifacts.target_segments,
            alignment_edges=artifacts.alignment_edges,
            updated_sentences=artifacts.updated_sentences,
            packet=bundle.packet,
        )
        self._write_chapter_memory(
            bundle=bundle,
            artifacts=artifacts,
            current_snapshot=chapter_memory_snapshot,
            compiled_context_packet=compiled_context_packet,
        )
        self.repository.session.flush()
        return artifacts

    def _build_artifacts(
        self,
        bundle: TranslationPacketBundle,
        worker_result: TranslationWorkerResult,
        chapter_memory_snapshot: MemorySnapshot | None,
    ) -> TranslationExecutionArtifacts:
        now = _utcnow()
        attempt = self.repository.next_attempt(bundle.packet.id)
        metadata = self.worker.metadata()
        output = worker_result.output
        usage = worker_result.usage
        translation_run = TranslationRun(
            id=stable_id("translation-run", bundle.packet.id, attempt),
            packet_id=bundle.packet.id,
            model_name=metadata.model_name,
            model_config_json={
                "worker": metadata.worker_name,
                "context_compile_version": self.context_compiler.compile_version,
                "chapter_memory_snapshot_version_used": (
                    chapter_memory_snapshot.version if chapter_memory_snapshot is not None else None
                ),
                **metadata.runtime_config,
            },
            prompt_version=metadata.prompt_version,
            attempt=attempt,
            status=RunStatus.SUCCEEDED,
            output_json=output.model_dump(mode="json"),
            token_in=usage.token_in,
            token_out=usage.token_out,
            cost_usd=usage.cost_usd,
            latency_ms=usage.latency_ms,
            created_at=now,
            updated_at=now,
        )

        temp_to_target_id: dict[str, str] = {}
        target_segments: list[TargetSegment] = []
        valid_sentence_ids = {sentence.id for sentence in bundle.current_sentences}
        for ordinal, segment in enumerate(output.target_segments, start=1):
            target_id = stable_id("target-segment", translation_run.id, ordinal)
            temp_to_target_id[segment.temp_id] = target_id
            target_segments.append(
                TargetSegment(
                    id=target_id,
                    chapter_id=bundle.packet.chapter_id,
                    translation_run_id=translation_run.id,
                    ordinal=ordinal,
                    text_zh=segment.text_zh,
                    segment_type=_normalize_segment_type(segment.segment_type),
                    confidence=segment.confidence,
                    final_status=TargetSegmentStatus.DRAFT,
                    created_at=now,
                    updated_at=now,
                )
            )

        alignment_edges: list[AlignmentEdge] = []
        for suggestion in output.alignment_suggestions:
            valid_source_sentence_ids = [
                sentence_id for sentence_id in suggestion.source_sentence_ids if sentence_id in valid_sentence_ids
            ]
            valid_target_temp_ids = [
                temp_id for temp_id in suggestion.target_temp_ids if temp_id in temp_to_target_id
            ]
            for sentence_id in valid_source_sentence_ids:
                for temp_id in valid_target_temp_ids:
                    target_id = temp_to_target_id[temp_id]
                    alignment_edges.append(
                        AlignmentEdge(
                            id=stable_id("alignment-edge", sentence_id, target_id),
                            sentence_id=sentence_id,
                            target_segment_id=target_id,
                            relation_type=_normalize_relation_type(suggestion.relation_type),
                            confidence=suggestion.confidence,
                            created_by=ActorType.MODEL,
                            created_at=now,
                        )
                    )

        low_confidence_ids = {flag.sentence_id for flag in output.low_confidence_flags}
        updated_sentences: list[Sentence] = []
        for sentence in bundle.current_sentences:
            sentence.sentence_status = (
                SentenceStatus.REVIEW_REQUIRED if sentence.id in low_confidence_ids else SentenceStatus.TRANSLATED
            )
            sentence.updated_at = now
            updated_sentences.append(sentence)

        bundle.packet.status = PacketStatus.TRANSLATED
        bundle.packet.updated_at = now
        return TranslationExecutionArtifacts(
            translation_run=translation_run,
            target_segments=target_segments,
            alignment_edges=alignment_edges,
            updated_sentences=updated_sentences,
        )

    def _write_chapter_memory(
        self,
        *,
        bundle: TranslationPacketBundle,
        artifacts: TranslationExecutionArtifacts,
        current_snapshot: MemorySnapshot | None,
        compiled_context_packet,
    ) -> None:
        existing_content = dict(current_snapshot.content_json) if current_snapshot is not None else {}
        recent_accepted = existing_content.get("recent_accepted_translations", [])
        if not isinstance(recent_accepted, list):
            recent_accepted = []

        target_excerpt = " ".join(segment.text_zh.strip() for segment in artifacts.target_segments if segment.text_zh).strip()
        source_excerpt = " ".join(
            (sentence.normalized_text or sentence.source_text or "").strip()
            for sentence in bundle.current_sentences
        ).strip()
        if source_excerpt and target_excerpt:
            entry: dict[str, Any] = {
                "packet_id": bundle.packet.id,
                "block_id": bundle.packet.block_start_id,
                "source_excerpt": source_excerpt,
                "target_excerpt": target_excerpt,
                "source_sentence_ids": [sentence.id for sentence in bundle.current_sentences],
            }
            recent_accepted = [
                item
                for item in recent_accepted
                if not (isinstance(item, dict) and item.get("packet_id") == bundle.packet.id)
            ]
            recent_accepted.append(entry)
            recent_accepted = recent_accepted[-4:]

        content_json = {
            "schema_version": 1,
            "chapter_id": bundle.packet.chapter_id,
            "chapter_title": existing_content.get("chapter_title"),
            "heading_path": existing_content.get("heading_path") or compiled_context_packet.heading_path,
            "chapter_brief": existing_content.get("chapter_brief") or compiled_context_packet.chapter_brief,
            "recent_accepted_translations": recent_accepted,
            "last_packet_id": bundle.packet.id,
            "last_translation_run_id": artifacts.translation_run.id,
        }
        self.chapter_memory_repository.supersede_and_create_next(
            current_snapshot=current_snapshot,
            document_id=bundle.context_packet.document_id,
            chapter_id=bundle.context_packet.chapter_id,
            content_json=content_json,
        )

    def _coerce_worker_result(
        self,
        payload: TranslationWorkerResult | TranslationWorkerOutput,
    ) -> TranslationWorkerResult:
        if isinstance(payload, TranslationWorkerResult):
            return payload
        if isinstance(payload, TranslationWorkerOutput):
            return TranslationWorkerResult(output=payload, usage=TranslationUsage())
        raise TypeError(f"Unsupported translation worker payload: {type(payload)!r}")
