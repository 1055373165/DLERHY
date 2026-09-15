"""Automatic terminology consistency for a translated document.

No glossary work is asked of the user. After translation this pass:

1. extracts candidate terms (``GlossaryExtractionService``),
2. decides one canonical Chinese rendering per inconsistent term, with the
   renderings the translation already uses as evidence, plus accepted surface
   variants; terms whose correct Chinese depends on the sentence are left out,
3. edits only the translated segments that miss a decided term, asking for the
   smallest change, and keeps an edit only if the term is now present, the
   rest of the text is essentially unchanged and the length is plausible,
4. locks the decided terms (with variants) so later reruns and review use them,
5. reports consistency before and after, and every segment it could not fix.

Consistency is measured per translated segment with the variant-tolerant
matcher: a segment whose source contains a term is consistent when its Chinese
contains the canonical rendering or an accepted variant.
"""

from __future__ import annotations

import difflib
import re
from collections import defaultdict
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
    target_has_rendering,
)
from book_agent.infra.repositories.events import emit_event
from book_agent.services.glossary_extraction import (
    GlossaryExtractionService,
    GlossarySuggestion,
    StructuredObjectClient,
)
from book_agent.services.glossary_service import GlossaryService

DECISION_BATCH_SIZE = 8
EDIT_BATCH_SIZE = 6
EVIDENCE_PER_TERM = 6
EVIDENCE_TEXT_CHARS = 320
MIN_EDIT_SIMILARITY = 0.75
EDIT_LENGTH_RATIO = (0.7, 1.4)
_CJK = re.compile(r"[一-鿿]")

DECISION_SYSTEM_PROMPT = (
    "You are the terminology editor of an English-to-Simplified-Chinese book translation. "
    "For each term you get the rendering proposed for the glossary and sample translated passages. "
    "Decide the single canonical Chinese rendering the whole book should use: prefer the rendering the "
    "translation already uses most when it is a correct, professional term in this field; otherwise choose "
    "the established professional term. List accepted_variants only for surface forms of the same rendering "
    "that readers would take as identical (for example with or without a trailing classifier such as 形态), "
    "never different wordings. Set context_dependent to true when the English word needs different Chinese "
    "in different sentences (an everyday word that is only sometimes a technical term); such terms will not "
    "be harmonized. "
    'Return one JSON object: {"decisions": [{"source_term": str, "canonical_zh": str, '
    '"accepted_variants": [str], "context_dependent": bool}]}, one entry per input term.'
)

EDIT_SYSTEM_PROMPT = (
    "You are a copy editor applying a fixed terminology to an existing Simplified Chinese translation. "
    "For each segment, change the Chinese as little as possible so that every listed term uses its required "
    "rendering. Replace only the words that translate the term and adjust at most the immediately adjacent "
    "words for grammar; keep all other wording, punctuation, numbers and formatting exactly as they are. "
    "If a term is not actually expressed in the Chinese (it is implied or referred to by a pronoun) and "
    "inserting it would read unnaturally, leave the text unchanged. "
    'Return one JSON object: {"segments": [{"id": str, "text_zh": str, "changed": bool, "reason": str}]}, '
    "one entry per input segment; reason is a few words when changed is false."
)

DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_term": {"type": "string"},
                    "canonical_zh": {"type": "string"},
                    "accepted_variants": {"type": "array", "items": {"type": "string"}},
                    "context_dependent": {"type": "boolean"},
                },
                "required": ["source_term", "canonical_zh"],
            },
        }
    },
    "required": ["decisions"],
}

EDIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "text_zh": {"type": "string"},
                    "changed": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "text_zh"],
            },
        }
    },
    "required": ["segments"],
}


@dataclass(slots=True)
class SegmentUnit:
    segment_id: str
    chapter_id: str
    source_text: str
    text_zh: str
    term_keys: set[str] = field(default_factory=set)


@dataclass(slots=True)
class TermDecision:
    key: str
    source_term: str
    term_type: TermType
    canonical_zh: str
    accepted_variants: list[str]
    context_dependent: bool
    segment_count: int
    consistent_before: int
    consistent_after: int = 0

    @property
    def renderings(self) -> list[str]:
        return [self.canonical_zh, *self.accepted_variants]


