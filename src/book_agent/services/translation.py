from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Protocol

from book_agent.core.ids import stable_id
from book_agent.domain.enums import (
    ActorType,
    PacketStatus,
    RelationType,
    RunStatus,
    SegmentType,
    SentenceStatus,
    TargetSegmentStatus,
)
from book_agent.domain.event_kinds import (
    GLOSSARY_VIOLATION,
    LLM_CALL_COMPLETED,
    LLM_CALL_FAILED,
    LLM_CALL_STARTED,
    PACKET_TRANSLATED,
)
from book_agent.domain.models import MemorySnapshot, Sentence
from book_agent.domain.models.translation import AlignmentEdge, TargetSegment, TranslationRun
from book_agent.infra.repositories.chapter_memory import ChapterTranslationMemoryRepository
from book_agent.infra.repositories.events import emit_event
from book_agent.infra.repositories.translation import TranslationPacketBundle, TranslationRepository
from book_agent.services.context_compile import ChapterContextCompileOptions, ChapterContextCompiler
from book_agent.services.glossary_enforcement import detect_violations
from book_agent.services.glossary_service import GlossaryService
from book_agent.services.memory_service import MemoryService
from book_agent.services.term_normalization import normalize_concept_payload
from book_agent.translation.chapter_memory import ChapterMemory
from book_agent.translation.contracts import (
    CompiledTranslationContext,
    TranslationUsage,
    TranslationWorkerOutput,
    TranslationWorkerResult,
)
from book_agent.translation.output_validation import OutputValidator
from book_agent.workers.failures import classify_failure
from book_agent.workers.translator import (
    EchoTranslationWorker,
    TranslationTask,
    TranslationWorker,
    TranslationWorkerMetadata,
)


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


@dataclass(slots=True)
class PreparedPacketTranslation:
    """Everything needed to run the worker and later persist its result."""

    packet_id: str
    chapter_id: str
    run_id: str | None
    task: TranslationTask
    worker_metadata: TranslationWorkerMetadata
    chapter_memory_snapshot_id: str | None
    call_id: str
    started_at: datetime

    @property
    def correlation_id(self) -> str:
        return f"packet:{self.packet_id}"


