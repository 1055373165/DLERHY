"""Document-level terminology mining — PDF v2 M2.5 (Pass A).

Given a `ParsedDocument`, surface a ranked list of candidate terms worth
locking for document-level translation consistency. The output feeds
the glossary service (M2.6); it does **not** itself translate anything.

Heuristics (pure, deterministic, no LLM):

  * Tokenize only translatable prose blocks; code/equation/figure/table
    never contribute noise.
  * Extract unigram, bigram, and trigram candidates — most domain terms
    are 1-3 tokens ("agent", "vector store", "retrieval augmented
    generation").
  * Drop tokens consisting of ASCII stopwords at every position.
  * Weight capitalised and acronym tokens more heavily (they're more
    likely to be proper nouns / named entities that translate drift).
  * Boost terms that appear in a definition-style context ("X is
    defined as", "we call X", "the term X refers to …") — they're
    likely key concepts even if low-frequency.

The algorithm is intentionally simple. Top 200 terms by weighted score,
with metadata capturing first-seen chapter/block to let reviewers jump
to the term's origin. Full TF-IDF across a corpus is deferred; the
"document" here is self-contained and cross-doc normalization lives in
a later milestone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final, Iterable

from book_agent.domain.structure.models import (
    TRANSLATE_ALL,
    ParsedBlock,
    ParsedChapter,
    ParsedDocument,
)


_WORD_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z][A-Za-z0-9\-]*")

# High-frequency English words that should never lead or solely fill a
# candidate. Deliberately kept short; domain terms often survive a
# permissive stoplist but are destroyed by an aggressive one.
_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "the", "a", "an", "of", "in", "on", "at", "to", "for", "with", "by",
        "from", "as", "is", "are", "was", "were", "be", "been", "being",
        "and", "or", "but", "nor", "so", "yet", "if", "than", "that", "this",
        "these", "those", "it", "its", "he", "she", "him", "her", "they",
        "them", "we", "you", "i", "my", "your", "our", "their", "his", "hers",
        "has", "have", "had", "do", "does", "did", "not", "no", "yes",
        "any", "some", "each", "every", "all", "both", "few", "many", "more",
        "most", "other", "such", "only", "own", "same", "than", "too", "very",
        "can", "will", "just", "should", "now", "into", "through", "during",
        "before", "after", "above", "below", "up", "down", "out", "over",
        "under", "again", "further", "then", "once", "here", "there", "when",
        "where", "why", "how", "also", "about", "like",
    }
)

_MIN_TOKEN_LEN: Final[int] = 2
_MIN_FREQUENCY: Final[int] = 2
_MAX_NGRAM: Final[int] = 3
_TOP_K_DEFAULT: Final[int] = 200

_DEFINITION_PATTERNS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b([A-Z][A-Za-z0-9\- ]{2,40}?)\s+is\s+defined\s+as\b",
        r"\bwe\s+call\s+([A-Za-z0-9\- ]{2,40}?)\b",
        r"\bthe\s+term\s+([A-Za-z0-9\- ]{2,40}?)\s+(?:refers|means|denotes)\b",
        r"\b([A-Z][A-Za-z0-9\-]*)\s*\(([A-Z]{2,10})\)",  # "Retrieval-Augmented Generation (RAG)"
    )
)


@dataclass(frozen=True, slots=True)
class TermCandidate:
    """A single mined term with enough provenance to support review.

    - `term`: surface form as it first appeared (case preserved from origin).
    - `frequency`: how many times this exact lowered n-gram appeared.
    - `weight`: frequency × boost from proper-noun / definition signals.
    - `first_seen_chapter_id`, `first_seen_block_anchor`,
      `first_seen_block_ordinal`: lets a reviewer jump to the origin.
    - `is_proper_noun`: every token starts with uppercase.
    - `is_acronym`: single-token, all-uppercase, 2-10 chars.
    - `definition_boost`: True if the term matched a definition pattern.
    """

    term: str
    frequency: int
    weight: float
    first_seen_chapter_id: str | None
    first_seen_block_anchor: str | None
    first_seen_block_ordinal: int | None
    is_proper_noun: bool
    is_acronym: bool
    definition_boost: bool


@dataclass(slots=True)
class _TermAccumulator:
    display_term: str
    frequency: int = 0
    is_proper_noun: bool = True
    is_acronym: bool = False
    definition_boost: bool = False
    first_seen_chapter_id: str | None = None
    first_seen_block_anchor: str | None = None
    first_seen_block_ordinal: int | None = None
    positions: list[tuple[str | None, str | None, int | None]] = field(default_factory=list)


def mine_terms(
    document: ParsedDocument,
    *,
    top_k: int = _TOP_K_DEFAULT,
    min_frequency: int = _MIN_FREQUENCY,
    max_ngram: int = _MAX_NGRAM,
) -> list[TermCandidate]:
    """Return at most `top_k` candidates sorted by descending weight."""

    accumulators: dict[str, _TermAccumulator] = {}
    seen_definitions: set[str] = set()

    for chapter in document.chapters:
        _scan_chapter(
            chapter,
            accumulators=accumulators,
            seen_definitions=seen_definitions,
            max_ngram=max_ngram,
        )

    candidates: list[TermCandidate] = []
    for key, acc in accumulators.items():
        if acc.frequency < min_frequency and not acc.definition_boost:
            continue
        is_unigram = " " not in key and "-" not in key.replace("-", " ")
        if is_unigram and acc.frequency < max(min_frequency, 3):
            # Unigrams need a higher bar to cut noise.
            if not (acc.definition_boost or acc.is_acronym):
                continue
        weight = _score(acc)
        candidates.append(
            TermCandidate(
                term=acc.display_term,
                frequency=acc.frequency,
                weight=weight,
                first_seen_chapter_id=acc.first_seen_chapter_id,
                first_seen_block_anchor=acc.first_seen_block_anchor,
                first_seen_block_ordinal=acc.first_seen_block_ordinal,
                is_proper_noun=acc.is_proper_noun,
                is_acronym=acc.is_acronym,
                definition_boost=acc.definition_boost,
            )
        )

    candidates.sort(key=lambda c: (-c.weight, -c.frequency, c.term.lower()))
    return candidates[:top_k]


def _scan_chapter(
    chapter: ParsedChapter,
    *,
    accumulators: dict[str, _TermAccumulator],
    seen_definitions: set[str],
    max_ngram: int,
) -> None:
    for block in chapter.blocks:
        if not _block_contributes(block):
            continue
        _scan_block_for_ngrams(
            chapter,
            block,
            accumulators=accumulators,
            max_ngram=max_ngram,
        )
        _scan_block_for_definitions(
            chapter,
            block,
            accumulators=accumulators,
            seen_definitions=seen_definitions,
        )


def _block_contributes(block: ParsedBlock) -> bool:
    return block.translatability == TRANSLATE_ALL and bool(block.text and block.text.strip())


def _scan_block_for_ngrams(
    chapter: ParsedChapter,
    block: ParsedBlock,
    *,
    accumulators: dict[str, _TermAccumulator],
    max_ngram: int,
) -> None:
    tokens = _tokenize(block.text)
    if not tokens:
        return
    for n in range(1, max_ngram + 1):
        if len(tokens) < n:
            continue
        for i in range(len(tokens) - n + 1):
            window = tokens[i : i + n]
            ngram_key = " ".join(t.lower() for t in window)
            if not _ngram_is_valid(window, n):
                continue
            acc = accumulators.get(ngram_key)
            display = " ".join(window)
            if acc is None:
                acc = _TermAccumulator(
                    display_term=display,
                    first_seen_chapter_id=chapter.chapter_id,
                    first_seen_block_anchor=block.anchor,
                    first_seen_block_ordinal=block.ordinal,
                )
                accumulators[ngram_key] = acc
            acc.frequency += 1
            # Update proper-noun / acronym flags based on the window.
            if not all(_starts_uppercase(t) for t in window):
                acc.is_proper_noun = False
            if n == 1 and _is_acronym(window[0]):
                acc.is_acronym = True


_LEADING_ARTICLE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:the|an|a)\s+", re.IGNORECASE
)


def _strip_leading_article(term: str) -> str:
    """Remove a leading The/An/A that swallowed the proper-noun head.

    Definition patterns like `[A-Z][A-Za-z0-9\\- ]{2,40}?\\s+is\\s+defined\\s+as`
    will greedily start at the first capital in the sentence — often an
    article ("An Action Plan is defined as…"). Stripping the article
    leaves us with the real term head ("Action Plan").
    """
    cleaned = _LEADING_ARTICLE_RE.sub("", term, count=1).strip()
    return cleaned


def _scan_block_for_definitions(
    chapter: ParsedChapter,
    block: ParsedBlock,
    *,
    accumulators: dict[str, _TermAccumulator],
    seen_definitions: set[str],
) -> None:
    text = block.text
    if not text:
        return
    for pattern in _DEFINITION_PATTERNS:
        for match in pattern.finditer(text):
            groups = [g for g in match.groups() if g]
            for raw in groups:
                cleaned = _strip_leading_article(raw.strip().strip(",.;:"))
                if not cleaned:
                    continue
                key = cleaned.lower()
                if key in seen_definitions:
                    continue
                seen_definitions.add(key)
                acc = accumulators.get(key)
                if acc is None:
                    acc = _TermAccumulator(
                        display_term=cleaned,
                        frequency=1,
                        first_seen_chapter_id=chapter.chapter_id,
                        first_seen_block_anchor=block.anchor,
                        first_seen_block_ordinal=block.ordinal,
                    )
                    accumulators[key] = acc
                acc.definition_boost = True


def _tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(text)


def _ngram_is_valid(tokens: list[str], n: int) -> bool:
    if not tokens:
        return False
    if any(len(t) < _MIN_TOKEN_LEN for t in tokens):
        return False
    first_lower = tokens[0].lower()
    last_lower = tokens[-1].lower()
    if first_lower in _STOPWORDS or last_lower in _STOPWORDS:
        return False
    if n == 1:
        tok = tokens[0]
        if tok.lower() in _STOPWORDS:
            return False
    return True


def _starts_uppercase(token: str) -> bool:
    return bool(token) and token[0].isupper()


def _is_acronym(token: str) -> bool:
    return 2 <= len(token) <= 10 and token.isalpha() and token.isupper()


def _score(acc: _TermAccumulator) -> float:
    score = float(acc.frequency)
    if acc.is_proper_noun:
        score *= 1.5
    if acc.is_acronym:
        score *= 2.0
    if acc.definition_boost:
        score += 5.0
    return score
