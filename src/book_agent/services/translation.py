from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any

from book_agent.core.ids import stable_id
from book_agent.domain.models import MemorySnapshot, Sentence
from book_agent.domain.enums import ActorType, PacketStatus, RelationType, RunStatus, SegmentType, SentenceStatus, TargetSegmentStatus
from book_agent.domain.models.translation import AlignmentEdge, TargetSegment, TranslationRun
from book_agent.infra.repositories.chapter_memory import ChapterTranslationMemoryRepository
from book_agent.infra.repositories.translation import TranslationPacketBundle, TranslationRepository
from book_agent.services.context_compile import ChapterContextCompileOptions, ChapterContextCompiler
from book_agent.services.memory_service import MemoryService
from book_agent.services.term_normalization import normalize_concept_payload
from book_agent.workers.contracts import (
    CompiledTranslationContext,
    TranslationUsage,
    TranslationWorkerOutput,
    TranslationWorkerResult,
)
from book_agent.workers.translator import EchoTranslationWorker, TranslationTask, TranslationWorker


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_nonnegative_int(value: object) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


CONCEPT_HINT_KEYWORDS = {
    "agent",
    "agentic",
    "ai",
    "context",
    "engineering",
    "generative",
    "language",
    "llm",
    "memory",
    "model",
    "models",
    "architecture",
    "distributed",
    "infrastructure",
    "planning",
    "retrieval",
    "sql",
    "substrate",
}
CONCEPT_HEADWORDS = {
    "ai",
    "agent",
    "agents",
    "architecture",
    "engineering",
    "infrastructure",
    "llm",
    "mechanisms",
    "memory",
    "model",
    "models",
    "modules",
    "sql",
    "stores",
    "substrate",
}
CONCEPT_MODIFIERS = {
    "adaptive",
    "agentic",
    "context",
    "data",
    "distributed",
    "durable",
    "external",
    "generative",
    "language",
    "large",
    "memory",
    "planning",
    "prompt",
    "reactive",
    "retrieval",
    "structured",
}
STOPWORDS = {
    "about",
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "been",
    "being",
    "beyond",
    "by",
    "call",
    "called",
    "calls",
    "created",
    "creates",
    "creating",
    "does",
    "doing",
    "even",
    "for",
    "from",
    "how",
    "if",
    "implies",
    "in",
    "into",
    "is",
    "it",
    "its",
    "itself",
    "just",
    "made",
    "maintained",
    "maintaining",
    "makes",
    "more",
    "not",
    "of",
    "on",
    "or",
    "our",
    "out",
    "over",
    "same",
    "some",
    "that",
    "their",
    "them",
    "then",
    "the",
    "these",
    "this",
    "those",
    "to",
    "through",
    "up",
    "we",
    "what",
    "when",
    "with",
    "why",
}
TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9-]*")


def _is_acronym(token: str) -> bool:
    alpha = "".join(char for char in token if char.isalpha())
    return bool(alpha) and alpha.isupper() and len(alpha) >= 2


def _looks_like_proper_name(token: str) -> bool:
    return token[:1].isupper() and token[1:].islower()


def _is_allowed_concept_phrase(phrase_tokens: list[str]) -> bool:
    lowered_tokens = [token.lower() for token in phrase_tokens]
    if any(token in STOPWORDS for token in lowered_tokens):
        return False
    if len(phrase_tokens) < 2:
        return False

    last_token = lowered_tokens[-1]
    if last_token not in CONCEPT_HEADWORDS:
        return False

    if all(token.islower() for token in lowered_tokens) and len(phrase_tokens) > 2:
        return False

    preceding_tokens = phrase_tokens[:-1]
    if len(preceding_tokens) >= 2 and all(_looks_like_proper_name(token) for token in preceding_tokens[:2]):
        return False

    for token, lowered in zip(preceding_tokens, lowered_tokens[:-1], strict=True):
        if lowered in CONCEPT_MODIFIERS or lowered in CONCEPT_HINT_KEYWORDS:
            continue
        if _is_acronym(token):
            continue
        if _looks_like_proper_name(token) and lowered in CONCEPT_MODIFIERS:
            continue
        return False
    return True


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


