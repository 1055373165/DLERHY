"""Align a block's retired sentences with its re-segmented ones (pure, deterministic).

Used by the parse-revision fork to decide which translations can be carried
over. Relations, from the old sentence's point of view:

- ``same``: the new sentence has the same text after normalisation, or a
  near-identical one (similarity >= SAME_THRESHOLD and length within
  LENGTH_TOLERANCE). Its translation carries over.
- ``split``: the old sentence's text was divided across several new ones.
- ``merge``: several old sentences became one new sentence.
- ``removed``: nothing in the new set corresponds to it.

Only ``same`` carries translations; split and merge need retranslation, but
the lineage is kept so issues and reviewers can follow the text.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

SAME_THRESHOLD = 0.92
LENGTH_TOLERANCE = 0.15

_SPACE = re.compile(r"\s+")


def normalize_sentence(text: str | None) -> str:
    return _SPACE.sub(" ", (text or "").strip()).casefold()


@dataclass(frozen=True, slots=True)
class SentenceLink:
    from_id: str
    to_id: str | None
    relation: str
    similarity: float | None = None


def _similar(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if abs(len(a) - len(b)) > LENGTH_TOLERANCE * max(len(a), len(b)):
        return 0.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def align_sentences(old: Sequence[tuple[str, str]], new: Sequence[tuple[str, str]]) -> list[SentenceLink]:
    """Links for every old sentence (in order); new sentences may be unlinked (added text)."""
    old_norm = [(sentence_id, normalize_sentence(text)) for sentence_id, text in old]
    new_norm = [(sentence_id, normalize_sentence(text)) for sentence_id, text in new]
    links: dict[str, SentenceLink] = {}
    used_new: set[str] = set()

    # 1. identical text, first unused match in reading order
    for old_id, old_text in old_norm:
        for new_id, new_text in new_norm:
            if new_id not in used_new and old_text and old_text == new_text:
                links[old_id] = SentenceLink(old_id, new_id, "same", 1.0)
                used_new.add(new_id)
                break

    # 2. near-identical text
    for old_id, old_text in old_norm:
        if old_id in links:
            continue
        best: tuple[float, str] | None = None
        for new_id, new_text in new_norm:
            if new_id in used_new:
                continue
            score = _similar(old_text, new_text)
            if score >= SAME_THRESHOLD and (best is None or score > best[0]):
                best = (score, new_id)
        if best is not None:
            links[old_id] = SentenceLink(old_id, best[1], "same", round(best[0], 3))
            used_new.add(best[1])

    # 3. splits and merges by containment
    result: list[SentenceLink] = []
    for old_id, old_text in old_norm:
        if old_id in links:
            result.append(links[old_id])
            continue
        parts = [new_id for new_id, new_text in new_norm if new_id not in used_new and new_text and new_text in old_text]
        if len(parts) >= 1 and old_text and sum(len(t) for i, t in new_norm if i in parts) >= 0.8 * len(old_text):
            result.extend(SentenceLink(old_id, new_id, "split") for new_id in parts)
            continue
        containers = [new_id for new_id, new_text in new_norm if old_text and old_text in new_text]
        if containers:
            result.append(SentenceLink(old_id, containers[0], "merge"))
            continue
        result.append(SentenceLink(old_id, None, "removed"))
    return result
