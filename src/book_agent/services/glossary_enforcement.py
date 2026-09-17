"""CJK leak checks for non-translatable blocks.

Locked-term adherence used to live here as a count-based detector that
disagreed with review's matcher. It is now ``domain.terminology.enforcement``,
shared by the translation output guardrail, the ``glossary.violation``
events and review's TERM_CONFLICT check.
"""

from __future__ import annotations

import re
from typing import Iterable

_CJK_RE = re.compile(r"[㐀-鿿豈-﫿]")


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
