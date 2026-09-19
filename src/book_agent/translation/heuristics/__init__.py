"""Book-specific translation heuristics loaded from data packs.

Style drift rules, term rendering overrides and the concept keyword sets were
Python constants tuned on one family of books (agentic AI / context
engineering). They now live in JSON packs next to this module. The default
pack is ``tech-book-default``; a document can select another pack through
``document.metadata_json["translation_heuristics_pack"]``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import Any

DEFAULT_PACK_NAME = "tech-book-default"
DOCUMENT_METADATA_KEY = "translation_heuristics_pack"


@dataclass(frozen=True, slots=True)
class StyleDriftRule:
    pattern_id: str
    source_pattern: re.Pattern[str]
    target_pattern: re.Pattern[str]
    preferred_hint: str | None = None
    message: str = ""
    prompt_guidance: str | None = None


@dataclass(frozen=True, slots=True)
class TermRenderingOverride:
    """Replace a known-bad target rendering of a source term with the preferred one."""

    source_term: str
    bad_target_pattern: re.Pattern[str]
    preferred_target: str


@dataclass(frozen=True, slots=True)
class HeuristicsPack:
    name: str
    style_drift_rules: tuple[StyleDriftRule, ...]
    term_rendering_overrides: tuple[TermRenderingOverride, ...]
    concept_hint_keywords: frozenset[str]
    concept_headwords: frozenset[str]
    concept_modifiers: frozenset[str]


def _compile(pattern: str, flag_names: list[str]) -> re.Pattern[str]:
    flags = 0
    for flag_name in flag_names:
        flags |= getattr(re, flag_name)
    return re.compile(pattern, flags)


def _parse_pack(payload: dict[str, Any]) -> HeuristicsPack:
    return HeuristicsPack(
        name=payload["name"],
        style_drift_rules=tuple(
            StyleDriftRule(
                pattern_id=rule["pattern_id"],
                source_pattern=_compile(rule["source_pattern"], rule.get("source_flags", [])),
                target_pattern=_compile(rule["target_pattern"], rule.get("target_flags", [])),
                preferred_hint=rule.get("preferred_hint"),
                message=rule.get("message", ""),
                prompt_guidance=rule.get("prompt_guidance"),
            )
            for rule in payload.get("style_drift_rules", [])
        ),
        term_rendering_overrides=tuple(
            TermRenderingOverride(
                source_term=override["source_term"],
                bad_target_pattern=_compile(override["bad_target_pattern"], override.get("bad_target_flags", [])),
                preferred_target=override["preferred_target"],
            )
            for override in payload.get("term_rendering_overrides", [])
        ),
        concept_hint_keywords=frozenset(payload.get("concept_hint_keywords", [])),
        concept_headwords=frozenset(payload.get("concept_headwords", [])),
        concept_modifiers=frozenset(payload.get("concept_modifiers", [])),
    )


@cache
def load_heuristics_pack(name: str = DEFAULT_PACK_NAME) -> HeuristicsPack:
    resource = files("book_agent.translation.heuristics") / f"{name}.json"
    if not resource.is_file():
        raise ValueError(f"Unknown translation heuristics pack: {name}")
    return _parse_pack(json.loads(resource.read_text(encoding="utf-8")))


def heuristics_pack_for_document(document: Any | None) -> HeuristicsPack:
    """The pack a document selects in its metadata, or the default pack."""
    metadata = getattr(document, "metadata_json", None) or {}
    name = str(metadata.get(DOCUMENT_METADATA_KEY) or "").strip() or DEFAULT_PACK_NAME
    return load_heuristics_pack(name)


DEFAULT_HEURISTICS = load_heuristics_pack()
