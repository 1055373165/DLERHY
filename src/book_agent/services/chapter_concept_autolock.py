from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Protocol

from sqlalchemy.orm import Session

from book_agent.core.config import Settings, get_settings
from book_agent.domain.enums import LockLevel, TargetSegmentStatus, TermStatus
from book_agent.infra.repositories.review import ChapterReviewBundle, ReviewRepository
from book_agent.schemas.common import BaseSchema
from book_agent.services.chapter_concept_lock import ChapterConceptLockResult, ChapterConceptLockService
from book_agent.workers.contracts import TranslationUsage
from book_agent.workers.providers import OpenAICompatibleTranslationClient


class ConceptTranslationExample(BaseSchema):
    source_text: str
    target_text: str


class ConceptResolutionPayload(BaseSchema):
    source_term: str
    canonical_zh: str
    confidence: float | None = None
    rationale: str | None = None


class ConceptResolver(Protocol):
    def resolve(
        self,
        *,
        source_term: str,
        chapter_title: str | None,
        chapter_brief: str | None,
        examples: list[ConceptTranslationExample],
    ) -> tuple[ConceptResolutionPayload | None, TranslationUsage | None]:
        ...


_HEURISTIC_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9\u4e00-\u9fff\-]{2,}")
_HEURISTIC_COMMON_STOP_PHRASES = {
    "译文",
    "本质上",
    "这一更广泛的挑战正被一些人称为",
    "这更广泛的挑战正被一些人称为",
}
_HEURISTIC_GENERIC_TECH_NOUNS = {
    "工程",
    "模型",
    "系统",
    "方法",
    "模式",
}
_HEURISTIC_ANCHOR_PATTERNS = (
    re.compile(r"(?:称为|称作|叫做)(?P<term>[A-Za-z0-9\u4e00-\u9fff\-]{2,16})"),
    re.compile(r"^(?P<term>[A-Za-z0-9\u4e00-\u9fff\-]{2,16})::"),
    re.compile(
        r"^(?P<term>[A-Za-z0-9\u4e00-\u9fff\-]{2,16})(?:通过|也会|会|能够|可以|决定|决定了|指的是|依赖于|依赖|成为|是|并非|不是|构成|提供)"
    ),
)
_PLURAL_VARIANT_SUFFIX_RULES = (
    ("ies", "y"),
    ("s", ""),
)


