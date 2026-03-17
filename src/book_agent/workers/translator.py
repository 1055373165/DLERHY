from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

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


def _format_section(title: str, lines: list[str]) -> list[str]:
    return [title, *lines]


def _sorted_term_lines(packet: ContextPacket) -> list[str]:
    if not packet.relevant_terms:
        return ["- none"]
    ordered = sorted(
        packet.relevant_terms,
        key=lambda term: (
            {"locked": 0, "preferred": 1, "suggested": 2}.get(term.lock_level, 99),
            term.source_term.lower(),
            term.target_term.lower(),
        ),
    )
    return [f"- {term.source_term} => {term.target_term} ({term.lock_level})" for term in ordered]


def _sorted_entity_lines(packet: ContextPacket) -> list[str]:
    if not packet.relevant_entities:
        return ["- none"]
    ordered = sorted(
        packet.relevant_entities,
        key=lambda entity: (entity.entity_type.lower(), entity.name.lower()),
    )
    return [
        f"- {entity.name} [{entity.entity_type}] => {entity.canonical_zh or '(unset)'}"
        for entity in ordered
    ]


def _concept_lines(packet: ContextPacket) -> list[str]:
    if not packet.chapter_concepts:
        return ["- none"]
    ordered = sorted(
        packet.chapter_concepts,
        key=lambda concept: (
            0 if concept.canonical_zh else 1,
            -(concept.times_seen or 0),
            concept.source_term.lower(),
        ),
    )
    lines: list[str] = []
    for concept in ordered:
        canonical = concept.canonical_zh or "(translation not locked yet)"
        details = [concept.status]
        if concept.times_seen:
            details.append(f"seen={concept.times_seen}")
        lines.append(f"- {concept.source_term} => {canonical} ({', '.join(details)})")
    return lines


def _packet_block_lines(blocks: list[PacketBlock]) -> list[str]:
    if not blocks:
        return ["- none"]
    lines: list[str] = []
    for index, block in enumerate(blocks, start=1):
        excerpt = " ".join((block.text or "").split())
        lines.append(f"- B{index} [{block.block_type}] {excerpt}")
    return lines


def _current_paragraph_lines(packet: ContextPacket) -> list[str]:
    if not packet.current_blocks:
        return ["- none"]
    lines: list[str] = []
    for index, block in enumerate(packet.current_blocks, start=1):
        lines.append(f"- P{index} [{block.block_type}] {block.text}")
    return lines


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


PromptLayout = Literal["paragraph-led", "sentence-led"]
PromptProfile = Literal["current", "role-style-v2", "role-style-memory-v2"]


class TranslationModelClient(Protocol):
    def generate_translation(self, request: TranslationPromptRequest) -> TranslationWorkerResult | TranslationWorkerOutput:
        ...


