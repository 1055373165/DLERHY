"""Text-Layer Sanity Gate — decides whether a PDF page's text layer is trustworthy.

See tasks/pdf-pipeline-v2.md §M1.2 and the PDF v2 spec §3.2.2. The gate is
a pure function over extracted text; it does not import PyMuPDF or touch disk,
which keeps it unit-testable without fixtures and cheap to run (under 50 ms
per page for typical content).

Three indicators, each independently sufficient to condemn a page:

  - `unicode_entropy`    — Shannon entropy (bits/char) of the character
    distribution. English prose concentrates around 4.0-4.8; corrupted
    custom-font output often falls either well below (repetitive PUA) or
    has a narrow distribution dominated by non-word characters.

  - `pua_ratio`          — fraction of characters in Unicode Private Use
    Areas (U+E000..U+F8FF, U+F0000..U+FFFFD, U+100000..U+10FFFD). These
    are reserved for application-specific mappings; their presence in
    rendered text almost always indicates a broken ToUnicode CMap.

  - `dict_hit_rate`      — fraction of alphabetic tokens that match a
    small high-frequency English word set. A genuine English page lands
    above ~0.20; corrupted text lands well below.

Thresholds are tuned to avoid false positives on short or specialized pages
(code-heavy, formula-heavy, list-heavy) while still catching the known bad
patterns. The gate defaults to *trust* (ok=True) when there is too little
text to judge — failure must be positively signaled, not inferred from noise.

**Scope for M1**: the gate fires on PUA-high and entropy-out-of-band only.
`dict_hit_rate` is computed and emitted for observability but is NOT used
to reject a page on its own — doing so would false-trigger on reference
lists / bibliographies, which consist of proper nouns that legitimately
miss common-word dictionaries. Within-ASCII glyph-swap corruption (a
rare failure mode where A→B and B→A) is out of scope for M1 and noted
as residual risk A1 in the PDF v2 spec §5.1.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Final


# Minimum total non-whitespace characters before any indicator can fire.
# Below this, a page is typically a figure-only page, a title page, or
# otherwise too sparse to judge — trust such pages by default.
# NOTE: we deliberately count non-whitespace rather than alphabetic chars,
# because a corrupted-font page may have zero chars that pass `.isalpha()`
# (PUA codepoints are not alphabetic) while still being dense with symbols
# — and that density is exactly the signal we need to assess.
_MIN_NON_WS_CHARS: Final[int] = 80

# PUA ranges. We treat any codepoint in these ranges as "private use".
_PUA_RANGES: Final[tuple[tuple[int, int], ...]] = (
    (0xE000, 0xF8FF),
    (0xF0000, 0xFFFFD),
    (0x100000, 0x10FFFD),
)

# Indicator thresholds. Breach any one → not OK.
_ENTROPY_MIN: Final[float] = 2.80
_ENTROPY_MAX: Final[float] = 5.80
_PUA_RATIO_MAX: Final[float] = 0.02
_DICT_HIT_RATE_MIN: Final[float] = 0.12

_WORD_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z]{2,}")

# Minimal high-frequency English word list. ~300 entries is enough to
# separate real English prose from corrupted custom-encoded text; we
# deliberately keep this inline rather than shipping an external corpus
# to keep the gate dependency-free and fast.
_COMMON_ENGLISH_WORDS: Final[frozenset[str]] = frozenset(
    {
        "the", "of", "and", "to", "in", "a", "is", "that", "for", "it", "as", "was",
        "with", "be", "by", "on", "not", "he", "i", "this", "are", "or", "his",
        "from", "at", "which", "but", "have", "an", "had", "they", "you", "were",
        "her", "all", "she", "there", "would", "their", "we", "him", "been", "has",
        "when", "who", "will", "more", "no", "if", "out", "so", "said", "what",
        "up", "its", "about", "into", "than", "them", "can", "only", "other",
        "new", "some", "could", "time", "these", "two", "may", "then", "do",
        "first", "any", "my", "now", "such", "like", "our", "over", "man",
        "me", "even", "most", "made", "after", "also", "did", "many", "before",
        "must", "through", "back", "years", "where", "much", "your", "way",
        "well", "down", "should", "because", "each", "just", "those", "people",
        "mr", "how", "too", "little", "state", "good", "very", "make", "world",
        "still", "own", "see", "men", "work", "long", "get", "here", "between",
        "both", "life", "being", "under", "never", "day", "same", "another",
        "know", "while", "last", "might", "us", "great", "old", "year", "off",
        "come", "since", "against", "go", "came", "right", "used", "take",
        "three", "himself", "few", "use", "place", "american", "during", "without",
        "high", "again", "home", "small", "found", "mrs", "thought", "went",
        "say", "part", "once", "general", "school", "every", "don", "does",
        "got", "united", "number", "hand", "course", "water", "until", "far",
        "public", "put", "think", "set", "though", "end", "why", "called",
        "didn", "eyes", "find", "going", "look", "asked", "later", "knew",
        "point", "next", "city", "head", "government", "business", "something",
        "system", "four", "state", "never", "looked", "however", "around",
        "nothing", "story", "example", "research", "based", "data", "process",
        "information", "different", "used", "including", "model", "text",
        "document", "page", "chapter", "figure", "table", "section", "paper",
        "study", "analysis", "results", "method", "using", "show", "given",
        "because", "between", "within", "among", "above", "below", "often",
        "translate", "translation", "language", "english", "chinese", "layer",
        "content", "sentence", "word", "character", "paragraph", "quality",
        "approach", "design", "pattern", "algorithm", "code", "function",
        "note", "value", "input", "output", "test", "case", "user", "type",
        "object", "class", "field", "module", "system", "service", "layer",
        "file", "line", "row", "column", "number", "level", "group",
    }
)


@dataclass(slots=True, frozen=True)
class SanityReport:
    """Outcome of sanity assessment for a single page's text layer.

    `ok=False` means the text layer should not be trusted; callers should
    route that page through OCR (or, once M2 lands, VLM) instead of
    PyMuPDF text extraction.
    """

    ok: bool
    reason: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)


def assess_text(text: str) -> SanityReport:
    """Assess the given text. Returns a SanityReport.

    Callers typically pass the concatenated text of all non-image blocks on
    a page (see `PyMuPDFTextExtractor`). Short/empty text returns ok=True
    with reason='insufficient_text' to preserve the default-trust posture.
    """

    if not text or not text.strip():
        return SanityReport(ok=True, reason="empty", metrics={"total_chars": 0})

    total_chars = len(text)
    non_ws_chars = sum(1 for ch in text if not ch.isspace())
    alpha_chars = sum(1 for ch in text if ch.isalpha())

    if non_ws_chars < _MIN_NON_WS_CHARS:
        return SanityReport(
            ok=True,
            reason="insufficient_text",
            metrics={
                "total_chars": total_chars,
                "non_ws_chars": non_ws_chars,
                "alpha_chars": alpha_chars,
            },
        )

    entropy = _char_entropy(text)
    pua_ratio = _pua_ratio(text, total_chars=total_chars)
    dict_hit = _dict_hit_rate(text)

    metrics: dict[str, Any] = {
        "total_chars": total_chars,
        "non_ws_chars": non_ws_chars,
        "alpha_chars": alpha_chars,
        "unicode_entropy": round(entropy, 4),
        "pua_ratio": round(pua_ratio, 6),
        "dict_hit_rate": round(dict_hit, 4),
    }

    if pua_ratio > _PUA_RATIO_MAX:
        return SanityReport(ok=False, reason="pua_high", metrics=metrics)
    if entropy < _ENTROPY_MIN:
        return SanityReport(ok=False, reason="entropy_low", metrics=metrics)
    if entropy > _ENTROPY_MAX:
        return SanityReport(ok=False, reason="entropy_high", metrics=metrics)

    # dict_hit_rate below the threshold is emitted as observability data but
    # does not fail the page on its own — see module docstring for rationale.
    return SanityReport(ok=True, reason=None, metrics=metrics)


def _char_entropy(text: str) -> float:
    counts: dict[str, int] = {}
    for ch in text:
        if ch.isspace():
            continue
        counts[ch] = counts.get(ch, 0) + 1
    total = sum(counts.values())
    if total == 0:
        return 0.0
    entropy = 0.0
    for count in counts.values():
        p = count / total
        entropy -= p * math.log2(p)
    return entropy


def _pua_ratio(text: str, *, total_chars: int) -> float:
    if total_chars == 0:
        return 0.0
    pua_count = 0
    for ch in text:
        code = ord(ch)
        for lo, hi in _PUA_RANGES:
            if lo <= code <= hi:
                pua_count += 1
                break
    return pua_count / total_chars


def _dict_hit_rate(text: str) -> float:
    tokens = _WORD_RE.findall(text.lower())
    if not tokens:
        return 0.0
    hits = sum(1 for token in tokens if token in _COMMON_ENGLISH_WORDS)
    return hits / len(tokens)
