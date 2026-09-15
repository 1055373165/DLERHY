"""Automatic terminology consistency for a translated document.

No glossary work is asked of the user, and the model never rewrites text:

1. **Extract** candidate terms (``GlossaryExtractionService``).
2. **Survey** every translated segment that contains a term: the model only
   reports the exact span of the Chinese that renders the term, or that the
   word is used in another sense there (an idiom, an everyday meaning). A span
   that does not literally occur in the translation is discarded.
3. **Tally** the surveyed renderings per term. The book's own majority
   rendering becomes canonical. Terms without a clear majority, or with many
   scattered renderings, are treated as context-dependent and left alone.
4. **Replace** minority spans with the canonical rendering by exact string
   replacement, only when the span is unambiguous in its segment. Every change
   is recorded as a ``glossary.updated`` event with the before/after text.
5. **Lock** the canonical terms and **report** strict consistency (exactly the
   canonical rendering) before and after, plus everything that was skipped.

An earlier version let the model edit segments and accept "variants" itself;
it declared different wordings consistent and forced terms into idioms and
sentences that never expressed them. Keeping the model to reporting spans makes
every change and every number checkable against the text.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.domain.enums import TargetSegmentStatus, TermType
from book_agent.domain.event_kinds import GLOSSARY_UPDATED
from book_agent.domain.models import Sentence
from book_agent.domain.models.translation import AlignmentEdge, TargetSegment
from book_agent.domain.terminology.matching import (
    SourceTermIndex,
    normalize_target,
    source_term_key,
)
from book_agent.infra.repositories.events import emit_event
from book_agent.services.glossary_extraction import (
    GlossaryExtractionService,
    GlossarySuggestion,
    StructuredObjectClient,
)
from book_agent.services.glossary_service import GlossaryService

SURVEY_BATCH_SIZE = 16
SURVEY_TEXT_CHARS = 600
MIN_MAJORITY_SHARE = 0.7
MAX_DISTINCT_RENDERINGS = 3
MIN_RENDERING_CHARS = 2
# A minority rendering is only swapped when it shares more than half of its characters
# with the canonical one (失败摆动 / 失败摇摆); different concepts (逆势 / 趋势,
# 放量 / 成交量, 上穿 / 交叉) share little and are left alone.
MIN_SHARED_CHARACTER_RATIO = 0.5
_CJK = re.compile(r"[一-鿿]")

SURVEY_SYSTEM_PROMPT = (
    "You audit terminology in an English-to-Simplified-Chinese book translation. "
    "For each item you get an English passage, its Chinese translation and one English term. "
    "Report how the Chinese renders that term in this passage: rendering_zh must be the exact contiguous "
    "characters copied from the Chinese that translate the term itself (no added or changed characters), "
    "without modifiers, demonstratives or words that translate other parts of the sentence, or an empty "
    "string when the term is not expressed on its own (for example when one Chinese word also carries "
    "another meaning such as a direction or a change). Set sense to \"term\" when the English "
    "word is used in its technical meaning in this field, or \"other\" when it is used in another sense "
    "(an idiom, an everyday meaning, part of a different expression). Do not suggest corrections. "
    'Return one JSON object: {"items": [{"id": str, "rendering_zh": str, "sense": "term" | "other"}]}, '
    "one entry per input item."
)

SURVEY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "rendering_zh": {"type": "string"},
                    "sense": {"type": "string"},
                },
                "required": ["id", "rendering_zh"],
            },
        }
    },
    "required": ["items"],
}


@dataclass(slots=True)
class SegmentUnit:
    segment_id: str
    chapter_id: str
    source_text: str
    text_zh: str
    term_keys: set[str] = field(default_factory=set)


@dataclass(slots=True)
class TermObservation:
    segment_id: str
    rendering: str | None  # exact span in the Chinese; None when not expressed or unusable
    sense: str  # term | other | unknown


@dataclass(slots=True)
class TermDecision:
    key: str
    source_term: str
    term_type: TermType
    canonical_zh: str | None
    rendering_counts: dict[str, int]
    expressed_segments: int
    other_sense_segments: int
    skipped_reason: str | None
    consistent_before: int
    consistent_after: int = 0
    replaced: int = 0
    unreplaced: dict[str, int] = field(default_factory=dict)

    @property
    def harmonized(self) -> bool:
        return self.skipped_reason is None and self.canonical_zh is not None


@dataclass(slots=True)
class TermConsistencyReport:
    document_id: str
    decisions: list[TermDecision]
    edited_segments: int
    locked_term_count: int
    token_in: int
    token_out: int
    applied: bool

    @property
    def harmonized_decisions(self) -> list[TermDecision]:
        return [decision for decision in self.decisions if decision.harmonized]

    def consistency(self, *, after: bool) -> float | None:
        decisions = self.harmonized_decisions
        total = sum(decision.expressed_segments for decision in decisions)
        if not total:
            return None
        consistent = sum(decision.consistent_after if after else decision.consistent_before for decision in decisions)
        return consistent / total


def exact_span(text_zh: str, rendering: str) -> str | None:
    """The rendering if it literally occurs in the translation (surrounding whitespace ignored)."""
    rendering = (rendering or "").strip()
    return rendering if rendering and rendering in text_zh else None


def decide_canonical(
    counts: Counter[str],
    *,
    proposed: str | None,
    min_share: float = MIN_MAJORITY_SHARE,
    max_distinct: int = MAX_DISTINCT_RENDERINGS,
) -> tuple[str | None, str | None]:
    """Return (canonical rendering, skip reason) from surveyed rendering counts."""
    total = sum(counts.values())
    if total < 2:
        return None, "too_few_occurrences"
    if len(counts) > max_distinct:
        return None, "many_renderings_context_dependent"
    ranked = counts.most_common()
    top_count = ranked[0][1]
    leaders = [rendering for rendering, count in ranked if count == top_count]
    if len(leaders) > 1 and proposed not in leaders:
        return None, "no_clear_majority"
    if top_count / total < min_share:
        return None, "no_clear_majority"
    return (proposed if proposed in leaders else leaders[0]), None


def renders_canonical(rendering: str, canonical: str) -> bool:
    """A surveyed span that already counts as the canonical rendering.

    Either it contains the canonical rendering with extra words ("大投资者" for
    "投资者"), or it is a short form contained in it ("日线" for "日线图",
    "倒头肩形" for "倒头肩形态"). Expanding a short form in place produced text
    such as "倒头肩形态等形态" and "日线图RSI".
    """
    normalized_rendering, normalized_canonical = normalize_target(rendering), normalize_target(canonical)
    return bool(normalized_rendering) and (
        normalized_canonical in normalized_rendering or normalized_rendering in normalized_canonical
    )


def shared_character_ratio(first: str, second: str) -> float:
    left, right = Counter(normalize_target(first)), Counter(normalize_target(second))
    shorter = min(sum(left.values()), sum(right.values()))
    return sum((left & right).values()) / shorter if shorter else 0.0


def replace_rendering(text_zh: str, rendering: str, canonical: str, *, expected_occurrences: int) -> str | None:
    """Swap a minority rendering for the canonical one, or None when that is not safe to do blindly."""
    if renders_canonical(rendering, canonical):
        return None  # already consistent; the span only carries extra words
    if len(normalize_target(rendering)) < MIN_RENDERING_CHARS:
        return None  # e.g. a single "图": too many unrelated matches
    if shared_character_ratio(rendering, canonical) <= MIN_SHARED_CHARACTER_RATIO:
        return None  # likely a different concept, not a variant of the term
    if canonical in text_zh and rendering in canonical:
        return None  # the span is part of the canonical rendering already present
    if text_zh.count(rendering) != expected_occurrences:
        return None  # the span also appears outside this term: ambiguous
    return text_zh.replace(rendering, canonical)


def _batched(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _clip(text: str, limit: int = SURVEY_TEXT_CHARS) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class TermConsistencyService:
    def __init__(self, session: Session, client: StructuredObjectClient, *, model_name: str) -> None:
        self.session = session
        self.client = client
        self.model_name = model_name
        self._token_in = 0
        self._token_out = 0

    def run(self, document_id: str, *, apply: bool = True, min_segments: int = 2) -> TermConsistencyReport:
        # Touch the glossary before any provider call: a schema or database problem
        # must fail here, not after every provider call was paid for.
        GlossaryService(self.session).list_document_entries(document_id)
        extraction = GlossaryExtractionService(self.session, self.client, model_name=self.model_name).extract(document_id)
        self._token_in += extraction.token_in
        self._token_out += extraction.token_out

        units = self._load_segment_units(document_id)
        suggestions = {source_term_key(item.source_term): item for item in extraction.suggestions}
        index = SourceTermIndex(item.source_term for item in suggestions.values())
        occurrences_in_unit: dict[tuple[str, str], int] = {}
        for unit in units:
            for occurrence in index.find(unit.source_text):
                unit.term_keys.add(occurrence.key)
                pair = (unit.segment_id, occurrence.key)
                occurrences_in_unit[pair] = occurrences_in_unit.get(pair, 0) + 1
        units_by_key: dict[str, list[SegmentUnit]] = defaultdict(list)
        for unit in units:
            for key in unit.term_keys:
                units_by_key[key].append(unit)
        surveyed_keys = [key for key in suggestions if len(units_by_key.get(key, [])) >= min_segments]

        observations = self._survey(surveyed_keys, suggestions, units_by_key)
        decisions = [self._decide(key, suggestions[key], observations[key]) for key in surveyed_keys]

        edited = self._replace(units, decisions, observations, occurrences_in_unit, apply=apply)

        locked = 0
        if apply:
            glossary = GlossaryService(self.session)
            for decision in decisions:
                if decision.harmonized:
                    glossary.lock_term(document_id, decision.source_term, decision.canonical_zh, term_type=decision.term_type)
                    locked += 1
            self.session.flush()

        return TermConsistencyReport(
            document_id=document_id,
            decisions=sorted(decisions, key=lambda item: (-item.expressed_segments, item.source_term.casefold())),
            edited_segments=edited,
            locked_term_count=locked,
            token_in=self._token_in,
            token_out=self._token_out,
            applied=apply,
        )

    # --- Loading -------------------------------------------------------------

    def _load_segment_units(self, document_id: str) -> list[SegmentUnit]:
        rows = self.session.execute(
            select(TargetSegment.id, TargetSegment.chapter_id, TargetSegment.text_zh, Sentence.source_text)
            .join(AlignmentEdge, AlignmentEdge.target_segment_id == TargetSegment.id)
            .join(Sentence, Sentence.id == AlignmentEdge.sentence_id)
            .where(
                Sentence.document_id == document_id,
                TargetSegment.final_status != TargetSegmentStatus.SUPERSEDED,
            )
            .order_by(TargetSegment.chapter_id, TargetSegment.ordinal, Sentence.ordinal_in_block)
        )
        units: dict[str, SegmentUnit] = {}
        for segment_id, chapter_id, text_zh, source_text in rows:
            unit = units.get(segment_id)
            if unit is None:
                units[segment_id] = SegmentUnit(segment_id, chapter_id, source_text or "", text_zh or "")
            elif source_text and source_text not in unit.source_text:
                unit.source_text = f"{unit.source_text} {source_text}"
        return list(units.values())

    # --- Survey --------------------------------------------------------------

    def _survey(
        self,
        keys: Sequence[str],
        suggestions: dict[str, GlossarySuggestion],
        units_by_key: dict[str, list[SegmentUnit]],
    ) -> dict[str, list[TermObservation]]:
        work = [(key, unit) for key in keys for unit in units_by_key[key]]
        observations: dict[str, list[TermObservation]] = defaultdict(list)
        for batch in _batched(work, SURVEY_BATCH_SIZE):
            lines = [
                f"### Item id: i{position}\nTerm: {suggestions[key].source_term}\n"
                f"EN: {_clip(unit.source_text)}\nZH: {_clip(unit.text_zh)}"
                for position, (key, unit) in enumerate(batch, start=1)
            ]
            payload = self._call(SURVEY_SYSTEM_PROMPT, "\n\n".join(lines), SURVEY_SCHEMA, "term_survey")
            results = {
                str(item.get("id") or "").strip(): item for item in payload.get("items") or [] if isinstance(item, dict)
            }
            for position, (key, unit) in enumerate(batch, start=1):
                item = results.get(f"i{position}") or {}
                sense = str(item.get("sense") or "unknown").strip().lower()
                if sense not in {"term", "other"}:
                    sense = "unknown"
                rendering = exact_span(unit.text_zh, str(item.get("rendering_zh") or ""))
                if rendering is not None and not _CJK.search(rendering) and not re.search(r"[A-Za-z]", rendering):
                    rendering = None
                observations[key].append(TermObservation(unit.segment_id, rendering, sense))
        return observations

    # --- Decisions -----------------------------------------------------------

    @staticmethod
    def _decide(key: str, suggestion: GlossarySuggestion, observations: Sequence[TermObservation]) -> TermDecision:
        term_observations = [item for item in observations if item.sense == "term" and item.rendering]
        counts = Counter(item.rendering for item in term_observations)
        canonical, skipped = decide_canonical(counts, proposed=suggestion.target_term)
        consistent = (
            sum(count for rendering, count in counts.items() if renders_canonical(rendering, canonical)) if canonical else 0
        )
        return TermDecision(
            key=key,
            source_term=suggestion.source_term,
            term_type=suggestion.term_type,
            canonical_zh=canonical,
            rendering_counts=dict(counts.most_common()),
            expressed_segments=len(term_observations),
            other_sense_segments=sum(1 for item in observations if item.sense == "other"),
            skipped_reason=skipped,
            consistent_before=consistent,
            consistent_after=consistent,
        )

    # --- Replacement ---------------------------------------------------------

    def _replace(
        self,
        units: Sequence[SegmentUnit],
        decisions: Sequence[TermDecision],
        observations: dict[str, list[TermObservation]],
        occurrences_in_unit: dict[tuple[str, str], int],
        *,
        apply: bool,
    ) -> int:
        units_by_id = {unit.segment_id: unit for unit in units}
        original_text = {unit.segment_id: unit.text_zh for unit in units}
        # Longer canonical renderings first, so a replacement never lands inside another term's span.
        ordered = sorted((d for d in decisions if d.harmonized), key=lambda d: -len(d.canonical_zh or ""))
        changes: dict[str, list[tuple[TermDecision, str]]] = defaultdict(list)
        for decision in ordered:
            for observation in observations[decision.key]:
                if (
                    observation.sense != "term"
                    or not observation.rendering
                    or renders_canonical(observation.rendering, decision.canonical_zh)
                ):
                    continue
                unit = units_by_id[observation.segment_id]
                replaced = replace_rendering(
                    unit.text_zh,
                    observation.rendering,
                    decision.canonical_zh,
                    expected_occurrences=occurrences_in_unit.get((unit.segment_id, decision.key), 1),
                )
                if replaced is None:
                    decision.unreplaced[observation.rendering] = decision.unreplaced.get(observation.rendering, 0) + 1
                    continue
                unit.text_zh = replaced
                decision.replaced += 1
                decision.consistent_after += 1
                changes[unit.segment_id].append((decision, observation.rendering))

        if apply and changes:
            segments = {
                segment.id: segment
                for segment in self.session.scalars(select(TargetSegment).where(TargetSegment.id.in_(list(changes))))
            }
            for segment_id, applied in changes.items():
                segment = segments[segment_id]
                after = units_by_id[segment_id].text_zh
                emit_event(
                    self.session,
                    kind=GLOSSARY_UPDATED,
                    chapter_id=segment.chapter_id,
                    actor_kind="agent",
                    actor_id="services.term_consistency",
                    payload={
                        "change": "segment_terminology_harmonized",
                        "target_segment_id": segment_id,
                        "replacements": [
                            {"source_term": decision.source_term, "from": rendering, "to": decision.canonical_zh}
                            for decision, rendering in applied
                        ],
                        "before": original_text[segment_id],
                        "after": after,
                    },
                )
                segment.text_zh = after
        return len(changes)

    def _call(self, system_prompt: str, user_prompt: str, schema: dict[str, Any], schema_name: str) -> dict[str, Any]:
        payload, usage = self.client.generate_structured_object(
            model_name=self.model_name,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=schema,
            schema_name=schema_name,
        )
        self._token_in += int(getattr(usage, "token_in", 0) or 0)
        self._token_out += int(getattr(usage, "token_out", 0) or 0)
        return payload if isinstance(payload, dict) else {}


_SKIP_REASON_ZH = {
    "too_few_occurrences": "术语义出现少于 2 次",
    "many_renderings_context_dependent": "译法过多，视为随语境变化",
    "no_clear_majority": "没有明显的多数译法",
}


def render_consistency_report_markdown(report: TermConsistencyReport) -> str:
    def percent(value: float | None) -> str:
        return "-" if value is None else f"{value * 100:.1f}%"

    harmonized = report.harmonized_decisions
    skipped = [decision for decision in report.decisions if not decision.harmonized]
    unreplaced = sum(sum(decision.unreplaced.values()) for decision in harmonized)
    lines = [
        "# 术语一致性报告",
        "",
        f"- 统一的术语：{len(harmonized)} 个；跳过：{len(skipped)} 个",
        f"- 严格一致率（只算标准译法）：{percent(report.consistency(after=False))} → {percent(report.consistency(after=True))}",
        f"- 修改的译文片段：{report.edited_segments}；因不安全而未替换的出现：{unreplaced}",
        f"- 已锁定术语：{report.locked_term_count}{'' if report.applied else '（试运行，未写入）'}",
        f"- Token：输入 {report.token_in}，输出 {report.token_out}",
        "",
        "## 统一的术语",
        "",
        "| 英文 | 标准译法（全书多数） | 其他译法（次数） | 术语义出现 | 修改前 | 修改后 | 未替换 |",
        "|---|---|---|---|---|---|---|",
    ]
    for decision in harmonized:
        others = "、".join(
            f"{rendering}({count})" for rendering, count in decision.rendering_counts.items() if rendering != decision.canonical_zh
        )
        lines.append(
            f"| {decision.source_term} | {decision.canonical_zh} | {others or '-'} | {decision.expressed_segments} "
            f"| {percent(decision.consistent_before / decision.expressed_segments)} "
            f"| {percent(decision.consistent_after / decision.expressed_segments)} "
            f"| {sum(decision.unreplaced.values()) or '-'} |"
        )
    if skipped:
        lines += ["", "## 跳过的术语", "", "| 英文 | 原因 | 译法（次数） | 其他词义出现 |", "|---|---|---|---|"]
        for decision in skipped:
            renderings = "、".join(f"{rendering}({count})" for rendering, count in decision.rendering_counts.items())
            lines.append(
                f"| {decision.source_term} | {_SKIP_REASON_ZH.get(decision.skipped_reason or '', decision.skipped_reason)} "
                f"| {renderings or '-'} | {decision.other_sense_segments or '-'} |"
            )
    return "\n".join(lines) + "\n"