def build_translation_prompt_request(
    task: TranslationTask,
    *,
    model_name: str,
    prompt_version: str,
    prompt_layout: PromptLayout = "paragraph-led",
    prompt_profile: PromptProfile = "current",
) -> TranslationPromptRequest:
    packet = task.context_packet
    heading_path = " > ".join(packet.heading_path) if packet.heading_path else "(root)"
    term_lines = _sorted_term_lines(packet)
    entity_lines = _sorted_entity_lines(packet)
    concept_lines = _concept_lines(packet)
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
    previous_translation_lines = [
        f"- {item.source_excerpt} => {item.target_excerpt}"
        for item in packet.prev_translated_blocks
    ] or ["- none"]
    prev_block_lines = _packet_block_lines(packet.prev_blocks)
    next_block_lines = _packet_block_lines(packet.next_blocks)
    current_paragraph_lines = _current_paragraph_lines(packet)
    contract_lines = [
        "- Translate the current paragraph into natural Chinese at paragraph level first, then ensure sentence-level coverage is complete.",
        "- Reuse the canonical Chinese rendering of any locked or previously established concept.",
        "- If a concept already appears in Previous Accepted Translations, keep the same Chinese term unless the current packet explicitly redefines it.",
        "- Preserve meaning, preserve protected spans, and maintain complete alignment coverage.",
        "- Use the sentence ledger only for alignment, coverage, and low-confidence flags.",
        "- Use only the sentence aliases shown below (for example: S1, S2) in source_sentence_ids and low_confidence_flags.sentence_id.",
        "- Return JSON that matches the provided response schema.",
    ]
    if prompt_layout == "sentence-led":
        contract_lines[0] = (
            "- Translate every current sentence faithfully into Chinese, but keep paragraph-level coherence across the full packet."
        )
        contract_lines[4] = "- Use the current sentences as the primary translation ledger and keep paragraph context coherent."

    style_lines: list[str] = []
    memory_handling_lines: list[str] = []
    if prompt_profile in {"role-style-v2", "role-style-memory-v2"}:
        style_lines = [
            "- Write like a polished Chinese technical translator, not a sentence-by-sentence converter.",
            "- Prefer established Chinese technical phrasing and avoid literal calques of English abstract noun chains.",
            "- Keep terminology stable across the packet and maintain a professional, readable register.",
            "- Preserve rhetorical emphasis, but do not over-fragment paragraphs unless the source clearly intends it.",
        ]
    if prompt_profile == "role-style-memory-v2":
        memory_handling_lines = [
            "- Treat Locked and Relevant Terms as authoritative whenever they match the source.",
            "- Treat locked Chapter Concept Memory as the default rendering for recurring concepts unless the current packet explicitly redefines them.",
            "- Use Previous Accepted Translations to continue local discourse and terminology continuity across paragraphs.",
            "- If wording remains ambiguous or risky, keep the translated body clean and report the uncertainty only via structured low_confidence_flags or notes.",
        ]

    sections = [
        *_format_section("Core Translation Contract:", contract_lines),
    ]
    if style_lines:
        sections.extend(_format_section("Chinese Style Priorities:", style_lines))
    if memory_handling_lines:
        sections.extend(_format_section("Memory and Ambiguity Handling:", memory_handling_lines))
    sections.extend(
        [
        *_format_section(
            "Section Context:",
            [
                f"- Heading Path: {heading_path}",
                f"- Chapter Brief: {packet.chapter_brief or '(none)'}",
            ],
        ),
        *_format_section("Locked and Relevant Terms:", term_lines),
        *_format_section("Relevant Entities:", entity_lines),
        *_format_section("Chapter Concept Memory:", concept_lines),
        *_format_section("Previous Accepted Translations (same local context):", previous_translation_lines),
        *_format_section("Previous Source Context:", prev_block_lines),
        *_format_section("Upcoming Source Context:", next_block_lines),
    ])
    if prompt_layout == "sentence-led":
        sections.extend(
            [
                *_format_section("Current Sentences:", sentence_lines),
                *_format_section("Current Paragraph:", current_paragraph_lines),
            ]
        )
    else:
        sections.extend(
            [
                *_format_section("Current Paragraph:", current_paragraph_lines),
                *_format_section("Sentence Ledger:", sentence_lines),
            ]
        )
    user_prompt = "\n".join(sections)
    if prompt_profile == "current":
        system_prompt = (
            "You are a high-fidelity book translation worker. "
            "Translate English book content into natural Chinese with paragraph-level coherence, "
            "preserve meaning, respect locked terms, and do not translate protected spans. "
            "You may reorganize sentence structure, but alignment coverage must remain complete."
        )
    elif prompt_profile == "role-style-v2":
        system_prompt = (
            "You are a senior technical translator and localizer for English-to-Chinese books, papers, and business documents. "
            "Produce accurate, professional, publication-grade Chinese that preserves structure and terminology consistency. "
            "Prefer natural Chinese technical prose over literal sentence mirroring, while keeping alignment coverage complete."
        )
    else:
        system_prompt = (
            "You are a senior English-to-Chinese technical translator working inside a structured translation system. "
            "Translate with paragraph-first coherence, authoritative use of locked terms and chapter concept memory, and clean professional Chinese. "
            "Keep the translated body free of inline translator notes; report uncertainty only through structured notes or low-confidence flags. "
            "Alignment coverage must remain complete."
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