@dataclass(slots=True)
class SegmentEditOutcome:
    segment_id: str
    status: str  # edited | kept | rejected
    reason: str
    term_keys: list[str]


@dataclass(slots=True)
class TermConsistencyReport:
    document_id: str
    decisions: list[TermDecision]
    edits: list[SegmentEditOutcome]
    locked_term_count: int
    token_in: int
    token_out: int
    applied: bool

    @property
    def harmonized_decisions(self) -> list[TermDecision]:
        return [decision for decision in self.decisions if not decision.context_dependent]

    def consistency(self, *, after: bool) -> float | None:
        decisions = self.harmonized_decisions
        total = sum(decision.segment_count for decision in decisions)
        if not total:
            return None
        consistent = sum(decision.consistent_after if after else decision.consistent_before for decision in decisions)
        return consistent / total


def accept_segment_edit(original: str, edited: str, renderings_by_term: Sequence[Sequence[str]]) -> str | None:
    """Return None when a minimal terminology edit is acceptable, else the rejection reason."""
    if not edited.strip() or not _CJK.search(edited):
        return "empty_or_not_chinese"
    for renderings in renderings_by_term:
        if not target_has_rendering(edited, renderings):
            return "term_missing_after_edit"
    original_normalized, edited_normalized = normalize_target(original), normalize_target(edited)
    if original_normalized:
        ratio = len(edited_normalized) / len(original_normalized)
        if not EDIT_LENGTH_RATIO[0] <= ratio <= EDIT_LENGTH_RATIO[1]:
            return "length_changed_too_much"
    if difflib.SequenceMatcher(None, original_normalized, edited_normalized).ratio() < MIN_EDIT_SIMILARITY:
        return "edit_too_large"
    return None


