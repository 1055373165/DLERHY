"""Variant-tolerant term matching for terminology consistency.

Source side: a term is identified by a *key* that ignores case, hyphens and
spaces between words ("timeframe" / "time-frame" / "time frame"), "&" versus
"and", possessive "'s", and the plural of its last word (regular and common
irregular forms). Matching is on whole tokens, and the longest term wins at a
position, so "Inverted Head & Shoulders" is not also counted as "Head &
Shoulders", and "bull" does not match "bullish".

Target side: a Chinese rendering is found in a translation after dropping
whitespace and punctuation and folding Latin case ("RSI 背离" == "rsi背离"),
and any of a term's accepted variants counts as the term.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z]+)?|&")
_IRREGULAR_PLURALS = {
    "men": "man",
    "women": "woman",
    "children": "child",
    "people": "person",
    "indices": "index",
    "matrices": "matrix",
    "analyses": "analysis",
    "hypotheses": "hypothesis",
    "criteria": "criterion",
    "phenomena": "phenomenon",
    "data": "datum",
    "media": "medium",
}
_SINGULAR_KEEP = ("ss", "us", "is")
# Singular words that end in -s.
_SINGULAR_WORDS = frozenset(
    {
        "atlas", "basis", "bias", "canvas", "chaos", "economics", "gas", "lens", "mathematics",
        "means", "news", "physics", "series", "species", "statistics", "status", "thesis", "various",
    }
)
MAX_TERM_TOKENS = 6


def singularize(word: str) -> str:
    lowered = word.lower()
    if lowered in _SINGULAR_WORDS:
        return lowered
    if lowered.endswith("es") and lowered[:-2] in _SINGULAR_WORDS:
        return lowered[:-2]  # "biases" -> "bias"
    if lowered in _IRREGULAR_PLURALS:
        return _IRREGULAR_PLURALS[lowered]
    if len(lowered) > 4 and lowered.endswith("ies"):
        return lowered[:-3] + "y"
    if len(lowered) > 4 and lowered.endswith(("ches", "shes", "sses", "xes", "zes")):
        return lowered[:-2]
    if len(lowered) > 3 and lowered.endswith("s") and not lowered.endswith(_SINGULAR_KEEP):
        return lowered[:-1]
    return lowered


def _token_spans(text: str) -> list[tuple[str, int, int]]:
    """Tokens with the character span they came from, so callers can look at their neighbours."""
    spans: list[tuple[str, int, int]] = []
    for match in _TOKEN_RE.finditer(text or ""):
        raw = match.group(0)
        if raw == "&":
            spans.append(("and", match.start(), match.end()))
            continue
        token = re.sub(r"['\u2019]", "", re.sub(r"['\u2019]s$", "", raw.lower()))
        if token:
            spans.append((token, match.start(), match.end()))
    return spans


def source_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(text or ""):
        if raw == "&":
            tokens.append("and")
            continue
        token = raw.lower()
        token = re.sub(r"['’]s$", "", token)
        token = re.sub(r"['’]", "", token)
        if token:
            tokens.append(token)
    return tokens


def _key_from_tokens(tokens: Sequence[str]) -> str:
    if not tokens:
        return ""
    return "".join(tokens[:-1]) + singularize(tokens[-1])


def source_term_key(term: str) -> str:
    """The identity of a source term across case, spacing, hyphenation and plural forms."""
    return _key_from_tokens(source_tokens(term))


@dataclass(frozen=True, slots=True)
class TermOccurrence:
    key: str
    start_token: int
    end_token: int


class SourceTermIndex:
    """Finds glossary terms in source text with longest-match-wins ownership."""

    def __init__(self, terms: Iterable[str]) -> None:
        self._max_tokens = 1
        self._keys: set[str] = set()
        for term in terms:
            tokens = source_tokens(term)
            key = _key_from_tokens(tokens)
            if not key:
                continue
            self._keys.add(key)
            self._max_tokens = max(self._max_tokens, min(len(tokens), MAX_TERM_TOKENS))
        # A term written as one word ("timeframe") can appear split in the text ("time frame").
        self._max_tokens = min(MAX_TERM_TOKENS, self._max_tokens + 1)

    def find(self, text: str) -> list[TermOccurrence]:
        spans = _token_spans(text)
        tokens = [token for token, _start, _end in spans]
        occurrences: list[TermOccurrence] = []
        position = 0
        while position < len(tokens):
            matched: TermOccurrence | None = None
            for size in range(min(self._max_tokens, len(tokens) - position), 0, -1):
                key = _key_from_tokens(tokens[position : position + size])
                if key in self._keys:
                    matched = TermOccurrence(key=key, start_token=position, end_token=position + size)
                    break
            if matched is None:
                position += 1
                continue
            if _inside_hyphenated_word(text or "", spans, matched):
                # "fault" in "fault-tolerant" is part of another word, like "bull" in "bullish".
                position += 1
                continue
            occurrences.append(matched)
            position = matched.end_token
        return occurrences

    def keys_in(self, text: str) -> set[str]:
        return {occurrence.key for occurrence in self.find(text)}

    def count(self, texts: Iterable[str]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for text in texts:
            for occurrence in self.find(text):
                counts[occurrence.key] = counts.get(occurrence.key, 0) + 1
        return counts


def _inside_hyphenated_word(text: str, spans: Sequence[tuple[str, int, int]], occurrence: TermOccurrence) -> bool:
    """True when the matched tokens are joined by a hyphen to a word outside the match."""
    start = spans[occurrence.start_token][1]
    end = spans[occurrence.end_token - 1][2]
    before = text[:start].rstrip()
    after = text[end:].lstrip() if text[end : end + 1] not in {"-", "\u2011"} else text[end:]
    joined_before = before.endswith(("-", "\u2011")) and text[:start].rstrip("-\u2011")[-1:].isalnum()
    joined_after = after.startswith(("-", "\u2011")) and after[1:2].isalnum()
    return joined_before or joined_after


def inside_identifier(text: str, spans: Sequence[tuple[str, int, int]], occurrence: TermOccurrence) -> bool:
    """True when the match is part of a code identifier: "agents" in scratch_agents, "tool call" in
    tool_call_id, "model context protocol" in @modelcontextprotocol/inspector, "agent" in agent.run().
    The Agent book's whole-book QA flagged these as term misses. CamelCase is left alone: product
    names (LangChain, OpenAI) are terms too."""
    start = spans[occurrence.start_token][1]
    end = spans[occurrence.end_token - 1][2]
    before = text[start - 1 : start]
    after = text[end : end + 1]
    if before in {"_", "@", "/"} or after in {"_", "("}:
        return True
    if before == "." and text[start - 2 : start - 1].isalnum():
        return True
    if after == "." and text[end + 1 : end + 2].isalpha() and text[end + 1 : end + 2].islower():
        return True
    # Inside the tokens: "tool_call" is two tokens joined by an underscore.
    return "_" in text[start:end]


def normalize_target(text: str) -> str:
    """Chinese text for rendering lookup: no whitespace or punctuation, Latin case folded."""
    return "".join(
        char.casefold()
        for char in unicodedata.normalize("NFKC", text or "")
        if not char.isspace() and not unicodedata.category(char).startswith("P")
    )


def target_has_rendering(target_text: str, renderings: Iterable[str]) -> bool:
    normalized = normalize_target(target_text)
    return any(candidate and candidate in normalized for candidate in (normalize_target(r) for r in renderings))


def renderings_by_key(entries: Mapping[str, Sequence[str]]) -> dict[str, tuple[str, ...]]:
    """Group accepted renderings under source keys (merging spelling variants of one term)."""
    grouped: dict[str, list[str]] = {}
    for term, renderings in entries.items():
        key = source_term_key(term)
        if not key:
            continue
        bucket = grouped.setdefault(key, [])
        for rendering in renderings:
            if rendering and rendering not in bucket:
                bucket.append(rendering)
    return {key: tuple(values) for key, values in grouped.items()}
