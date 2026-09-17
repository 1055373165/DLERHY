"""Locked-term enforcement shared by translation time and review time.

One matcher decides whether a translation honours a locked term: the source
side uses ``SourceTermIndex`` (whole tokens, spelling/plural variants,
longest term owns a position) and the target side uses
``target_has_rendering`` (whitespace, punctuation and Latin case ignored,
any accepted rendering counts). The output guardrail rejects a worker
answer with a violation before it is persisted; review turns the ones that
survive into TERM_CONFLICT issues; the translation service reports them as
``glossary.violation`` events.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from book_agent.domain.terminology.matching import SourceTermIndex, source_term_key, target_has_rendering


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
        counts: dict[str, int] = {}
        for occurrence in occurrences:
            counts[occurrence.key] = counts.get(occurrence.key, 0) + 1
        for position, term in usable:
            key = source_term_key(term.source_term)
            if key not in counts:
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