def _batched(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _clip(text: str, limit: int = EVIDENCE_TEXT_CHARS) -> str:
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
        # must fail here, not after every extraction, decision and edit was paid for.
        GlossaryService(self.session).list_document_entries(document_id)
        extraction = GlossaryExtractionService(self.session, self.client, model_name=self.model_name).extract(document_id)
        self._token_in += extraction.token_in
        self._token_out += extraction.token_out

        units = self._load_segment_units(document_id)
        suggestions = {source_term_key(item.source_term): item for item in extraction.suggestions}
        index = SourceTermIndex(item.source_term for item in suggestions.values())
        for unit in units:
            unit.term_keys = index.keys_in(unit.source_text)
        units_by_key: dict[str, list[SegmentUnit]] = defaultdict(list)
        for unit in units:
            for key in unit.term_keys:
                units_by_key[key].append(unit)

        decisions = self._decide(suggestions, units_by_key, min_segments=min_segments)
        for decision in decisions:
            decision.consistent_before = sum(
                1 for unit in units_by_key[decision.key] if target_has_rendering(unit.text_zh, decision.renderings)
            )

        edits = self._harmonize(units, decisions, apply=apply)

        for decision in decisions:
            decision.consistent_after = sum(
                1 for unit in units_by_key[decision.key] if target_has_rendering(unit.text_zh, decision.renderings)
            )

        locked = 0
        if apply:
            glossary = GlossaryService(self.session)
            for decision in decisions:
                if decision.context_dependent:
                    continue
                glossary.lock_term(
                    document_id,
                    decision.source_term,
                    decision.canonical_zh,
                    term_type=decision.term_type,
                    target_variants=decision.accepted_variants,
                )
                locked += 1
            self.session.flush()

        return TermConsistencyReport(
            document_id=document_id,
            decisions=sorted(decisions, key=lambda item: (-item.segment_count, item.source_term.casefold())),
            edits=edits,
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

    # --- Decisions -----------------------------------------------------------

    def _decide(
        self,
        suggestions: dict[str, GlossarySuggestion],
        units_by_key: dict[str, list[SegmentUnit]],
        *,
        min_segments: int,
    ) -> list[TermDecision]:
        decisions: list[TermDecision] = []
        needs_model: list[tuple[str, GlossarySuggestion]] = []
        for key, suggestion in suggestions.items():
            matched = units_by_key.get(key, [])
            if len(matched) < min_segments:
                continue
            if all(target_has_rendering(unit.text_zh, [suggestion.target_term]) for unit in matched):
                # Already consistent with the proposed rendering: nothing to decide.
                decisions.append(self._decision(key, suggestion, suggestion.target_term, [], False, len(matched)))
            else:
                needs_model.append((key, suggestion))

        for batch in _batched(needs_model, DECISION_BATCH_SIZE):
            lines: list[str] = []
            for key, suggestion in batch:
                matched = units_by_key[key]
                with_proposed = [unit for unit in matched if target_has_rendering(unit.text_zh, [suggestion.target_term])]
                without = [unit for unit in matched if unit not in with_proposed]
                samples = without[: EVIDENCE_PER_TERM - 2] + with_proposed[:2]
                lines.append(
                    f"### Term: {suggestion.source_term}\n"
                    f"Proposed rendering: {suggestion.target_term} "
                    f"(found in {len(with_proposed)} of {len(matched)} translated segments)\n"
                    + "\n".join(f"- EN: {_clip(unit.source_text)}\n  ZH: {_clip(unit.text_zh)}" for unit in samples)
                )
            payload = self._call(DECISION_SYSTEM_PROMPT, "\n\n".join(lines), DECISION_SCHEMA, "term_decisions")
            by_key = {
                source_term_key(str(item.get("source_term") or "")): item
                for item in payload.get("decisions") or []
                if isinstance(item, dict)
            }
            for key, suggestion in batch:
                item = by_key.get(key)
                canonical = " ".join(str((item or {}).get("canonical_zh") or "").split())
                if not item or not canonical:
                    continue  # no usable decision: leave the term alone
                variants = [
                    " ".join(str(variant).split())
                    for variant in item.get("accepted_variants") or []
                    if str(variant).strip() and " ".join(str(variant).split()) != canonical
                ]
                decisions.append(
                    self._decision(
                        key,
                        suggestion,
                        canonical,
                        variants,
                        item.get("context_dependent") is True,
                        len(units_by_key[key]),
                    )
                )
        return decisions

    @staticmethod
    def _decision(
        key: str,
        suggestion: GlossarySuggestion,
        canonical: str,
        variants: list[str],
        context_dependent: bool,
        segment_count: int,
    ) -> TermDecision:
        return TermDecision(
            key=key,
            source_term=suggestion.source_term,
            term_type=suggestion.term_type,
            canonical_zh=canonical,
            accepted_variants=variants,
            context_dependent=context_dependent,
            segment_count=segment_count,
            consistent_before=0,
        )

    # --- Harmonization -------------------------------------------------------

    def _harmonize(self, units: list[SegmentUnit], decisions: list[TermDecision], *, apply: bool) -> list[SegmentEditOutcome]:
        active = {decision.key: decision for decision in decisions if not decision.context_dependent}
        pending: list[tuple[SegmentUnit, list[TermDecision]]] = []
        for unit in units:
            missing = [
                active[key]
                for key in sorted(unit.term_keys)
                if key in active and not target_has_rendering(unit.text_zh, active[key].renderings)
            ]
            if missing:
                pending.append((unit, missing))

        outcomes: list[SegmentEditOutcome] = []
        segments: dict[str, TargetSegment] = {}
        if apply and pending:
            pending_ids = [unit.segment_id for unit, _ in pending]
            segments = {
                segment.id: segment
                for segment in self.session.scalars(select(TargetSegment).where(TargetSegment.id.in_(pending_ids)))
            }
        for batch in _batched(pending, EDIT_BATCH_SIZE):
            request_lines = []
            for position, (unit, missing) in enumerate(batch, start=1):
                terms = "; ".join(f"{decision.source_term} → {decision.canonical_zh}" for decision in missing)
                request_lines.append(
                    f"### Segment id: s{position}\nEN: {unit.source_text}\nZH: {unit.text_zh}\nRequired terms: {terms}"
                )
            payload = self._call(EDIT_SYSTEM_PROMPT, "\n\n".join(request_lines), EDIT_SCHEMA, "terminology_edits")
            results = {
                str(item.get("id") or "").strip(): item for item in payload.get("segments") or [] if isinstance(item, dict)
            }
            for position, (unit, missing) in enumerate(batch, start=1):
                keys = [decision.key for decision in missing]
                item = results.get(f"s{position}")
                if item is None:
                    outcomes.append(SegmentEditOutcome(unit.segment_id, "rejected", "no_response", keys))
                    continue
                edited = str(item.get("text_zh") or "")
                if item.get("changed") is False or normalize_target(edited) == normalize_target(unit.text_zh):
                    reason = " ".join(str(item.get("reason") or "unchanged").split())
                    outcomes.append(SegmentEditOutcome(unit.segment_id, "kept", reason, keys))
                    continue
                rejection = accept_segment_edit(unit.text_zh, edited, [decision.renderings for decision in missing])
                if rejection is not None:
                    outcomes.append(SegmentEditOutcome(unit.segment_id, "rejected", rejection, keys))
                    continue
                if apply:
                    segment = segments[unit.segment_id]
                    emit_event(
                        self.session,
                        kind=GLOSSARY_UPDATED,
                        chapter_id=unit.chapter_id,
                        actor_kind="agent",
                        actor_id="services.term_consistency",
                        payload={
                            "change": "segment_terminology_harmonized",
                            "target_segment_id": unit.segment_id,
                            "terms": [
                                {"source_term": decision.source_term, "canonical_zh": decision.canonical_zh}
                                for decision in missing
                            ],
                            "before": unit.text_zh,
                            "after": edited,
                        },
                    )
                    segment.text_zh = edited
                unit.text_zh = edited
                outcomes.append(SegmentEditOutcome(unit.segment_id, "edited", "", keys))
        return outcomes

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


def render_consistency_report_markdown(report: TermConsistencyReport) -> str:
    def percent(value: float | None) -> str:
        return "-" if value is None else f"{value * 100:.1f}%"

    edited = sum(1 for item in report.edits if item.status == "edited")
    kept = [item for item in report.edits if item.status == "kept"]
    rejected = [item for item in report.edits if item.status == "rejected"]
    rejection_counts: dict[str, int] = defaultdict(int)
    for item in rejected:
        rejection_counts[item.reason] += 1
    decisions = report.harmonized_decisions
    skipped = [decision for decision in report.decisions if decision.context_dependent]
    lines = [
        "# 术语一致性报告",
        "",
        f"- 统一的术语：{len(decisions)} 个（随语境变化、未统一：{len(skipped)} 个）",
        f"- 术语一致率：{percent(report.consistency(after=False))} → {percent(report.consistency(after=True))}",
        f"- 修改的译文片段：{edited}；模型判断无需修改：{len(kept)}；校验未通过、保留原文：{len(rejected)}",
        f"- 已锁定术语：{report.locked_term_count}{'' if report.applied else '（试运行，未写入）'}",
        f"- Token：输入 {report.token_in}，输出 {report.token_out}",
    ]
    if rejection_counts:
        lines.append("- 未通过校验的原因：" + "，".join(f"{reason} {count}" for reason, count in sorted(rejection_counts.items())))
    lines += ["", "## 术语明细", "", "| 英文 | 标准译法 | 可接受变体 | 片段数 | 修改前 | 修改后 |", "|---|---|---|---|---|---|"]
    for decision in decisions:
        before = decision.consistent_before / decision.segment_count
        after = decision.consistent_after / decision.segment_count
        lines.append(
            f"| {decision.source_term} | {decision.canonical_zh} | {' / '.join(decision.accepted_variants) or '-'} "
            f"| {decision.segment_count} | {percent(before)} | {percent(after)} |"
        )
    if skipped:
        lines += ["", "## 随语境变化、未统一的词", "", "、".join(decision.source_term for decision in skipped)]
    return "\n".join(lines) + "\n"