@dataclass(slots=True)
class HeuristicConceptResolver:
    def resolve(
        self,
        *,
        source_term: str,
        chapter_title: str | None,
        chapter_brief: str | None,
        examples: list[ConceptTranslationExample],
    ) -> tuple[ConceptResolutionPayload | None, TranslationUsage | None]:
        target_texts = [self._normalize_text(example.target_text) for example in examples if example.target_text]
        if not target_texts:
            return None, None

        candidate = self._candidate_from_anchor_patterns(target_texts)
        if candidate is None:
            candidate = self._candidate_from_common_substring(target_texts)
        if candidate is None:
            candidate = self._candidate_from_consensus_tokens(target_texts)
        if candidate is None:
            return None, None
        if self._is_overly_generic_candidate(source_term, candidate):
            return None, None

        return (
            ConceptResolutionPayload(
                source_term=source_term,
                canonical_zh=candidate,
                confidence=0.9,
                rationale="Heuristic consensus from aligned translated examples.",
            ),
            None,
        )

    def _candidate_from_anchor_patterns(self, target_texts: list[str]) -> str | None:
        extracted: list[str] = []
        for text in target_texts:
            candidate = None
            for pattern in _HEURISTIC_ANCHOR_PATTERNS:
                match = pattern.search(text)
                if match is None:
                    continue
                term = self._normalize_candidate(match.group("term"))
                if self._is_viable_candidate(term):
                    candidate = term
                    break
            if candidate is None:
                return None
            extracted.append(candidate)
        if not extracted:
            return None
        unique = {item for item in extracted}
        if len(unique) != 1:
            return None
        return extracted[0]

    def _candidate_from_common_substring(self, target_texts: list[str]) -> str | None:
        common = target_texts[0]
        for text in target_texts[1:]:
            common = self._longest_common_substring(common, text)
            if not common:
                return None
        return self._extract_best_token(common)

    def _candidate_from_consensus_tokens(self, target_texts: list[str]) -> str | None:
        token_counts: dict[str, int] = {}
        for text in target_texts:
            tokens = {
                token
                for token in _HEURISTIC_TOKEN_PATTERN.findall(text)
                if self._is_viable_candidate(token)
            }
            for token in tokens:
                token_counts[token] = token_counts.get(token, 0) + 1
        required_count = len(target_texts)
        candidates = [token for token, count in token_counts.items() if count >= required_count]
        if not candidates:
            return None
        candidates.sort(key=lambda item: (-len(item), item))
        return candidates[0]

    def _extract_best_token(self, text: str) -> str | None:
        candidates = [
            self._normalize_candidate(token)
            for token in _HEURISTIC_TOKEN_PATTERN.findall(text)
            if self._is_viable_candidate(self._normalize_candidate(token))
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda item: (-len(item), item))
        return candidates[0]

    def _is_viable_candidate(self, token: str) -> bool:
        normalized = self._normalize_candidate(token)
        if not normalized:
            return False
        if normalized in _HEURISTIC_COMMON_STOP_PHRASES:
            return False
        if len(normalized) < 2 or len(normalized) > 16:
            return False
        return any("\u4e00" <= char <= "\u9fff" for char in normalized)

    def _is_overly_generic_candidate(self, source_term: str, candidate: str) -> bool:
        source_tokens = [token for token in re.findall(r"[A-Za-z0-9]+", str(source_term or "")) if token]
        normalized_candidate = self._normalize_candidate(candidate)
        if len(source_tokens) < 2:
            return False
        if normalized_candidate in _HEURISTIC_GENERIC_TECH_NOUNS:
            return True
        return len(normalized_candidate) <= 2

    def _normalize_text(self, text: str) -> str:
        return " ".join(str(text or "").split())

    def _normalize_candidate(self, token: str) -> str:
        return str(token or "").strip(" -_:;,.()[]{}<>\"'")

    def _longest_common_substring(self, left: str, right: str) -> str:
        if not left or not right:
            return ""
        rows = len(left) + 1
        cols = len(right) + 1
        matrix = [[0] * cols for _ in range(rows)]
        best_length = 0
        best_end = 0
        for i, left_char in enumerate(left, start=1):
            for j, right_char in enumerate(right, start=1):
                if left_char != right_char:
                    continue
                matrix[i][j] = matrix[i - 1][j - 1] + 1
                if matrix[i][j] > best_length:
                    best_length = matrix[i][j]
                    best_end = i
        return left[best_end - best_length : best_end]


@dataclass(slots=True)
class FallbackConceptResolver:
    resolvers: tuple[ConceptResolver, ...]

    def resolve(
        self,
        *,
        source_term: str,
        chapter_title: str | None,
        chapter_brief: str | None,
        examples: list[ConceptTranslationExample],
    ) -> tuple[ConceptResolutionPayload | None, TranslationUsage | None]:
        for resolver in self.resolvers:
            resolution, usage = resolver.resolve(
                source_term=source_term,
                chapter_title=chapter_title,
                chapter_brief=chapter_brief,
                examples=examples,
            )
            if resolution is not None and resolution.canonical_zh:
                return resolution, usage
        return None, None


