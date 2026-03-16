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


class ContextPacket(BaseSchema):
    packet_id: str
    document_id: str
    chapter_id: str
    packet_type: Literal["translate", "retranslate", "review"]
    book_profile_version: int
    chapter_brief_version: int | None = None
    heading_path: list[str] = Field(default_factory=list)
    current_blocks: list[PacketBlock]
    prev_blocks: list[PacketBlock] = Field(default_factory=list)
    next_blocks: list[PacketBlock] = Field(default_factory=list)
    prev_translated_blocks: list[TranslatedContextBlock] = Field(default_factory=list)
    relevant_terms: list[RelevantTerm] = Field(default_factory=list)
    relevant_entities: list[RelevantEntity] = Field(default_factory=list)
    protected_spans: list[ProtectedSpan] = Field(default_factory=list)
    chapter_brief: str | None = None
    style_constraints: dict[str, str | bool | int | float] = Field(default_factory=dict)
    open_questions: list[str] = Field(default_factory=list)
    budget_hint: dict[str, int] = Field(default_factory=dict)


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


class TranslationUsage(BaseSchema):
    token_in: int = 0
    token_out: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
    cost_usd: float | None = None
    provider_request_id: str | None = None
    raw_usage: dict[str, Any] = Field(default_factory=dict)


class TranslationWorkerResult(BaseSchema):
    output: TranslationWorkerOutput
    usage: TranslationUsage = Field(default_factory=TranslationUsage)

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


class QualitySummary(BaseSchema):
    coverage_ok: bool
    term_consistency_score: float | None = None
    style_drift_score: float | None = None


class ReviewIssueSuggestion(BaseSchema):
    issue_type: str
    severity: str
    sentence_id: str | None = None
    evidence: dict[str, str] = Field(default_factory=dict)
    suggested_action: str | None = None


class ReviewerOutput(BaseSchema):
    chapter_id: str
    quality_summary: QualitySummary
    issues: list[ReviewIssueSuggestion] = Field(default_factory=list)
    rerun_recommendations: list[dict[str, str]] = Field(default_factory=list)
