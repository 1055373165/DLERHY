"""A Complete Guide To Volume Price Analysis: "Chapter One" titles, a misspelled "Foreward", and a joined
"Acknowledgments & Trader Resources".

Its foreword was folded into a "Front Matter" chapter and its acknowledgments into chapter twelve
(neither title was a known front-matter label), and parse health then blocked the book on the
"Front Matter" chapter, the pages before the first bookmark, as a contents line turned chapter,
while it checked none of the chapters (it knew "Chapter 1" but not "Chapter One").
"""

from __future__ import annotations

import unittest

from book_agent.domain.structure import parse_health as ph
from book_agent.domain.structure.parse_health import HealthChapter, OutlineEntry
from book_agent.ingestion.pdf.classify import _should_keep_book_top_level_outline_title

OUTLINE = [
    OutlineEntry(1, "Foreward", 11),
    OutlineEntry(1, "Chapter One : There’s Nothing New In Trading", 15),
    OutlineEntry(1, "Chapter Two : Why Volume", 30),
    OutlineEntry(1, "Acknowledgments & Trader Resources", 273),
]


def _chapter(ordinal: int, title: str, start: int) -> HealthChapter:
    return HealthChapter(id=f"c{ordinal}", ordinal=ordinal, title=title, page_start=start, page_end=start)


class SpelledOutlineTests(unittest.TestCase):
    def test_a_misspelled_foreword_and_a_joined_acknowledgments_stay_their_own(self) -> None:
        self.assertTrue(_should_keep_book_top_level_outline_title("Foreward"))
        self.assertTrue(_should_keep_book_top_level_outline_title("Acknowledgments & Trader Resources"))
        self.assertFalse(_should_keep_book_top_level_outline_title("Trader Resources & Tools"))

    def test_the_pages_before_the_first_bookmark_are_no_extra_chapter(self) -> None:
        chapters = [_chapter(1, "Front Matter", 1), _chapter(2, "Foreward", 11), _chapter(3, OUTLINE[1].title, 15),
                    _chapter(4, OUTLINE[2].title, 30), _chapter(5, OUTLINE[3].title, 273)]
        result = ph.check_chapters_vs_outline(chapters, OUTLINE)
        self.assertEqual(result.count, 0)
        # "Chapter One" starts the body: the chapters are checked (it was 0 of 0).
        self.assertEqual(result.denominator, 3)

    def test_a_spelled_chapter_folded_away_is_missing(self) -> None:
        chapters = [_chapter(1, "Front Matter", 1), _chapter(2, "Foreward", 11), _chapter(3, OUTLINE[1].title, 15)]
        result = ph.check_chapters_vs_outline(chapters, OUTLINE)
        self.assertEqual(result.breakdown["missing_chapter"], 2)


if __name__ == "__main__":
    unittest.main()