def _dedupe_alignment_edges(edges: list[AlignmentEdge]) -> list[AlignmentEdge]:
    deduped: list[AlignmentEdge] = []
    seen_pairs: set[tuple[str, str]] = set()
    for edge in edges:
        key = (edge.sentence_id, edge.target_segment_id)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        deduped.append(edge)
    return deduped


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
        memory_service: MemoryService | None = None,
    ):
        self.repository = repository
        self.worker = worker or EchoTranslationWorker()
        self.chapter_memory_repository = chapter_memory_repository or ChapterTranslationMemoryRepository(
            repository.session
        )
        self.context_compiler = context_compiler or ChapterContextCompiler()
        self.memory_service = memory_service or MemoryService(
            chapter_memory_repository=self.chapter_memory_repository,
            context_compiler=self.context_compiler,
        )

    def execute_packet(
        self,
        packet_id: str,
        *,
        compile_options: ChapterContextCompileOptions | None = None,
        rerun_hints: tuple[str, ...] = (),
    ) -> TranslationExecutionArtifacts:
        bundle = self.repository.load_packet_bundle(packet_id)
        compiled_context_result = self.memory_service.load_compiled_context(
            packet=bundle.context_packet,
            options=compile_options,
            rerun_hints=rerun_hints,
        )
        chapter_memory_snapshot = compiled_context_result.chapter_memory_snapshot
        compiled_context_packet = compiled_context_result.context
        worker_result = self._coerce_worker_result(
            self.worker.translate(
                TranslationTask(
                    context_packet=compiled_context_packet,
                    current_sentences=bundle.current_sentences,
                )
            )
        )
        artifacts = self._build_artifacts(bundle, worker_result, compiled_context_packet)
        self.repository.save_translation_artifacts(
            translation_run=artifacts.translation_run,
            target_segments=artifacts.target_segments,
            alignment_edges=artifacts.alignment_edges,
            updated_sentences=artifacts.updated_sentences,
            packet=bundle.packet,
        )
        self.write_chapter_memory(
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
        compiled_context_packet: CompiledTranslationContext,
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
                "context_compile_version": compiled_context_packet.context_compile_version,
                "chapter_memory_snapshot_version_used": compiled_context_packet.memory_version_used,
                "compiled_context_metadata": dict(compiled_context_packet.compile_metadata),
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
        alignment_edges = _dedupe_alignment_edges(alignment_edges)

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

    def write_chapter_memory(
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
        active_concepts = existing_content.get("active_concepts", [])
        if not isinstance(active_concepts, list):
            active_concepts = []
        existing_brief_version = _coerce_nonnegative_int(existing_content.get("chapter_brief_version"))
        packet_brief_version = _coerce_nonnegative_int(bundle.packet.chapter_brief_version)
        chapter_brief = existing_content.get("chapter_brief")
        heading_path = existing_content.get("heading_path") or compiled_context_packet.heading_path
        if compiled_context_packet.chapter_brief and (
            chapter_brief is None or packet_brief_version >= existing_brief_version
        ):
            chapter_brief = compiled_context_packet.chapter_brief
            existing_brief_version = packet_brief_version
            heading_path = compiled_context_packet.heading_path

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

        active_concepts = self._merge_active_concepts(
            existing_concepts=active_concepts,
            source_sentences=[sentence.source_text for sentence in bundle.current_sentences],
            packet_id=bundle.packet.id,
        )

        content_json = {
            "schema_version": 1,
            "chapter_id": bundle.packet.chapter_id,
            "chapter_title": existing_content.get("chapter_title"),
            "heading_path": heading_path,
            "chapter_brief": chapter_brief,
            "chapter_brief_version": existing_brief_version or None,
            "active_concepts": active_concepts,
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

    def _merge_active_concepts(
        self,
        *,
        existing_concepts: list[dict[str, Any]],
        source_sentences: list[str],
        packet_id: str,
    ) -> list[dict[str, Any]]:
        concept_map: dict[str, dict[str, Any]] = {}
        for concept in existing_concepts:
            if not isinstance(concept, dict):
                continue
            normalized_concept = normalize_concept_payload(concept)
            key = str(normalized_concept.get("source_term") or "").strip().lower()
            if not key:
                continue
            concept_map[key] = normalized_concept

        for source_term in self._extract_concept_candidates(source_sentences):
            key = source_term.lower()
            mention_count = self._count_concept_mentions(source_term, source_sentences)
            current = concept_map.get(key)
            if current is None:
                packet_mention_counts = {packet_id: mention_count}
                concept_map[key] = {
                    "source_term": source_term,
                    "canonical_zh": None,
                    "status": "candidate",
                    "confidence": 0.6,
                    "first_seen_packet_id": packet_id,
                    "last_seen_packet_id": packet_id,
                    "packet_ids_seen": [packet_id],
                    "times_seen": 1,
                    "mention_count": mention_count,
                    "packet_mention_counts": packet_mention_counts,
                }
                continue
            current["last_seen_packet_id"] = packet_id
            packet_ids_seen = [
                str(item).strip()
                for item in list(current.get("packet_ids_seen") or [])
                if str(item).strip()
            ]
            if packet_id not in packet_ids_seen:
                packet_ids_seen.append(packet_id)
            current["packet_ids_seen"] = packet_ids_seen
            current["times_seen"] = len(packet_ids_seen)
            packet_mention_counts = self._normalize_packet_mention_counts(current, packet_ids_seen)
            packet_mention_counts[packet_id] = mention_count
            current["packet_mention_counts"] = packet_mention_counts
            current["mention_count"] = sum(packet_mention_counts.values())

        concepts = list(concept_map.values())
        concepts.sort(
            key=lambda item: (
                0 if item.get("canonical_zh") else 1,
                -(int(item.get("times_seen") or 0)),
                str(item.get("source_term") or "").lower(),
            )
        )
        return concepts[:12]

    def _count_concept_mentions(self, source_term: str, source_sentences: list[str]) -> int:
        lowered = source_term.casefold()
        return sum(1 for sentence in source_sentences if lowered in str(sentence or "").casefold())

    def _normalize_packet_mention_counts(
        self,
        concept: dict[str, Any],
        packet_ids_seen: list[str],
    ) -> dict[str, int]:
        raw_counts = concept.get("packet_mention_counts")
        normalized: dict[str, int] = {}
        if isinstance(raw_counts, dict):
            for packet_id, count in raw_counts.items():
                key = str(packet_id).strip()
                if not key:
                    continue
                normalized[key] = max(int(count or 0), 0)
        if normalized:
            for packet_id in packet_ids_seen:
                normalized.setdefault(packet_id, 0)
            return normalized

        if not packet_ids_seen:
            return {}

        total_mentions = max(int(concept.get("mention_count") or concept.get("times_seen") or 0), 0)
        if total_mentions == 0:
            return {packet_id: 0 for packet_id in packet_ids_seen}

        base = max(total_mentions // len(packet_ids_seen), 1)
        remainder = max(total_mentions - (base * len(packet_ids_seen)), 0)
        fallback_counts: dict[str, int] = {}
        for index, packet_id in enumerate(packet_ids_seen):
            fallback_counts[packet_id] = base + (1 if index < remainder else 0)
        return fallback_counts

    def _extract_concept_candidates(self, source_sentences: list[str]) -> list[str]:
        candidates: dict[str, str] = {}
        for sentence in source_sentences:
            tokens = TOKEN_PATTERN.findall(sentence or "")
            if not tokens:
                continue
            for size in range(2, 5):
                for index in range(0, len(tokens) - size + 1):
                    phrase_tokens = tokens[index : index + size]
                    lowered_tokens = [token.lower() for token in phrase_tokens]
                    if not any(token in CONCEPT_HINT_KEYWORDS for token in lowered_tokens):
                        continue
                    if sum(1 for token in lowered_tokens if token not in STOPWORDS) < 2:
                        continue
                    if not _is_allowed_concept_phrase(phrase_tokens):
                        continue
                    phrase = " ".join(phrase_tokens)
                    key = phrase.lower()
                    if key not in candidates:
                        candidates[key] = phrase
        return list(candidates.values())

    def _coerce_worker_result(
        self,
        payload: TranslationWorkerResult | TranslationWorkerOutput,
    ) -> TranslationWorkerResult:
        if isinstance(payload, TranslationWorkerResult):
            return payload
        if isinstance(payload, TranslationWorkerOutput):
            return TranslationWorkerResult(output=payload, usage=TranslationUsage())
        raise TypeError(f"Unsupported translation worker payload: {type(payload)!r}")
