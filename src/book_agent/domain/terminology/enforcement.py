"""Locked-term enforcement shared by translation time and review time.

One matcher decides whether a translation honours a locked term: the source
side uses ``SourceTermIndex`` (whole tokens, spelling/plural variants,
longest term owns a position) and the target side uses
``target_has_rendering`` (whitespace, punctuation and Latin case ignored,
any accepted rendering counts). The output guardrail rejects a worker
answer with a violation before it is persisted; review turns the ones that
survive into TERM_CONFLICT issues; the translation service reports them as
``glossary.violation`` events.

A cited work's English title is not prose to translate: a term inside a quoted English title
("Fully Autonomous AI Agents Should Not Be Developed.") or anywhere in a citation entry is not
required (``domain.structure.citations``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from book_agent.domain.structure.citations import is_citation_entry, title_spans
from book_agent.domain.terminology.matching import (
    SourceTermIndex,
    inside_identifier,
    source_term_key,
    target_has_rendering,
)
from book_agent.domain.terminology.matching import _token_spans as token_spans


@dataclass(frozen=True, slots=True)
class LockedTerm:
    source_term: str
    # First entry is the expected rendering; the rest are accepted variants.
    renderings: tuple[str, ...]

    @property
    def expected_target_term(self) -> str:
        return self.renderings[0] if self.renderings else ""


@dataclass(frozen=True, slots=True)
class TermViolation:
    unit_id: str
    # Position of the violated term in the ``terms`` argument.
    term_index: int
    source_term: str
    expected_target_term: str
    accepted_renderings: tuple[str, ...]
    source_occurrences: int
    target_text: str


_CODE_CHARS = re.compile(r"[@_()./:<>\[\]]")


def _needs_literal_source(term: LockedTerm) -> bool:
    """Terms the source must spell exactly for the lock to apply.

    The matcher folds case, punctuation and plurals, which is right for words ("large language
    model"). An identifier locked as-is ("@tool", "step()", "final_result") would otherwise claim
    every "tool" and "step", and a word kept in English with its capitals ("Event", "Action",
    "ARES") every "event", "action" and "are".
    """
    source = term.source_term.strip()
    if _CODE_CHARS.search(source):
        return True
    kept_as_is = any(rendering.strip() == source for rendering in term.renderings)
    return kept_as_is


def _literal_in(term: str, source_text: str) -> bool:
    term = term.strip()
    if term.islower() and not _CODE_CHARS.search(term):
        # A lowercase word kept as it is ("execute") is a name in code: "execute(" or `execute`.
        return re.search(rf"`{re.escape(term)}`|(?<![\w@]){re.escape(term)}\(", source_text) is not None
    # A plural of a word kept as it is ("LLMs") is the word.
    return re.search(rf"(?<![\w@]){re.escape(term)}(?:e?s)?(?![\w])", source_text) is not None


def _outside(spans: Sequence[tuple[int, int]], start: int, end: int) -> bool:
    return not any(span_start <= start and end <= span_end for span_start, span_end in spans)


def _literal_outside_titles(term: str, source_text: str, titles: Sequence[tuple[int, int]]) -> bool:
    if not titles:
        return _literal_in(term, source_text)
    # The text with its titles blanked out, offsets kept.
    blanked = list(source_text)
    for start, end in titles:
        blanked[start:end] = " " * (end - start)
    return _literal_in(term, "".join(blanked))


def find_term_violations(
    units: Iterable[tuple[str, str, str]],
    terms: Sequence[LockedTerm],
    *,
    skip_empty_targets: bool = False,
) -> list[TermViolation]:
    """Violations for ``(unit_id, source_text, target_text)`` units.

    A unit is a sentence, an aligned group or a whole packet. Terms whose
    source key does not occur in the unit (under longest-match ownership
    across all terms) are not checked.
    """
    usable = [
        (position, term)
        for position, term in enumerate(terms)
        if term.source_term.strip() and any(r.strip() for r in term.renderings)
    ]
    if not usable:
        return []
    index = SourceTermIndex(term.source_term for _, term in usable)
    violations: list[TermViolation] = []
    for unit_id, source_text, target_text in units:
        if skip_empty_targets and not (target_text or "").strip():
            continue
        occurrences = index.find(source_text or "")
        if not occurrences:
            continue
        if is_citation_entry(source_text or ""):
            # A reference entry keeps its English title, authors and venue as the book has them.
            continue
        titles = title_spans(source_text or "")
        counts: dict[str, int] = {}
        # A translated term inside a code identifier (scratch_agents, tool_call_id) is not the term;
        # an identifier locked as-is ("@tool") is, so it keeps every occurrence.
        prose_counts: dict[str, int] = {}
        spans = token_spans(source_text or "")
        for occurrence in occurrences:
            if titles and not _outside(titles, spans[occurrence.start_token][1], spans[occurrence.end_token - 1][2]):
                continue
            counts[occurrence.key] = counts.get(occurrence.key, 0) + 1
            if not inside_identifier(source_text or "", spans, occurrence):
                prose_counts[occurrence.key] = prose_counts.get(occurrence.key, 0) + 1
        for position, term in usable:
            key = source_term_key(term.source_term)
            if key not in counts:
                continue
            if _needs_literal_source(term):
                if not _literal_outside_titles(term.source_term, source_text or "", titles):
                    continue
            elif key not in prose_counts:
                continue
            renderings = tuple(r for r in term.renderings if r and r.strip())
            if target_has_rendering(target_text or "", renderings):
                continue
            violations.append(
                TermViolation(
                    unit_id=unit_id,
                    term_index=position,
                    source_term=term.source_term,
                    expected_target_term=renderings[0],
                    accepted_renderings=renderings,
                    source_occurrences=counts[key],
                    target_text=target_text or "",
                )
            )
    return violations
