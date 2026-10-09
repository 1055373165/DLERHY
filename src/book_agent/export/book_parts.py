"""A book's parts ("第 1 部分 …", "Part 1 …") and the chapters under them.

A reading copy lists a part and its chapters as a tree: the part, then its chapters one level
down, as the book's own contents do. A part runs from its title to the next part or the first
entry after it that is not a numbered chapter (an afterword, the appendixes, the index).
"""

from __future__ import annotations

import re
from collections.abc import Sequence

_NUMERAL = r"(?:\d{1,3}|[一二三四五六七八九十百]+|[IVXLC]+)"
_PART_RE = re.compile(rf"^\s*(?:第\s*{_NUMERAL}\s*部分?(?=\s|[:：]|$)|part\s+{_NUMERAL}\b)", re.IGNORECASE)
_CHAPTER_RE = re.compile(
    rf"^\s*(?:第\s*{_NUMERAL}\s*章|chapter\s+\d{{1,3}}\b|\d{{1,2}}(?:\s|\.\s|[:：]))", re.IGNORECASE
)


def is_part_title(title: str | None) -> bool:
    return bool(_PART_RE.match(title or ""))


def is_chapter_title(title: str | None) -> bool:
    return bool(_CHAPTER_RE.match(title or "")) and not is_part_title(title)


def part_depths(titles: Sequence[str | None]) -> list[int]:
    """Per entry: 1 for a numbered chapter inside a part, 0 for everything else (a part included).

    All 0 when the book has no part with a chapter under it.
    """
    depths: list[int] = []
    in_part = False
    for title in titles:
        if is_part_title(title):
            in_part = True
            depths.append(0)
        elif in_part and is_chapter_title(title):
            depths.append(1)
        else:
            in_part = False
            depths.append(0)
    return depths
