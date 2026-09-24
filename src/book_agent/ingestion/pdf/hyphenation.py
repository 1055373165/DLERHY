"""Rejoin words a typesetter split across lines with a hyphen.

Line joining keeps a line-end hyphen ("pro-" + "vider" -> "pro-vider") because
the same glyph also ends the first half of a genuine compound broken at its
own hyphen ("fault-" + "tolerant"). Which one it was is decided here, once the
whole book's text is known, from the book itself:

* A hyphenated token that appears anywhere *inside* a single PDF line is
  written that way by the author, so it is kept wherever it occurs.
* Otherwise every occurrence was made by joining lines. If the joined word
  appears inside a line elsewhere in the book ("provider"), the hyphen was a
  line break and goes.
* With no such evidence, a split is kept as a compound when both halves are
  words of their own ("tool-" + "calling") or the first half is an acronym
  ("LM-" + "supervised"); a split whose halves are not words ("execu-" +
  "tion") is joined.

No word list is involved; the vocabulary is the book's own text.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

# A word, possibly hyphenated or with an apostrophe ("isn't", "fault-tolerant").
_WORD_RE = re.compile(r"[A-Za-z]+(?:['’-][A-Za-z]+)*")
# A hyphenated token in running text, not glued to digits or other hyphens.
_HYPHENATED_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9\-.@\\])([A-Za-z]+(?:-[A-Za-z]+)+)(?![A-Za-z0-9\-@\\]|\.[A-Za-z0-9])")
_URL_CHUNK_RE = re.compile(r"://|www\.|@")
_CHUNK_TAIL_RE = re.compile(r"\S+$")
_CHUNK_HEAD_RE = re.compile(r"\S+")


def inline_word_counts(lines: Iterable[str]) -> Counter[str]:
    """Case-folded counts of the words and hyphenated tokens that sit wholly inside one line.

    ``lines`` is the book's text lines in reading order. A fragment that a
    line-end hyphen cut off ("pro" of "pro-", "vider" at the start of the next
    line) is not a word and is not counted.
    """
    counts: Counter[str] = Counter()
    previous_ended_with_hyphen = False
    for line in lines:
        stripped = line.strip()
        ends_with_hyphen = stripped.endswith("-") and stripped[-2:-1].isalpha()
        matches = list(_WORD_RE.finditer(stripped))
        for index, match in enumerate(matches):
            if index == 0 and previous_ended_with_hyphen and match.start() == 0:
                continue
            if ends_with_hyphen and index == len(matches) - 1 and match.end() == len(stripped) - 1:
                continue
            word = match.group(0).casefold()
            counts[word] += 1
            if "-" in word:
                # The parts of a compound are words of the book as well.
                counts.update(word.split("-"))
        previous_ended_with_hyphen = ends_with_hyphen
    return counts


def rejoin_line_break_hyphens(text: str, inline_counts: Counter[str]) -> str:
    """``text`` with each hyphen that only a line break put there removed."""
    if "-" not in text:
        return text

    def seen(token: str) -> bool:
        return inline_counts.get(token.casefold(), 0) > 0

    def resolve(match: re.Match[str]) -> str:
        token = match.group(1)
        if seen(token):
            return token
        before = _CHUNK_TAIL_RE.search(text, 0, match.start())
        after = _CHUNK_HEAD_RE.match(text, match.end())
        if _URL_CHUNK_RE.search(f"{before.group(0) if before else ''}{token}{after.group(0) if after else ''}"):
            return token
        parts = token.split("-")
        # Joining at a hyphen needs the joined word to be the book's own and
        # the hyphenated pair not to be ("an-swer" in "question-and-an-swer").
        pieces = [parts[0]]
        for left, right in zip(parts, parts[1:]):
            if right[:1].islower() and seen(left + right) and not seen(f"{left}-{right}"):
                pieces[-1] += right
            else:
                pieces.append(right)
        if len(pieces) < len(parts):
            return "-".join(pieces)
        if len(parts) != 2 or not parts[1][:1].islower():
            return token
        left, right = parts
        if sum(char.isupper() for char in left) > 1:
            # Typesetters do not break an acronym or a CamelCase name ("LM-supervised").
            return token
        if seen(left) and seen(right):
            return token
        return left + right

    return _HYPHENATED_TOKEN_RE.sub(resolve, text)