@dataclass(slots=True)
class OpenAICompatibleConceptResolver:
    client: OpenAICompatibleTranslationClient
    model_name: str

    def resolve(
        self,
        *,
        source_term: str,
        chapter_title: str | None,
        chapter_brief: str | None,
        examples: list[ConceptTranslationExample],
    ) -> tuple[ConceptResolutionPayload | None, TranslationUsage | None]:
        payload, usage = self.client.generate_structured_object(
            model_name=self.model_name,
            system_prompt=(
                "You normalize recurring English technical concepts into a single canonical Chinese rendering. "
                "Prefer the natural Chinese term already supported by the chapter's translated examples. "
                "Keep the canonical term concise, publication-ready, and stable across singular/plural variants. "
                "Return exactly one JSON object."
            ),
            user_prompt=self._build_user_prompt(
                source_term=source_term,
                chapter_title=chapter_title,
                chapter_brief=chapter_brief,
                examples=examples,
            ),
            response_schema=ConceptResolutionPayload.model_json_schema(),
            schema_name="concept_resolution",
        )
        if "source_term" not in payload or not str(payload.get("source_term") or "").strip():
            payload = {
                **payload,
                "source_term": source_term,
            }
        resolution = ConceptResolutionPayload.model_validate(payload)
        canonical_zh = " ".join((resolution.canonical_zh or "").split()).strip(" ,;:()[]{}<>\"'")
        if not canonical_zh:
            return None, usage
        return (
            resolution.model_copy(
                update={
                    "source_term": source_term,
                    "canonical_zh": canonical_zh,
                }
            ),
            usage,
        )

    def _build_user_prompt(
        self,
        *,
        source_term: str,
        chapter_title: str | None,
        chapter_brief: str | None,
        examples: list[ConceptTranslationExample],
    ) -> str:
        example_lines = [
            f"- Source: {example.source_text}\n  Target: {example.target_text}"
            for example in examples
        ] or ["- none"]
        return "\n".join(
            [
                f"Source term: {source_term}",
                f"Chapter title: {chapter_title or '(unknown)'}",
                f"Chapter brief: {chapter_brief or '(none)'}",
                "Aligned translation examples:",
                *example_lines,
                "Requirements:",
                "- Pick the best canonical Chinese term for the recurring concept above.",
                "- Prefer terminology that already appears in the translated examples.",
                "- Singular/plural variants should share one canonical Chinese term when they represent the same concept.",
                "- Keep canonical_zh concise. Do not include explanations inside canonical_zh.",
                "- rationale should be one short sentence.",
            ]
        )


def build_default_concept_resolver(settings: Settings | None = None) -> ConceptResolver:
    effective_settings = settings or get_settings()
    heuristic = HeuristicConceptResolver()
    backend = effective_settings.translation_backend.lower().strip()
    if backend != "openai_compatible" or not effective_settings.translation_openai_api_key:
        return heuristic
    return FallbackConceptResolver(
        resolvers=(
            OpenAICompatibleConceptResolver(
                client=OpenAICompatibleTranslationClient(
                    api_key=effective_settings.translation_openai_api_key,
                    base_url=effective_settings.translation_openai_base_url,
                    timeout_seconds=effective_settings.translation_timeout_seconds,
                    max_retries=effective_settings.translation_max_retries,
                    retry_backoff_seconds=effective_settings.translation_retry_backoff_seconds,
                    input_cache_hit_cost_per_1m_tokens=effective_settings.translation_input_cache_hit_cost_per_1m_tokens,
                    input_cost_per_1m_tokens=effective_settings.translation_input_cost_per_1m_tokens,
                    output_cost_per_1m_tokens=effective_settings.translation_output_cost_per_1m_tokens,
                ),
                model_name=effective_settings.translation_model,
            ),
            heuristic,
        )
    )


@dataclass(slots=True)
class ConceptAutoLockRecord:
    source_term: str
    canonical_zh: str
    confidence: float | None
    rationale: str | None
    snapshot_version: int
    created_new_concept: bool
    term_entry_id: str
    term_entry_version: int
    created_new_term_entry: bool
    token_in: int = 0
    token_out: int = 0
    cost_usd: float | None = None
    latency_ms: int = 0


@dataclass(slots=True)
class ChapterConceptAutoLockArtifacts:
    chapter_id: str
    requested_source_terms: list[str]
    locked_records: list[ConceptAutoLockRecord]
    skipped_source_terms: list[str]


