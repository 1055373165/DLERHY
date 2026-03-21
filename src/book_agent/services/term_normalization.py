from __future__ import annotations

import re
from typing import Any

from book_agent.workers.contracts import ConceptCandidate, RelevantTerm

_WHITESPACE_RE = re.compile(r"\s+")
_AGENTIC_AI_BAD_TARGET_RE = re.compile(r"^智能体式(?:AI|人工智能)$", re.IGNORECASE)
_AGENTIC_AI_PREFERRED_TARGET = "智能体AI"


def _normalize_text(value: str | None) -> str:
    return _WHITESPACE_RE.sub(" ", str(value or "")).strip()


def normalize_term_rendering(source_term: str | None, target_term: str | None) -> str:
    normalized_source = _normalize_text(source_term)
    normalized_target = _normalize_text(target_term)
    if not normalized_target:
        return normalized_target
    compact_target = normalized_target.replace(" ", "")
    if normalized_source.casefold() == "agentic ai" and _AGENTIC_AI_BAD_TARGET_RE.fullmatch(compact_target):
        return _AGENTIC_AI_PREFERRED_TARGET
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