class TranslationService:
    def __init__(
        self,
        repository: TranslationRepository,
        worker: TranslationWorker | None = None,
        chapter_memory_repository: ChapterTranslationMemoryRepository | None = None,
        context_compiler: ChapterContextCompiler | None = None,
        memory_service: MemoryService | None = None,
        default_auto_commit_memory: bool = False,
        post_translation_hooks: Sequence[PostTranslationHook] | None = None,
        output_validator: OutputValidator | None = None,
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
        self.default_auto_commit_memory = default_auto_commit_memory
        self.output_validator = output_validator or OutputValidator()
        self.post_translation_hooks: tuple[PostTranslationHook, ...] = (
            tuple(post_translation_hooks)
            if post_translation_hooks is not None
            else (GlossaryViolationHook(), ChapterMemoryProposalHook())
        )

    def execute_packet(
        self,
        packet_id: str,
        *,
        compile_options: ChapterContextCompileOptions | None = None,
        rerun_hints: tuple[str, ...] = (),
        auto_commit_memory: bool | None = None,
        run_id: str | None = None,
    ) -> TranslationExecutionArtifacts:
        """Prepare, translate and persist a packet within the current session.

        Callers that must not hold a database transaction across the LLM call
        (the run executor) use prepare_packet / call_worker /
        persist_packet_result with separate sessions instead.
        """
        prepared = self.prepare_packet(
            packet_id,
            compile_options=compile_options,
            rerun_hints=rerun_hints,
            run_id=run_id,
        )
        try:
            worker_result = self.call_worker(prepared)
        except Exception as exc:
            self.record_worker_failure(prepared, exc)
            raise
        return self.persist_packet_result(
            prepared,
            worker_result,
            auto_commit_memory=auto_commit_memory,
        )

    def prepare_packet(
        self,
        packet_id: str,
        *,
        compile_options: ChapterContextCompileOptions | None = None,
        rerun_hints: tuple[str, ...] = (),
        run_id: str | None = None,
    ) -> PreparedPacketTranslation:
        bundle = self.repository.load_packet_bundle(packet_id)
        compiled_context_result = self.memory_service.load_compiled_context(
            packet=bundle.context_packet,
            options=compile_options,
            rerun_hints=rerun_hints,
        )
        chapter_memory_snapshot = compiled_context_result.chapter_memory_snapshot
        compiled_context_packet = compiled_context_result.context
        worker_metadata = self.worker.metadata()
        prepared = PreparedPacketTranslation(
            packet_id=bundle.packet.id,
            chapter_id=bundle.packet.chapter_id,
            run_id=run_id,
            task=TranslationTask(
                context_packet=compiled_context_packet,
                current_sentences=bundle.current_sentences,
            ),
            worker_metadata=worker_metadata,
            chapter_memory_snapshot_id=(
                chapter_memory_snapshot.id if chapter_memory_snapshot is not None else None
            ),
            call_id=stable_id("llm-call", bundle.packet.id, str(_utcnow().timestamp())),
            started_at=_utcnow(),
        )
        emit_event(
            self.repository.session,
            kind=LLM_CALL_STARTED,
            run_id=run_id,
            chapter_id=prepared.chapter_id,
            packet_id=prepared.packet_id,
            actor_kind="agent",
            actor_id=f"worker.{worker_metadata.worker_name}",
            correlation_id=prepared.correlation_id,
            payload={
                "call_id": prepared.call_id,
                "backend": worker_metadata.worker_name,
                "model": worker_metadata.model_name,
                "sentence_count": len(bundle.current_sentences),
            },
        )
        return prepared

    def call_worker(self, prepared: PreparedPacketTranslation) -> TranslationWorkerResult:
        """Run the translation worker. Touches no database state."""
        return self._coerce_worker_result(self.worker.translate(prepared.task))

    def record_worker_failure(self, prepared: PreparedPacketTranslation, exc: Exception) -> None:
        """Record a failed attempt: a FAILED translation run with its error code, and the event."""
        metadata = prepared.worker_metadata
        now = _utcnow()
        attempt = self.repository.next_attempt(prepared.packet_id)
        error_code = classify_failure(exc).reason
        self.repository.session.add(
            TranslationRun(
                id=stable_id("translation-run", prepared.packet_id, attempt),
                packet_id=prepared.packet_id,
                model_name=metadata.model_name,
                model_config_json={"worker": metadata.worker_name, **metadata.runtime_config},
                prompt_version=metadata.prompt_version,
                attempt=attempt,
                status=RunStatus.FAILED,
                error_code=error_code,
                created_at=now,
                updated_at=now,
            )
        )
        self.repository.session.flush()
        emit_event(
            self.repository.session,
            kind=LLM_CALL_FAILED,
            run_id=prepared.run_id,
            chapter_id=prepared.chapter_id,
            packet_id=prepared.packet_id,
            actor_kind="agent",
            actor_id=f"worker.{metadata.worker_name}",
            correlation_id=prepared.correlation_id,
            payload={
                "call_id": prepared.call_id,
                "backend": metadata.worker_name,
                "model": metadata.model_name,
                "error_class": type(exc).__name__,
                "error_code": error_code,
                "error_message": str(exc)[:500],
                "elapsed_ms": int((_utcnow() - prepared.started_at).total_seconds() * 1000),
            },
        )

    def persist_packet_result(
        self,
        prepared: PreparedPacketTranslation,
        worker_result: TranslationWorkerResult,
        *,
        auto_commit_memory: bool | None = None,
    ) -> TranslationExecutionArtifacts:
        effective_auto_commit_memory = (
            self.default_auto_commit_memory if auto_commit_memory is None else auto_commit_memory
        )
        run_id = prepared.run_id
        worker_metadata = prepared.worker_metadata
        compiled_context_packet = prepared.task.context_packet
        bundle = self.repository.load_packet_bundle(prepared.packet_id)
        chapter_memory_snapshot = (
            self.repository.session.get(MemorySnapshot, prepared.chapter_memory_snapshot_id)
            if prepared.chapter_memory_snapshot_id is not None
            else None
        )
        _usage = worker_result.usage
        emit_event(
            self.repository.session,
            kind=LLM_CALL_COMPLETED,
            run_id=run_id,
            chapter_id=bundle.packet.chapter_id,
            packet_id=bundle.packet.id,
            actor_kind="agent",
            actor_id=f"worker.{worker_metadata.worker_name}",
            correlation_id=prepared.correlation_id,
            payload={
                "call_id": prepared.call_id,
                "backend": worker_metadata.worker_name,
                "model": worker_metadata.model_name,
                "token_in": int(_usage.token_in or 0),
                "token_out": int(_usage.token_out or 0),
                "total_tokens": int(_usage.total_tokens or 0),
                "cost_usd": float(_usage.cost_usd) if _usage.cost_usd is not None else None,
                "latency_ms": int(_usage.latency_ms or 0),
                "provider_request_id": _usage.provider_request_id,
            },
        )
        validation = self.output_validator.validate(
            [sentence.id for sentence in bundle.current_sentences],
            worker_result.output,
        )
        artifacts = self._build_artifacts(bundle, worker_result, compiled_context_packet, worker_metadata)
        artifacts.translation_run.error_code = validation.error_code
        self.repository.save_translation_artifacts(
            translation_run=artifacts.translation_run,
            target_segments=artifacts.target_segments,
            alignment_edges=artifacts.alignment_edges,
            updated_sentences=artifacts.updated_sentences,
            packet=bundle.packet,
        )
        translated_payload = {
            "translation_run_id": artifacts.translation_run.id,
            "attempt": artifacts.translation_run.attempt,
            "target_segment_count": len(artifacts.target_segments),
            "sentence_count": len(bundle.current_sentences),
        }
        if not validation.ok:
            translated_payload["output_validation"] = validation.to_json()
        emit_event(
            self.repository.session,
            kind=PACKET_TRANSLATED,
            run_id=run_id,
            chapter_id=bundle.packet.chapter_id,
            packet_id=bundle.packet.id,
            actor_kind="system",
            actor_id="services.translation",
            payload=translated_payload,
        )
        outcome = PacketTranslationOutcome(
            bundle=bundle,
            artifacts=artifacts,
            compiled_context_packet=compiled_context_packet,
            chapter_memory_snapshot=chapter_memory_snapshot,
            run_id=run_id,
            auto_commit_memory=effective_auto_commit_memory,
        )
        for hook in self.post_translation_hooks:
            hook.after_packet_translated(self, outcome)
        self.repository.session.flush()
        return artifacts

    def _emit_glossary_violations(
        self,
        *,
        bundle: TranslationPacketBundle,
        artifacts: "TranslationExecutionArtifacts",
        run_id: str | None,
    ) -> None:
        """Detect locked-term violations in the freshly-translated packet
        and emit `GLOSSARY_VIOLATION` events. PDF v2 M2.7 worker integration.

        Resolution model: per-document glossary is loaded once per
        execute_packet call. Source/target text is concatenated across
        the packet's current sentences and target segments respectively;
        a single detect_violations pass produces one event per breached
        rule. Per-sentence pinpointing can come later — first ship the
        signal in production.
        """
        document_id = bundle.context_packet.document_id
        if not document_id:
            return
        glossary_service = GlossaryService(self.repository.session)
        locked = glossary_service.get_locked_terms(document_id)
        if not locked:
            return
        source_text = "\n".join(s.source_text for s in bundle.current_sentences if s.source_text)
        target_text = "\n".join(
            seg.text_zh for seg in artifacts.target_segments if getattr(seg, "text_zh", None)
        )
        violations = detect_violations(source_text, target_text, locked)
        for v in violations:
            emit_event(
                self.repository.session,
                kind=GLOSSARY_VIOLATION,
                run_id=run_id,
                chapter_id=bundle.packet.chapter_id,
                packet_id=bundle.packet.id,
                actor_kind="system",
                actor_id="services.translation.glossary_enforcement",
                payload={
                    "translation_run_id": artifacts.translation_run.id,
                    "document_id": document_id,
                    "source_term": v.source_term,
                    "expected_target": v.expected_target,
                    "source_match_count": v.source_match_count,
                    "target_match_count": v.target_match_count,
                    "severity_hint": v.severity_hint,
                },
            )

    def _build_artifacts(
        self,
        bundle: TranslationPacketBundle,
        worker_result: TranslationWorkerResult,
        compiled_context_packet: CompiledTranslationContext,
        metadata: TranslationWorkerMetadata,
    ) -> TranslationExecutionArtifacts:
        now = _utcnow()
        attempt = self.repository.next_attempt(bundle.packet.id)
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
        content_json = self._build_chapter_memory_content_json(
            bundle=bundle,
            artifacts=artifacts,
            current_snapshot=current_snapshot,
            compiled_context_packet=compiled_context_packet,
        )
        self.chapter_memory_repository.supersede_and_create_next(
            current_snapshot=current_snapshot,
            document_id=bundle.context_packet.document_id,
            chapter_id=bundle.context_packet.chapter_id,
            content_json=content_json,
        )

    def _build_chapter_memory_content_json(
        self,
        *,
        bundle: TranslationPacketBundle,
        artifacts: TranslationExecutionArtifacts,
        current_snapshot: MemorySnapshot | None,
        compiled_context_packet,
    ) -> dict[str, Any]:
        memory = ChapterMemory.from_content(current_snapshot.content_json if current_snapshot is not None else None)
        memory = memory.with_brief(
            compiled_context_packet.chapter_brief,
            version=bundle.packet.chapter_brief_version,
            heading_path=compiled_context_packet.heading_path,
        )

        target_excerpt = " ".join(segment.text_zh.strip() for segment in artifacts.target_segments if segment.text_zh).strip()
        source_excerpt = " ".join(
            (sentence.normalized_text or sentence.source_text or "").strip()
            for sentence in bundle.current_sentences
        ).strip()
        if source_excerpt and target_excerpt:
            memory = memory.with_recent_translation(
                {
                    "packet_id": bundle.packet.id,
                    "block_id": bundle.packet.block_start_id,
                    "source_excerpt": source_excerpt,
                    "target_excerpt": target_excerpt,
                    "source_sentence_ids": [sentence.id for sentence in bundle.current_sentences],
                }
            )

        memory = replace(
            memory,
            chapter_id=bundle.packet.chapter_id,
            active_concepts=self._merge_active_concepts(
                existing_concepts=memory.active_concepts,
                source_sentences=[sentence.source_text for sentence in bundle.current_sentences],
                packet_id=bundle.packet.id,
            ),
            last_packet_id=bundle.packet.id,
            last_translation_run_id=artifacts.translation_run.id,
        )
        return memory.to_content()

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


@dataclass(slots=True)
class PacketTranslationOutcome:
    """A packet translation that has just been persisted, as seen by post-translation hooks."""

    bundle: TranslationPacketBundle
    artifacts: TranslationExecutionArtifacts
    compiled_context_packet: CompiledTranslationContext
    chapter_memory_snapshot: MemorySnapshot | None
    run_id: str | None
    auto_commit_memory: bool


class PostTranslationHook(Protocol):
    def after_packet_translated(self, service: TranslationService, outcome: PacketTranslationOutcome) -> None:
        ...


class GlossaryViolationHook:
    """PDF v2 M2.7: report locked-glossary violations as events without failing the run."""

    def after_packet_translated(self, service: TranslationService, outcome: PacketTranslationOutcome) -> None:
        try:
            service._emit_glossary_violations(
                bundle=outcome.bundle,
                artifacts=outcome.artifacts,
                run_id=outcome.run_id,
            )
        except Exception:  # pragma: no cover - post-validation must never break translation
            pass


class ChapterMemoryProposalHook:
    """Record the chapter memory proposal for the packet and commit it when auto-commit is on."""

    def after_packet_translated(self, service: TranslationService, outcome: PacketTranslationOutcome) -> None:
        context_packet = outcome.bundle.context_packet
        service.memory_service.record_translation_proposals(
            document_id=context_packet.document_id,
            chapter_id=context_packet.chapter_id,
            packet_id=outcome.bundle.packet.id,
            translation_run_id=outcome.artifacts.translation_run.id,
            current_snapshot=outcome.chapter_memory_snapshot,
            proposed_content_json=service._build_chapter_memory_content_json(
                bundle=outcome.bundle,
                artifacts=outcome.artifacts,
                current_snapshot=outcome.chapter_memory_snapshot,
                compiled_context_packet=outcome.compiled_context_packet,
            ),
        )
        if outcome.auto_commit_memory:
            service.memory_service.commit_approved_packet_memory(
                document_id=context_packet.document_id,
                chapter_id=context_packet.chapter_id,
                translation_run_id=outcome.artifacts.translation_run.id,
            )