@dataclass(slots=True)
class ChapterConceptAutoLockService:
    session: Session
    resolver: ConceptResolver | None = None
    review_repository: ReviewRepository = field(init=False)
    lock_service: ChapterConceptLockService = field(init=False)

    def __post_init__(self) -> None:
        if self.resolver is None:
            self.resolver = build_default_concept_resolver()
        self.review_repository = ReviewRepository(self.session)
        self.lock_service = ChapterConceptLockService(self.session)

    def auto_lock_chapter_concepts(
        self,
        chapter_id: str,
        *,
        source_terms: list[str] | None = None,
        min_times_seen: int = 2,
        max_examples_per_term: int = 5,
    ) -> ChapterConceptAutoLockArtifacts:
        bundle = self.review_repository.load_chapter_bundle(chapter_id)
        requested_source_terms = source_terms or self._candidate_source_terms(bundle, min_times_seen=min_times_seen)
        ordered_terms = self._ordered_unique_terms(requested_source_terms)
        observed_source_terms = {
            term.source_term.casefold()
            for term in bundle.term_entries
            if term.status == TermStatus.ACTIVE and term.source_term
        } | {term.casefold() for term in ordered_terms}
        locked_variants = self._locked_variant_terms(bundle, observed_source_terms)

        locked_records: list[ConceptAutoLockRecord] = []
        skipped_source_terms: list[str] = []
        for source_term in ordered_terms:
            variant_key = self._variant_key_for_source_term(source_term, observed_source_terms)
            examples = self._examples_for_source_term(
                bundle,
                source_term,
                limit=max_examples_per_term,
            )
            if not examples:
                skipped_source_terms.append(source_term)
                continue
            reused_canonical = locked_variants.get(variant_key)
            if reused_canonical:
                resolution = ConceptResolutionPayload(
                    source_term=source_term,
                    canonical_zh=reused_canonical,
                    confidence=1.0,
                    rationale="Reused canonical Chinese from an existing singular/plural sibling term.",
                )
                usage = None
            else:
                resolution, usage = self.resolver.resolve(
                    source_term=source_term,
                    chapter_title=bundle.chapter.title_src,
                    chapter_brief=str((bundle.chapter_brief.content_json or {}).get("summary") or "").strip()
                    if bundle.chapter_brief is not None
                    else None,
                    examples=examples,
                )
            if resolution is None or not resolution.canonical_zh:
                skipped_source_terms.append(source_term)
                continue
            lock_result = self.lock_service.lock_concept(
                chapter_id=chapter_id,
                source_term=resolution.source_term,
                canonical_zh=resolution.canonical_zh,
            )
            locked_variants[variant_key] = lock_result.canonical_zh
            locked_records.append(self._record_from_resolution(lock_result, resolution, usage))

        return ChapterConceptAutoLockArtifacts(
            chapter_id=chapter_id,
            requested_source_terms=ordered_terms,
            locked_records=locked_records,
            skipped_source_terms=skipped_source_terms,
        )

    def _candidate_source_terms(
        self,
        bundle: ChapterReviewBundle,
        *,
        min_times_seen: int,
    ) -> list[str]:
        snapshot = bundle.chapter_translation_memory
        if snapshot is None:
            return []
        active_concepts = snapshot.content_json.get("active_concepts", [])
        if not isinstance(active_concepts, list):
            return []

        locked_term_sources = {
            term.source_term.casefold()
            for term in bundle.term_entries
            if term.status == TermStatus.ACTIVE
            and term.lock_level == LockLevel.LOCKED
            and term.source_term
        }

        source_terms: list[str] = []
        for concept in active_concepts:
            if not isinstance(concept, dict):
                continue
            source_term = str(concept.get("source_term") or "").strip()
            if not source_term:
                continue
            if concept.get("canonical_zh"):
                continue
            mention_count = int(concept.get("mention_count") or concept.get("times_seen") or 0)
            if mention_count < min_times_seen:
                continue
            if source_term.casefold() in locked_term_sources:
                continue
            source_terms.append(source_term)
        return source_terms

    def _examples_for_source_term(
        self,
        bundle: ChapterReviewBundle,
        source_term: str,
        *,
        limit: int,
    ) -> list[ConceptTranslationExample]:
        lowered = source_term.casefold()
        active_target_map = {
            segment.id: segment
            for segment in bundle.target_segments
            if segment.final_status != TargetSegmentStatus.SUPERSEDED
        }
        alignments_by_sentence: dict[str, list[str]] = {}
        for edge in bundle.alignment_edges:
            if edge.target_segment_id not in active_target_map:
                continue
            alignments_by_sentence.setdefault(edge.sentence_id, []).append(edge.target_segment_id)

        examples: list[ConceptTranslationExample] = []
        seen_pairs: set[tuple[str, str]] = set()
        for sentence in sorted(bundle.sentences, key=lambda item: (str(item.block_id), item.ordinal_in_block, item.id)):
            source_text = str(sentence.source_text or "").strip()
            if not source_text or lowered not in source_text.casefold():
                continue
            target_text = " ".join(
                active_target_map[target_id].text_zh
                for target_id in alignments_by_sentence.get(sentence.id, [])
                if target_id in active_target_map and active_target_map[target_id].text_zh
            ).strip()
            if not target_text:
                continue
            pair = (source_text, target_text)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            examples.append(
                ConceptTranslationExample(
                    source_text=source_text,
                    target_text=target_text,
                )
            )
            if len(examples) >= limit:
                break
        return examples

    def _ordered_unique_terms(self, source_terms: list[str]) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        for source_term in source_terms:
            normalized = str(source_term or "").strip()
            if not normalized:
                continue
            key = normalized.casefold()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(normalized)
        return ordered

    def _locked_variant_terms(
        self,
        bundle: ChapterReviewBundle,
        observed_source_terms: set[str],
    ) -> dict[str, str]:
        locked_variants: dict[str, str] = {}
        for term in bundle.term_entries:
            if term.status != TermStatus.ACTIVE or term.lock_level != LockLevel.LOCKED:
                continue
            source_term = str(term.source_term or "").strip()
            target_term = str(term.target_term or "").strip()
            if not source_term or not target_term:
                continue
            locked_variants[self._variant_key_for_source_term(source_term, observed_source_terms)] = target_term
        return locked_variants

    def _variant_key_for_source_term(
        self,
        source_term: str,
        observed_source_terms: set[str],
    ) -> str:
        normalized = " ".join(str(source_term or "").casefold().split())
        if not normalized:
            return normalized
        words = normalized.split()
        if not words:
            return normalized
        last_word = words[-1]
        for suffix, replacement in _PLURAL_VARIANT_SUFFIX_RULES:
            if not last_word.endswith(suffix) or len(last_word) <= len(suffix):
                continue
            candidate = " ".join(words[:-1] + [last_word[: -len(suffix)] + replacement])
            if candidate and candidate in observed_source_terms:
                return candidate
        return normalized

    def _record_from_resolution(
        self,
        lock_result: ChapterConceptLockResult,
        resolution: ConceptResolutionPayload,
        usage: TranslationUsage | None,
    ) -> ConceptAutoLockRecord:
        return ConceptAutoLockRecord(
            source_term=lock_result.source_term,
            canonical_zh=lock_result.canonical_zh,
            confidence=resolution.confidence,
            rationale=resolution.rationale,
            snapshot_version=lock_result.snapshot_version,
            created_new_concept=lock_result.created_new_concept,
            term_entry_id=lock_result.term_entry_id,
            term_entry_version=lock_result.term_entry_version,
            created_new_term_entry=lock_result.created_new_term_entry,
            token_in=usage.token_in if usage is not None else 0,
            token_out=usage.token_out if usage is not None else 0,
            cost_usd=usage.cost_usd if usage is not None else None,
            latency_ms=usage.latency_ms if usage is not None else 0,
        )
