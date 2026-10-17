"""Translation contracts: context packets sent to workers and structured worker output."""

from typing import Any, Literal

from pydantic import Field

from book_agent.schemas.common import BaseSchema


class PacketBlock(BaseSchema):
    block_id: str
    block_type: str
    sentence_ids: list[str]
    text: str


class RelevantTerm(BaseSchema):
    source_term: str
    target_term: str
    lock_level: str


class RelevantEntity(BaseSchema):
    name: str
    entity_type: str
    canonical_zh: str | None = None
    aliases: list[str] = Field(default_factory=list)


class ProtectedSpan(BaseSchema):
    source_sentence_id: str
    start: int
    end: int
    reason: str


class TranslatedContextBlock(BaseSchema):
    block_id: str
    source_excerpt: str
    target_excerpt: str
    source_sentence_ids: list[str] = Field(default_factory=list)


class ConceptCandidate(BaseSchema):
    source_term: str
    canonical_zh: str | None = None
    status: str = "candidate"
    confidence: float | None = None
    first_seen_packet_id: str | None = None
    last_seen_packet_id: str | None = None
    times_seen: int = 1


class DiscourseBridge(BaseSchema):
    previous_paragraph_role: str | None = None
    current_paragraph_role: str | None = None
    relation_to_previous: str | None = None
    active_referents: list[str] = Field(default_factory=list)


class ContextPacket(BaseSchema):
    packet_id: str
    document_id: str
    chapter_id: str
    packet_type: Literal["translate", "retranslate", "review"]
    book_profile_version: int
    chapter_brief_version: int | None = None
    packet_ordinal: int | None = None
    chapter_memory_version: int | None = None
    heading_path: list[str] = Field(default_factory=list)
    current_blocks: list[PacketBlock]
    prev_blocks: list[PacketBlock] = Field(default_factory=list)
    next_blocks: list[PacketBlock] = Field(default_factory=list)
    prev_translated_blocks: list[TranslatedContextBlock] = Field(default_factory=list)
    chapter_concepts: list[ConceptCandidate] = Field(default_factory=list)
    relevant_terms: list[RelevantTerm] = Field(default_factory=list)
    relevant_entities: list[RelevantEntity] = Field(default_factory=list)
    protected_spans: list[ProtectedSpan] = Field(default_factory=list)
    chapter_brief: str | None = None
    section_brief: str | None = None
    discourse_bridge: DiscourseBridge | None = None
    style_constraints: dict[str, str | bool | int | float] = Field(default_factory=dict)
    open_questions: list[str] = Field(default_factory=list)
    input_version_bundle: dict[str, Any] = Field(default_factory=dict)
    runtime_state: dict[str, Any] = Field(default_factory=dict)
    budget_hint: dict[str, int] = Field(default_factory=dict)


class CompiledTranslationContext(ContextPacket):
    source_packet_id: str
    context_compile_version: str
    memory_version_used: int | None = None
    compile_metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_context_packet(
        cls,
        packet: ContextPacket,
        *,
        context_compile_version: str,
        memory_version_used: int | None,
        compile_metadata: dict[str, Any] | None = None,
    ) -> "CompiledTranslationContext":
        payload = packet.model_dump(mode="python")
        payload.update(
            {
                "source_packet_id": packet.packet_id,
                "context_compile_version": context_compile_version,
                "memory_version_used": memory_version_used,
                "compile_metadata": dict(compile_metadata or {}),
            }
        )
        return cls.model_validate(payload)


class TranslationTargetSegment(BaseSchema):
    temp_id: str
    text_zh: str
    segment_type: str
    source_sentence_ids: list[str]
    confidence: float | None = None


class AlignmentSuggestion(BaseSchema):
    source_sentence_ids: list[str]
    target_temp_ids: list[str]
    relation_type: str
    confidence: float | None = None


class LowConfidenceFlag(BaseSchema):
    sentence_id: str
    reason: str


class TranslationNote(BaseSchema):
    type: str
    message: str


class TranslationWorkerOutput(BaseSchema):
    packet_id: str
    target_segments: list[TranslationTargetSegment]
    alignment_suggestions: list[AlignmentSuggestion]
    low_confidence_flags: list[LowConfidenceFlag] = Field(default_factory=list)
    notes: list[TranslationNote] = Field(default_factory=list)


