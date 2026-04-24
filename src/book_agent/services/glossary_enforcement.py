"""Glossary adherence post-validator (PDF v2 M2.7).

Pure function that answers: *for this (source_text, target_text) pair,
did the translation honour every applicable locked term?*

Designed to sit behind the translation worker without modifying the
worker itself. Callers invoke `detect_violations(...)` on each
translated sentence (or block) and either:

  * Emit `GLOSSARY_VIOLATION` events for observability and review.
  * Flag the offending sentence for re-translation / manual review.
  * Surface the violation in the review UI alongside source/target.

No network, no LLM, no DB — the service above this one owns those
concerns. Determinism in → determinism out makes the detector trivial
to regression-test.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

_CJK_RE = re.compile(r"[㐀-鿿豈-﫿]")


@dataclass(frozen=True, slots=True)
class GlossaryViolation:
    """A single locked-term rule that was breached in a translated pair.

    - `source_term`: the English term that triggered the check.
    - `expected_target`: the locked Chinese translation that should have
      appeared.
    - `source_match_count`: how many times the source term appeared in
      the source text (case-insensitive). A count ≥ 1 is what fires
      the check; higher counts are surfaced for severity triage.
    - `target_match_count`: how many times the expected target appeared
      in the target text. By definition < source_match_count for a
      violation to fire.
    - `severity_hint`: "hard" if the target never appeared;
      "partial" if it appeared but fewer times than the source.
    """

    source_term: str
    expected_target: str
    source_match_count: int
    target_match_count: int
    severity_hint: str


def detect_violations(
    source_text: str,
    target_text: str,
    locked_glossary: dict[str, str],
    *,
    case_insensitive: bool = True,
) -> list[GlossaryViolation]:
    """Return the list of violations for this (source, target) pair.

    An empty glossary short-circuits to `[]`. Entries with empty source
    or target strings are ignored — they are not actionable rules.
    """
    if not locked_glossary or not source_text:
        return []
    violations: list[GlossaryViolation] = []
    for raw_source, raw_target in locked_glossary.items():
        source_term = (raw_source or "").strip()
        target_term = (raw_target or "").strip()
        if not source_term or not target_term:
            continue
        source_count = _count_occurrences(
            source_text, source_term, case_insensitive=case_insensitive
        )
        if source_count == 0:
            continue
        target_count = _count_occurrences(
            target_text or "", target_term, case_insensitive=False
        )
        if target_count >= source_count:
            continue
        severity = "hard" if target_count == 0 else "partial"
        violations.append(
            GlossaryViolation(
                source_term=source_term,
                expected_target=target_term,
                source_match_count=source_count,
                target_match_count=target_count,
                severity_hint=severity,
            )
        )
    return violations


def has_cjk_characters(text: str) -> bool:
    """Utility: does `text` contain any CJK Unified Ideograph?

    Useful companion check — a `translatability=translate_none` block
    whose target text contains CJK is itself a protocol violation
    (spec §5.1 KPI 3), distinct from a glossary violation but often
    caught in the same post-validation pass.
    """
    return bool(text) and _CJK_RE.search(text) is not None


def detect_non_translatable_leaks(
    blocks: Iterable,
) -> list[str]:
    """Scan an iterable of objects exposing `translatability` + `target_text`
    (or `source_text`) for TRANSLATE_NONE items whose target leaked CJK.

    Returns a list of block identifiers (anchors / ids). The caller
    decides how to report (event, test failure, review flag).
    """
    out: list[str] = []
    for block in blocks:
        translatability = getattr(block, "translatability", None)
        if translatability != "translate_none":
            continue
        target_text = getattr(block, "target_text", None) or ""
        if not target_text:
            continue
        if has_cjk_characters(target_text):
            identifier = (
                getattr(block, "anchor", None)
                or getattr(block, "id", None)
                or repr(block)
            )
            out.append(str(identifier))
    return out


# --- Internals ---


def _count_occurrences(
    haystack: str,
    needle: str,
    *,
    case_insensitive: bool,
) -> int:
    if not haystack or not needle:
        return 0
    # Word-boundary matching for terms that look like whole words —
    # prevents "agent" matching inside "agentic" and creating
    # false-positive violations. CJK targets skip the boundary logic
    # because regex `\b` doesn't work meaningfully in non-Latin scripts;
    # a raw substring count is what readers perceive.
    is_latin = needle[0].isascii() and needle[0].isalpha()
    if is_latin:
        pattern = r"(?<![A-Za-z0-9])" + re.escape(needle) + r"(?![A-Za-z0-9])"
        flags = re.IGNORECASE if case_insensitive else 0
        return len(re.findall(pattern, haystack, flags=flags))
    # CJK / mixed: substring count.
    if case_insensitive:
        return haystack.lower().count(needle.lower())
    return haystack.count(needle)
