from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from book_agent.core.ids import stable_id
from book_agent.domain.models import Sentence
from book_agent.workers.contracts import (
    AlignmentSuggestion,
    ContextPacket,
    TranslationTargetSegment,
    TranslationUsage,
    TranslationWorkerOutput,
    TranslationWorkerResult,
)


@dataclass(frozen=True, slots=True)
class TranslationWorkerMetadata:
    worker_name: str
    model_name: str
    prompt_version: str
    runtime_config: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TranslationTask:
    context_packet: ContextPacket
    current_sentences: list[Sentence]


class TranslationWorker(Protocol):
    def metadata(self) -> TranslationWorkerMetadata:
        ...

    def translate(self, task: TranslationTask) -> TranslationWorkerResult | TranslationWorkerOutput:
        ...


@dataclass(frozen=True, slots=True)
class TranslationPromptRequest:
    packet_id: str
    model_name: str
    prompt_version: str
    system_prompt: str
    user_prompt: str
    response_schema: dict[str, Any]
    sentence_alias_map: dict[str, str] = field(default_factory=dict)


class TranslationModelClient(Protocol):
    def generate_translation(self, request: TranslationPromptRequest) -> TranslationWorkerResult | TranslationWorkerOutput:
        ...


def build_translation_prompt_request(
    task: TranslationTask,
    *,
    model_name: str,
    prompt_version: str,
) -> TranslationPromptRequest:
    packet = task.context_packet
    heading_path = " > ".join(packet.heading_path) if packet.heading_path else "(root)"
    term_lines = [
        f"- {term.source_term} => {term.target_term} ({term.lock_level})" for term in packet.relevant_terms
    ] or ["- none"]
    entity_lines = [
        f"- {entity.name} [{entity.entity_type}] => {entity.canonical_zh or '(unset)'}"
        for entity in packet.relevant_entities
    ] or ["- none"]
    sentence_alias_map = {
        f"S{index}": sentence.id for index, sentence in enumerate(task.current_sentences, start=1)
    }
    sentence_lines = [
        f"{index}. [{alias}] {sentence.source_text}"
        for index, (alias, sentence) in enumerate(
            zip(sentence_alias_map.keys(), task.current_sentences, strict=False),
            start=1,
        )
    ]
    user_prompt = "\n".join(
        [
            f"Packet ID: {packet.packet_id}",
            f"Heading Path: {heading_path}",
            f"Chapter Brief: {packet.chapter_brief or '(none)'}",
            "Relevant Terms:",
            *term_lines,
            "Relevant Entities:",
            *entity_lines,
            "Current Sentences:",
            *sentence_lines,
            "Use only the sentence aliases shown above (for example: S1, S2) in source_sentence_ids and low_confidence_flags.sentence_id.",
            "Return JSON that matches the provided response schema.",
        ]
    )
    system_prompt = (
        "You are a high-fidelity book translation worker. "
        "Translate every translatable English sentence into natural Chinese, "
        "preserve meaning, respect locked terms, and do not translate protected spans. "
        "You may reorganize sentence structure, but alignment coverage must remain complete."
    )
    return TranslationPromptRequest(
        packet_id=packet.packet_id,
        model_name=model_name,
        prompt_version=prompt_version,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_schema=TranslationWorkerOutput.model_json_schema(),
        sentence_alias_map=sentence_alias_map,
    )


class EchoTranslationWorker:
    """Deterministic placeholder worker for pipeline validation."""

    def __init__(self, model_name: str = "echo-worker", prompt_version: str = "p0.echo.v1"):
        self._metadata = TranslationWorkerMetadata(
            worker_name=self.__class__.__name__,
            model_name=model_name,
            prompt_version=prompt_version,
            runtime_config={"mode": "deterministic"},
        )

    def metadata(self) -> TranslationWorkerMetadata:
        return self._metadata

    def translate(self, task: TranslationTask) -> TranslationWorkerResult:
        target_segments: list[TranslationTargetSegment] = []
        alignments: list[AlignmentSuggestion] = []

        for sentence in task.current_sentences:
            temp_id = stable_id("temp-segment", task.context_packet.packet_id, sentence.id)
            target_segments.append(
                TranslationTargetSegment(
                    temp_id=temp_id,
                    text_zh=f"ZH::{sentence.normalized_text or sentence.source_text}",
                    segment_type="sentence",
                    source_sentence_ids=[sentence.id],
                    confidence=0.75,
                )
            )
            alignments.append(
                AlignmentSuggestion(
                    source_sentence_ids=[sentence.id],
                    target_temp_ids=[temp_id],
                    relation_type="1:1",
                    confidence=0.95,
                )
            )

        return TranslationWorkerResult(
            output=TranslationWorkerOutput(
                packet_id=task.context_packet.packet_id,
                target_segments=target_segments,
                alignment_suggestions=alignments,
            ),
            usage=TranslationUsage(),
        )


class LLMTranslationWorker:
    """Structured LLM translation worker.

    This keeps prompt construction and provider I/O behind a stable contract so
    real providers can replace the placeholder worker without touching the
    persistence and QA pipeline.
    """

    def __init__(
        self,
        client: TranslationModelClient,
        *,
        model_name: str,
        prompt_version: str = "p0.llm.v1",
        runtime_config: dict[str, Any] | None = None,
    ):
        self.client = client
        self._metadata = TranslationWorkerMetadata(
            worker_name=self.__class__.__name__,
            model_name=model_name,
            prompt_version=prompt_version,
            runtime_config=runtime_config or {},
        )

    def metadata(self) -> TranslationWorkerMetadata:
        return self._metadata

    def translate(self, task: TranslationTask) -> TranslationWorkerResult | TranslationWorkerOutput:
        request = build_translation_prompt_request(
            task,
            model_name=self._metadata.model_name,
            prompt_version=self._metadata.prompt_version,
        )
        payload = self.client.generate_translation(request)
        return self._remap_sentence_aliases(payload, request)

    def _remap_sentence_aliases(
        self,
        payload: TranslationWorkerResult | TranslationWorkerOutput,
        request: TranslationPromptRequest,
    ) -> TranslationWorkerResult | TranslationWorkerOutput:
        alias_map = request.sentence_alias_map or {}
        if not alias_map:
            return payload

        valid_sentence_ids = set(alias_map.values())

        def _normalize_sentence_ids(values: list[str]) -> list[str]:
            normalized: list[str] = []
            for value in values:
                resolved = alias_map.get(value, value)
                if resolved in valid_sentence_ids and resolved not in normalized:
                    normalized.append(resolved)
            return normalized

        def _normalize_output(output: TranslationWorkerOutput) -> TranslationWorkerOutput:
            return output.model_copy(
                update={
                    "target_segments": [
                        segment.model_copy(
                            update={"source_sentence_ids": _normalize_sentence_ids(segment.source_sentence_ids)}
                        )
                        for segment in output.target_segments
                    ],
                    "alignment_suggestions": [
                        suggestion.model_copy(
                            update={"source_sentence_ids": _normalize_sentence_ids(suggestion.source_sentence_ids)}
                        )
                        for suggestion in output.alignment_suggestions
                    ],
                    "low_confidence_flags": [
                        flag.model_copy(
                            update={"sentence_id": alias_map.get(flag.sentence_id, flag.sentence_id)}
                        )
                        for flag in output.low_confidence_flags
                        if alias_map.get(flag.sentence_id, flag.sentence_id) in valid_sentence_ids
                    ],
                }
            )

        if isinstance(payload, TranslationWorkerResult):
            return payload.model_copy(update={"output": _normalize_output(payload.output)})
        return _normalize_output(payload)
