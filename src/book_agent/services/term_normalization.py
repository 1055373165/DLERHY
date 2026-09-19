from __future__ import annotations

import re
from typing import Any

from book_agent.domain.terminology.enforcement import LockedTerm
from book_agent.translation.contracts import ConceptCandidate, RelevantTerm
from book_agent.translation.heuristics import DEFAULT_HEURISTICS

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_text(value: str | None) -> str:
    return _WHITESPACE_RE.sub(" ", str(value or "")).strip()


def normalize_term_rendering(source_term: str | None, target_term: str | None) -> str:
    normalized_source = _normalize_text(source_term)
    normalized_target = _normalize_text(target_term)
    if not normalized_target:
        return normalized_target
    compact_target = normalized_target.replace(" ", "")
    source_key = normalized_source.casefold()
    for override in DEFAULT_HEURISTICS.term_rendering_overrides:
        if source_key == override.source_term and override.bad_target_pattern.fullmatch(compact_target):
            return override.preferred_target
    return normalized_target


def normalize_relevant_term(term: RelevantTerm) -> RelevantTerm:
    normalized_target = normalize_term_rendering(term.source_term, term.target_term)
    if normalized_target == term.target_term:
        return term
    return term.model_copy(update={"target_term": normalized_target})


def normalize_concept_candidate(concept: ConceptCandidate) -> ConceptCandidate:
    normalized_target = normalize_term_rendering(concept.source_term, concept.canonical_zh)
    if normalized_target == concept.canonical_zh:
        return concept
    return concept.model_copy(update={"canonical_zh": normalized_target or None})


def normalize_concept_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    source_term = str(payload.get("source_term") or "").strip()
    canonical_zh = str(payload.get("canonical_zh") or "").strip()
    if canonical_zh:
        normalized["canonical_zh"] = normalize_term_rendering(source_term, canonical_zh)
    return normalized


def locked_term_from_entry(entry: Any) -> LockedTerm:
    """A glossary entry as the shared enforcement matcher sees it: expected rendering first, then variants."""
    expected = normalize_term_rendering(entry.source_term, entry.target_term)
    variants = [str(variant) for variant in (getattr(entry, "target_variants_json", None) or []) if str(variant).strip()]
    return LockedTerm(source_term=str(entry.source_term or ""), renderings=tuple([expected, *variants]))

