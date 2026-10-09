"""Inline emphasis as it travels through translation: ``*em*``, ``**strong**`` and ```code```.

The EPUB parser writes ``<em>``/``<i>`` as ``*x*``, ``<strong>``/``<b>`` as ``**x**`` and
``<code>`` as ```x``` (``domain.structure.epub._extract_rich_text``) so the model can keep the
emphasis around the same words. The reading formats turn the paired markers back into tags; a
reader must never see the asterisks.

Pairing is deliberately conservative, so the asterisks that are text stay text:

* code spans are never looked into (```*args```, ```2*3```);
* an opening star is not preceded by an ASCII letter, digit, ``_``, ``*`` or ``\\`` and is
  followed by a non-space (``2*3*4``, ``2 * 3`` and ``a*b`` are arithmetic, not emphasis);
* a closing star follows a non-space and is not followed by an ASCII letter, digit or ``_``,
  unless the emphasised words end in punctuation (``*arXiv,*2404.16244``: the book's own
  missing space);
* a star run only closes a run of the same length (``*args`` and ``**kwargs`` never pair up);
* a lone star (a footnote mark, ``*args`` in prose) has nothing to pair with and stays.

One quirk of real books is accepted: a space inside the closing marker before a number
(``<em>arXiv, </em>2502.02649`` read as ``*arXiv, *2502.02649``) pairs, and the space is moved
outside the emphasis.

Chinese has no ASCII word characters, so ``其*智能体程序*（agent program）`` pairs as expected.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

# A Markdown code span: a run of backticks, content, the same run again ("`find_dotenv()`").
CODE_SPAN_RE = re.compile(r"(?<!`)(`+)(?!`)(?P<code>.*?[^`\s].*?)(?<!`)\1(?!`)")

_CODE_OPEN, _CODE_CLOSE = "", ""
_STRONG_OPEN, _STRONG_CLOSE = "", ""
_EM_OPEN, _EM_CLOSE = "", ""
_MARK_CHARS = _CODE_OPEN + _CODE_CLOSE + _STRONG_OPEN + _STRONG_CLOSE + _EM_OPEN + _EM_CLOSE


def _pair_re(marker: str, inner: str) -> re.Pattern[str]:
    star = re.escape(marker)
    return re.compile(
        rf"(?<![A-Za-z0-9_*\\]){star}(?![\s*])"
        rf"(?P<inner>{inner}*?)"
        r"(?:"
        rf"(?<=[^\s*]){star}(?![A-Za-z0-9_*])"  # an ordinary close
        rf"|(?<=[,.;:]){star}(?=[A-Za-z0-9])"  # "*arXiv,*2404": the book left out the space
        rf"|(?<=[,.;:])(?P<gap>[ \t]+){star}(?=\d)"  # "*arXiv, *2502": the space went inside
        r")"
    )


# Strong words may hold an emphasised one ("**a *b* c**"); emphasis is matched after strong.
_STRONG_RE = _pair_re("**", r"(?:[^*\n]|(?<!\*)\*(?!\*))")
_EM_RE = _pair_re("*", r"[^*\n]")


@dataclass(frozen=True, slots=True)
class MarkedText:
    """Text with its code spans and emphasis pairs replaced by private-use placeholders."""

    text: str
    # Each code span's code, and the span as it was written (its backticks).
    codes: tuple[str, ...]
    originals: tuple[str, ...] = ()


def _mask_code(text: str) -> MarkedText:
    codes: list[str] = []
    originals: list[str] = []

    def keep(match: re.Match[str]) -> str:
        codes.append(match.group("code").strip())
        originals.append(match.group(0))
        return f"{_CODE_OPEN}{len(codes) - 1}{_CODE_CLOSE}"

    return MarkedText(CODE_SPAN_RE.sub(keep, text), tuple(codes), tuple(originals))


def _mark_pairs(text: str) -> str:
    def strong(match: re.Match[str]) -> str:
        return f"{_STRONG_OPEN}{match.group('inner')}{_STRONG_CLOSE}{match.group('gap') or ''}"

    def em(match: re.Match[str]) -> str:
        return f"{_EM_OPEN}{match.group('inner')}{_EM_CLOSE}{match.group('gap') or ''}"

    return _EM_RE.sub(em, _STRONG_RE.sub(strong, text))


def mark(text: str, *, code_spans: bool = True) -> MarkedText:
    """``text`` with its paired markers (and, with ``code_spans``, its code spans) as placeholders."""
    text = (text or "").translate({ord(char): None for char in _MARK_CHARS})
    masked = _mask_code(text) if code_spans else MarkedText(text, ())
    return MarkedText(_mark_pairs(masked.text), masked.codes, masked.originals)


def render_html(
    text: str,
    escape: Callable[[str], str],
    *,
    code_spans: bool = True,
    code_html: Callable[[str], str] | None = None,
) -> str:
    """``text`` as HTML: emphasis pairs as ``<em>``/``<strong>``, code spans through ``code_html``.

    ``escape`` escapes the plain text between the markup (it never sees a marker).
    """
    marked = mark(text, code_spans=code_spans)
    escaped = escape(marked.text)
    escaped = (
        escaped.replace(_STRONG_OPEN, "<strong>")
        .replace(_STRONG_CLOSE, "</strong>")
        .replace(_EM_OPEN, "<em>")
        .replace(_EM_CLOSE, "</em>")
    )
    if not marked.codes:
        return escaped
    render_code = code_html or (lambda code: f"<code>{escape(code)}</code>")
    return re.sub(
        rf"{_CODE_OPEN}(\d+){_CODE_CLOSE}",
        lambda match: render_code(marked.codes[int(match.group(1))]),
        escaped,
    )


def plain_text(text: str, *, code_spans: bool = True) -> str:
    """What a reader reads: paired markers dropped, code spans as their code."""
    marked = mark(text, code_spans=code_spans)
    plain = marked.text
    for char in (_STRONG_OPEN, _STRONG_CLOSE, _EM_OPEN, _EM_CLOSE):
        plain = plain.replace(char, "")
    return re.sub(rf"{_CODE_OPEN}(\d+){_CODE_CLOSE}", lambda match: marked.codes[int(match.group(1))], plain)


def pair_counts(text: str) -> tuple[int, int]:
    """(strong pairs, em pairs) outside code spans."""
    marked = mark(text).text
    return marked.count(_STRONG_OPEN), marked.count(_EM_OPEN)


def _without_code(text: str) -> str:
    return CODE_SPAN_RE.sub(" ", text or "")


_CJK = "㐀-鿿豈-﫿　-〿＀-￯"
# What a stray emphasis marker looks like once pairing is done: a double star, or a star against
# a Chinese character or full-width punctuation ("其*智能体程序（agent", "注意力**"), or an opening
# star after a curly quote whose closing star the translation lost ("Needs，”*arXiv，2504").
# "**kwargs" is Python's keyword splat, not a marker.
STRAY_MARKER_RE = re.compile(rf"\*\*+(?![A-Za-z_])|(?<=[{_CJK}])\*|\*(?=[{_CJK}])|(?<=[“”‘’])\*(?=[A-Za-z])")
# Text that would read as an emphasis pair but did not pair ("*arXiv, *2502" with letters after).
_PAIR_LOOKALIKE_RE = re.compile(r"(?<![A-Za-z0-9_*\\])\*[^\s*][^*\n]{0,80}?[^\s*]\*(?!\*)")


def literal_markers(plain: str) -> list[str]:
    """Marker-like asterisks in text a reader sees (no pairing: what is there is shown as is).

    Only marker-like stars count: a run of two or more, a star touching Chinese text, or two
    stars around words the way a pair is written. ``2*3``, ``*args``, ``**kwargs`` and a
    footnote star are text, not leftovers.
    """
    return [match.group(0) for match in STRAY_MARKER_RE.finditer(plain or "")] + [
        match.group(0) for match in _PAIR_LOOKALIKE_RE.finditer(plain or "")
    ]


def stray_markers(text: str, *, code_spans: bool = True) -> list[str]:
    """Emphasis markers a reader would see as asterisks once the paired ones are rendered."""
    marked = mark(text, code_spans=code_spans)
    rest = re.sub(rf"{_CODE_OPEN}\d+{_CODE_CLOSE}", " ", marked.text)
    for char in (_STRONG_OPEN, _STRONG_CLOSE, _EM_OPEN, _EM_CLOSE):
        rest = rest.replace(char, " ")
    return literal_markers(rest)


def unbalanced_against(target: str, sources: Sequence[str]) -> list[str]:
    """Stray markers in a translation beyond those its source text already has."""
    found = stray_markers(target)
    if not found:
        return []
    allowed = len([marker for source in sources for marker in stray_markers(source)])
    return found[allowed:] if allowed else found


def drop_stray_markers(target: str, sources: Sequence[str]) -> str:
    """The translation with stray emphasis markers its sources do not have removed (code untouched).

    A pair the sources have no kind of (the model set a phrase in ``**bold**`` for a PDF book)
    is unwrapped too: the reader would see emphasis the book does not have.
    """
    if "*" not in (target or ""):
        return target
    source_strong = sum(pair_counts(source)[0] for source in sources)
    source_em = sum(pair_counts(source)[1] for source in sources)
    source_strays = sum(len(stray_markers(source)) for source in sources)
    marked = mark(target)
    text = marked.text
    if not source_strong:
        text = text.replace(_STRONG_OPEN, "").replace(_STRONG_CLOSE, "")
    if not source_em:
        text = text.replace(_EM_OPEN, "").replace(_EM_CLOSE, "")
    if not source_strays:
        text = STRAY_MARKER_RE.sub("", text)
    restored = (
        text.replace(_STRONG_OPEN, "**")
        .replace(_STRONG_CLOSE, "**")
        .replace(_EM_OPEN, "*")
        .replace(_EM_CLOSE, "*")
    )
    return re.sub(rf"{_CODE_OPEN}(\d+){_CODE_CLOSE}", lambda match: marked.originals[int(match.group(1))], restored)
