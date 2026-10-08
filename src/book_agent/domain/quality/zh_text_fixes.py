"""Deterministic corrections of Chinese translation text: the pilot repair's ``segment_edit`` transforms.

Each takes a segment's text and returns it corrected (the same string when nothing applies). They
touch nothing inside inline code (`...`) or URLs, and fix only what the layered QA's L5/L7 checks
flag (``domain/quality/pilot_qa.py``): CJK–Latin spacing against the book's convention, half-width
punctuation in Chinese, stray whitespace, program notes leaked into the text, a numbered label named
two ways ("清单 2.1" / "代码清单 2.1") and a term rendered two ways.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from book_agent.domain.quality.pilot_qa import LEAKED_MARKERS

_CJK = "㐀-䶿一-鿿豈-﫿"
# Spans a fix never touches: inline code, URLs, e-mail-like identifiers.
_PROTECTED_SPAN_RE = re.compile(r"`[^`]*`|(?:https?://|www\.)\S+|\b[\w.-]+@[\w.-]+\b")
_MISSING_SPACE_RE = re.compile(rf"(?<=[{_CJK}])(?=[A-Za-z0-9])|(?<=[A-Za-z0-9%])(?=[{_CJK}])")
_EXTRA_SPACE_RE = re.compile(rf"(?<=[{_CJK}]) +(?=[A-Za-z0-9])|(?<=[A-Za-z0-9%]) +(?=[{_CJK}])")
_HALFWIDTH_AFTER_CJK_RE = re.compile(rf"(?<=[{_CJK}])([,;!?])")
_HALFWIDTH_BEFORE_CJK_RE = re.compile(rf"([,;!?])(?=[{_CJK}])")
_COLON_BETWEEN_CJK_RE = re.compile(rf"(?<=[{_CJK}]):(?=\s?[{_CJK}])")
_FULLWIDTH = {",": "，", ";": "；", "!": "！", "?": "？", ":": "："}
_LEAK_PREFIX_RE = re.compile(r"^\s*(?:译文|翻译|中文翻译|Translation)\s*[:：]\s*")
# Whitespace a Chinese reader never wants: after full-width punctuation, runs of spaces, U+3000.
_SPACE_AFTER_CJK_PUNCT_RE = re.compile(rf"(?<=[、。！，：；？])[ \t]+(?=[{_CJK}“「])")
_SPACE_RUN_RE = re.compile(r"(?<=\S)[ \t]{2,}(?=\S)")


def _outside_protected(text: str, fix: Callable[[str], str]) -> str:
    """Apply ``fix`` to the parts of ``text`` outside inline code and URLs."""
    parts: list[str] = []
    last = 0
    for match in _PROTECTED_SPAN_RE.finditer(text):
        parts.append(fix(text[last : match.start()]))
        parts.append(match.group(0))
        last = match.end()
    parts.append(fix(text[last:]))
    return "".join(parts)


def fix_cjk_latin_spacing(text: str, convention: str) -> str:
    """A space (``spaced``) or none (``unspaced``) between Chinese and Latin letters or digits."""
    if convention == "spaced":
        return _outside_protected(text, lambda part: _MISSING_SPACE_RE.sub(" ", part))
    return _outside_protected(text, lambda part: _EXTRA_SPACE_RE.sub("", part))


def fix_odd_whitespace(text: str) -> str:
    fixed = text.replace("　", "")
    fixed = _SPACE_AFTER_CJK_PUNCT_RE.sub("", fixed)
    return _outside_protected(fixed, lambda part: _SPACE_RUN_RE.sub(" ", part))


def fix_halfwidth_punctuation(text: str) -> str:
    def fix(part: str) -> str:
        part = _COLON_BETWEEN_CJK_RE.sub("：", part)
        part = _HALFWIDTH_AFTER_CJK_RE.sub(lambda match: _FULLWIDTH[match.group(1)], part)
        part = _HALFWIDTH_BEFORE_CJK_RE.sub(lambda match: _FULLWIDTH[match.group(1)], part)
        # The space a half-width mark carried is not wanted after a full-width one.
        return re.sub(r"([，；！？：]) +(?=[^A-Za-z0-9`])", r"\1", part)

    return _outside_protected(text, fix)


def strip_leaked_markers(text: str) -> str:
    """The translation without program notes ("图片锚点保留", "ZH::", a "译文：" prefix) or markdown ("**")."""
    fixed = strip_markdown_emphasis(_LEAK_PREFIX_RE.sub("", text))
    for marker in LEAKED_MARKERS:
        fixed = fixed.replace(marker, "")
    if fixed == text:
        return text
    fixed = re.sub(r"[（(]\s*[)）]", "", fixed)
    return " ".join(fixed.split())


def unify_label(text: str, numbers: Sequence[str], wanted: str, others: Sequence[str]) -> str:
    """"清单 2.1" -> "代码清单 2.1": another name of a numbered label becomes the book's usual one.

    Only right before one of ``numbers``, and never where the other name is already the tail of
    the wanted one ("代码清单" holds "清单").
    """
    fixed = text
    for other in sorted((name for name in others if name and name != wanted), key=len, reverse=True):
        guard = wanted[: -len(other)] if wanted.endswith(other) else ""
        for number in numbers:
            lookbehind = f"(?<!{re.escape(guard)})" if guard else ""
            pattern = rf"{lookbehind}{re.escape(other)}(\s*){re.escape(number)}(?![\d.])"
            fixed = re.sub(pattern, lambda match: f"{wanted}{match.group(1)}{number}", fixed)
    return fixed


# Markdown emphasis the model wraps around a heading or a phrase ("**核心要点**"): shown as asterisks.
MARKDOWN_EMPHASIS_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.DOTALL)


_LEADING_BULLET_RE = re.compile(r"^\s*[\u2022\u25cf\u25e6\u25aa\u2023\u2219\u25a0]\s*")


def strip_unsourced_bullet(text: str, sources: Sequence[str]) -> str:
    """"• 由路由智能体接收…" for "1 A router agent receives…": a bullet glyph the source does not have.

    The model sometimes opens a numbered item's translation with a bullet; the reading copy draws
    the item's number, so the reader saw "1. •". A source that starts with a bullet keeps it.
    """
    if not _LEADING_BULLET_RE.match(text or "") or any(_LEADING_BULLET_RE.match(source or "") for source in sources):
        return text
    return _LEADING_BULLET_RE.sub("", text, count=1)


def strip_markdown_emphasis(text: str) -> str:
    """"**核心要点**" -> "核心要点" (outside inline code)."""
    if "**" not in (text or ""):
        return text
    return _outside_protected(text, lambda part: MARKDOWN_EMPHASIS_RE.sub(r"\1", part))


def replace_rendering(text: str, minority: str, majority: str) -> str:
    """One rendering of a term for another; the majority form already in the text stays as it is."""
    if not minority or minority == majority or minority not in text:
        return text
    placeholder = "\x00"
    protected = text.replace(majority, placeholder) if majority else text
    return protected.replace(minority, majority).replace(placeholder, majority)