class TranslationModelReply(BaseSchema):
    """What the model writes in the slim reply format: only what it alone knows.

    The packet id is the caller's, and the alignment is each segment's ``source_sentence_ids``:
    asking the model to copy them back was about half of its output tokens
    (docs/specs/cost-governance/SPEC.md, E4). ``complete_model_reply`` makes the full output.
    """

    target_segments: list[TranslationTargetSegment]
    low_confidence_flags: list[LowConfidenceFlag] = Field(default_factory=list)
    notes: list[TranslationNote] = Field(default_factory=list)


REPLY_FORMATS = ("full", "slim")


def _join_sourceless_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A segment that names no sentence joins the next one (or the one before, at the end).

    Without an alignment it would never be shown: How Large Language Models Work's "WARNING We use the
    word 'thinking' loosely..." came back as "警告" alone, and the reading copy lost the label.
    """
    if not any(not (segment.get("source_sentence_ids") or []) for segment in segments):
        return segments
    joined: list[dict[str, Any]] = []
    carry = ""
    for segment in segments:
        text = str(segment.get("text_zh") or "")
        if not (segment.get("source_sentence_ids") or []):
            carry = f"{carry} {text}".strip() if carry else text
            continue
        if carry:
            segment = {**segment, "text_zh": f"{carry} {text}".strip()}
            carry = ""
        joined.append(segment)
    if carry and joined:
        joined[-1] = {**joined[-1], "text_zh": f"{joined[-1].get('text_zh') or ''} {carry}".strip()}
    return joined


def complete_model_reply(payload: dict[str, Any], *, packet_id: str) -> dict[str, Any]:
    """The full output from a reply that left out the packet id or the alignment.

    Each segment aligns to the sentences it names: one sentence in one segment is 1:1, several
    sentences in one segment n:1, one sentence split over several segments 1:n. A reply that
    carries its own alignment keeps it.
    """
    completed = dict(payload)
    if not completed.get("packet_id"):
        completed["packet_id"] = packet_id
    if "alignment_suggestions" in completed and completed["alignment_suggestions"]:
        return completed
    segments = _join_sourceless_segments(
        [segment for segment in completed.get("target_segments") or [] if isinstance(segment, dict)]
    )
    completed["target_segments"] = segments
    uses: dict[str, int] = {}
    for segment in segments:
        for sentence_id in segment.get("source_sentence_ids") or []:
            uses[str(sentence_id)] = uses.get(str(sentence_id), 0) + 1
    suggestions = []
    for segment in segments:
        sources = [str(sentence_id) for sentence_id in segment.get("source_sentence_ids") or []]
        if not sources or not segment.get("temp_id"):
            continue
        if len(sources) > 1:
            relation = "n:1"
        elif uses.get(sources[0], 0) > 1:
            relation = "1:n"
        else:
            relation = "1:1"
        suggestion = {"source_sentence_ids": sources, "target_temp_ids": [segment["temp_id"]], "relation_type": relation}
        if segment.get("confidence") is not None:
            suggestion["confidence"] = segment["confidence"]
        suggestions.append(suggestion)
    completed["alignment_suggestions"] = suggestions
    return completed


class TranslationUsage(BaseSchema):
    token_in: int = 0
    token_out: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
    cost_usd: float | None = None
    provider_request_id: str | None = None
    raw_usage: dict[str, Any] = Field(default_factory=dict)
    # The input tokens the provider's prompt cache served (hit) or not (miss); None when unknown.
    prompt_cache_hit_tokens: int | None = None
    prompt_cache_miss_tokens: int | None = None


class TranslationWorkerResult(BaseSchema):
    output: TranslationWorkerOutput
    usage: TranslationUsage = Field(default_factory=TranslationUsage)
    # Filled by TranslationService.call_worker: outputs the guardrail rejected
    # before this one was accepted (or before the repair budget ran out).
    rejected_outputs: list[dict[str, Any]] = Field(default_factory=list)
    # Filled by TranslationService.call_worker when the polish pass ran (see translation.polish).
    polish: dict[str, Any] | None = None

    @property
    def packet_id(self) -> str:
        return self.output.packet_id

    @property
    def target_segments(self) -> list[TranslationTargetSegment]:
        return self.output.target_segments

    @property
    def alignment_suggestions(self) -> list[AlignmentSuggestion]:
        return self.output.alignment_suggestions

    @property
    def low_confidence_flags(self) -> list[LowConfidenceFlag]:
        return self.output.low_confidence_flags

    @property
    def notes(self) -> list[TranslationNote]:
        return self.output.notes
