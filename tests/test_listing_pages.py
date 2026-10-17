"""Contents, index and reference pages read as lists, and text inside a figure is the figure's.

How Large Language Models Work blocked its export on all of these:
- its contents put each page number on a line of its own ("preface" / "x"), and its continuation
  pages were known as contents only by their running head ("CONTENTS vii", or "viii" / "CONTENTS"
  at the top of the page's only block): the entries became five tables the export could not draw;
- its index wraps an entry's page list onto the next line ("...152–154," / "157–158");
- its numbered references on a page read as contents became tables;
- figures 6.2 and 6.3 (code with a box around each token) also came out as pipe tables;
- every page of its two-column index was blocking "reading order may be scrambled".
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from book_agent.domain.enums import BlockType
from book_agent.domain.structure.pdf import (
    _CONTENTS_RUNNING_HEAD,
    _absorb_tables_inside_figures_pass,
    _reads_as_listing_entries,
)


class ListingEntryTests(unittest.TestCase):
    def test_a_page_number_on_its_own_line_still_makes_an_entry(self) -> None:
        self.assertTrue(
            _reads_as_listing_entries(
                ["preface", "x", "acknowledgments", "xii", "about this book", "xiv", "about the authors", "xvii"]
            )
        )
        self.assertTrue(
            _reads_as_listing_entries(["• LLMs and language equity", "26", "2.4", "Check your understanding", "27"])
        )

    def test_an_index_entry_whose_pages_wrap_onto_the_next_line(self) -> None:
        self.assertTrue(
            _reads_as_listing_entries(
                [
                    "and LLM training data 69, 145, 152–154,",
                    "157–158",
                    "public domain and 152, 155–156",
                    "See also copyright",
                ]
            )
        )

    def test_a_table_of_numbers_is_not_a_listing(self) -> None:
        self.assertFalse(_reads_as_listing_entries(["Model", "Parameters", "Context", "Price"]))

    def test_a_contents_running_head(self) -> None:
        for text in ("CONTENTS vii", "viii CONTENTS", "contents"):
            self.assertTrue(_CONTENTS_RUNNING_HEAD.fullmatch(text), text)
        self.assertIsNone(_CONTENTS_RUNNING_HEAD.fullmatch("Table of figures 12"))


def _block(block_type: BlockType, bbox: list[float], anchor: str, page: int = 113) -> SimpleNamespace:
    return SimpleNamespace(
        block_type=block_type,
        page_start=page,
        page_end=page,
        bbox_regions=[{"page_number": page, "bbox": bbox}],
        metadata={},
        flags=[],
        anchor=anchor,
    )


class _Service:
    @staticmethod
    def _page_bbox(block: SimpleNamespace, page_number: int) -> list[float] | None:
        return next((region["bbox"] for region in block.bbox_regions if region["page_number"] == page_number), None)


class TablesInsideFiguresTests(unittest.TestCase):
    def test_a_table_inside_a_figure_is_the_figures_text(self) -> None:
        figure = _block(BlockType.FIGURE, [101.0, 49.0, 451.0, 155.0], "fig")
        inner = _block(BlockType.TABLE, [268.0, 58.0, 444.0, 82.0], "tbl-in")
        outside = _block(BlockType.TABLE, [101.0, 300.0, 451.0, 400.0], "tbl-out")
        other_page = _block(BlockType.TABLE, [268.0, 58.0, 444.0, 82.0], "tbl-p2", page=114)
        kept = _absorb_tables_inside_figures_pass(_Service(), [figure, inner, outside, other_page], None)
        self.assertEqual([block.anchor for block in kept], ["fig", "tbl-out", "tbl-p2"])
        self.assertEqual(figure.metadata["figure_cluster"]["absorbed_label_anchors"], ["tbl-in"])


class ListingPagesReviewTests(unittest.TestCase):
    def test_index_and_contents_pages_drop_out_of_the_flagged_pages(self) -> None:
        from book_agent.services.review import ReviewService

        def block(page: int, family: str, role: str = "body") -> SimpleNamespace:
            return SimpleNamespace(source_span_json={"source_page_start": page, "pdf_page_family": family, "pdf_block_role": role})

        bundle = SimpleNamespace(
            blocks=[
                block(192, "index"),
                block(192, "body", "header"),
                block(8, "toc"),
                block(199, "body"),
                block(199, "index"),
            ]
        )
        listing = ReviewService._listing_pages(object.__new__(ReviewService), bundle, [192, 8, 199])
        self.assertEqual(listing, {192, 8})


class ReferencePagesCorpusTests(unittest.TestCase):
    @unittest.skipUnless(__import__("os").getenv("BOOK_AGENT_RUN_CORPUS_TESTS") == "1", "Set BOOK_AGENT_RUN_CORPUS_TESTS=1 to parse the real book.")
    def test_how_large_language_models_work_keeps_its_references_out_of_the_contents(self) -> None:
        """Entries ending in page ranges ("91, 1357-1392") have contents lines' shape: the book's reference
        pages read as contents, and the reading copy (which leaves the printed contents out) lost them."""
        from pathlib import Path

        from book_agent.domain.structure.pdf import PDFParser

        path = Path("~/Downloads/ai 书籍/How Large Language Models Work (Edward Raff, Drew Farris, Stella Biderman) (z-library.sk, 1lib.sk, z-lib.sk).pdf").expanduser()
        if not path.exists():
            self.skipTest(f"corpus book not on this machine: {path}")
        parsed = PDFParser().parse(path)
        references = next(chapter for chapter in parsed.chapters if chapter.title == "References")
        families = [block.metadata.get("pdf_page_family") for block in references.blocks]
        self.assertNotIn("toc", families)
        self.assertNotIn("table", [block.block_type for block in references.blocks])


if __name__ == "__main__":
    unittest.main()
