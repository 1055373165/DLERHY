# ruff: noqa: E402
"""Tests for PDF multi-column reading-order reordering (PDF v2 M1.3).

Before this change, column-major ordering was gated on the academic-paper
recovery lane. A two-column page on any other lane (magazine, newsletter,
technical book with two-column interior) silently went through top-down
y-sort, producing scrambled paragraphs like "left-col line 1, right-col
line 1, left-col line 2, right-col line 2, …". The fix generalizes
column detection to all lanes while preserving the conservative
grouping-failure fallback.

See tasks/pdf-pipeline-v2.md §M1.3 and spec §3.1 failure mode 2.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.structure.pdf import PdfStructureRecoveryService
from book_agent.ingestion.pdf.models import PdfFileProfile, PdfPage, PdfTextBlock


def _mk_block(
    *,
    block_number: int,
    text: str,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    page_number: int = 1,
) -> PdfTextBlock:
    return PdfTextBlock(
        page_number=page_number,
        block_number=block_number,
        text=text,
        bbox=(x0, y0, x1, y1),
        line_texts=[text],
        span_count=20,
        line_count=1,
        font_size_min=10.0,
        font_size_max=10.0,
        font_size_avg=10.0,
    )


def _book_profile() -> PdfFileProfile:
    # Deliberately NON-academic lane; historically this would have skipped
    # column-major ordering and produced an interleaved sequence.
    return PdfFileProfile(
        pdf_kind="text",
        page_count=1,
        has_extractable_text=True,
        outline_present=False,
        layout_risk="normal",
        ocr_required=False,
        recovery_lane="default_book",
    )


def _filler_prose(seed: str) -> str:
    # Each column-candidate block needs ≥40 normalized chars to qualify.
    base = (
        "This paragraph contains plenty of substantive prose to qualify "
        "as a column-candidate block under the multi-column signature."
    )
    return f"{seed} {base}"


class PdfMultiColumnReorderTests(unittest.TestCase):
    def test_two_column_page_reorders_left_then_right(self) -> None:
        # Page width 600, two columns: left [40..280], right [320..560].
        # Four blocks per column, interleaved vertically.
        blocks = [
            _mk_block(
                block_number=1,
                text=_filler_prose("LEFT-1"),
                x0=40, y0=100, x1=280, y1=140,
            ),
            _mk_block(
                block_number=2,
                text=_filler_prose("RIGHT-1"),
                x0=320, y0=105, x1=560, y1=145,
            ),
            _mk_block(
                block_number=3,
                text=_filler_prose("LEFT-2"),
                x0=40, y0=160, x1=280, y1=200,
            ),
            _mk_block(
                block_number=4,
                text=_filler_prose("RIGHT-2"),
                x0=320, y0=165, x1=560, y1=205,
            ),
            _mk_block(
                block_number=5,
                text=_filler_prose("LEFT-3"),
                x0=40, y0=220, x1=280, y1=260,
            ),
            _mk_block(
                block_number=6,
                text=_filler_prose("RIGHT-3"),
                x0=320, y0=225, x1=560, y1=265,
            ),
        ]
        page = PdfPage(
            page_number=1,
            width=600.0,
            height=800.0,
            blocks=blocks,
        )
        service = PdfStructureRecoveryService()

        ordered = service._ordered_page_blocks(page, _book_profile())
        ordered_labels = [b.text.split(" ", 1)[0] for b in ordered]

        # After column-major reordering the three LEFT blocks must all
        # appear before the three RIGHT blocks. The prior top-down sort
        # would have produced LEFT-1, RIGHT-1, LEFT-2, RIGHT-2, LEFT-3,
        # RIGHT-3 — a regression we explicitly guard against here.
        left_indices = [i for i, label in enumerate(ordered_labels) if label.startswith("LEFT")]
        right_indices = [i for i, label in enumerate(ordered_labels) if label.startswith("RIGHT")]
        self.assertEqual(
            len(left_indices), 3, f"unexpected left count: {ordered_labels}"
        )
        self.assertEqual(
            len(right_indices), 3, f"unexpected right count: {ordered_labels}"
        )
        self.assertLess(
            max(left_indices),
            min(right_indices),
            f"columns interleaved instead of column-major: {ordered_labels}",
        )
        # Within each column, top-to-bottom order preserved.
        self.assertEqual(ordered_labels[left_indices[0]], "LEFT-1")
        self.assertEqual(ordered_labels[left_indices[1]], "LEFT-2")
        self.assertEqual(ordered_labels[left_indices[2]], "LEFT-3")
        self.assertEqual(ordered_labels[right_indices[0]], "RIGHT-1")
        self.assertEqual(ordered_labels[right_indices[1]], "RIGHT-2")
        self.assertEqual(ordered_labels[right_indices[2]], "RIGHT-3")

    def test_single_column_page_unchanged(self) -> None:
        # Four full-width blocks — must NOT trip the column-major path,
        # because the multi-column signature needs narrow candidate blocks.
        blocks = [
            _mk_block(
                block_number=i,
                text=_filler_prose(f"FULL-{i}"),
                x0=40, y0=100 + 40 * i, x1=560, y1=130 + 40 * i,
            )
            for i in range(1, 5)
        ]
        page = PdfPage(
            page_number=1,
            width=600.0,
            height=800.0,
            blocks=blocks,
        )
        service = PdfStructureRecoveryService()

        ordered = service._ordered_page_blocks(page, _book_profile())
        ordered_labels = [b.text.split(" ", 1)[0] for b in ordered]
        self.assertEqual(ordered_labels, ["FULL-1", "FULL-2", "FULL-3", "FULL-4"])


if __name__ == "__main__":
    unittest.main()


class ColumnOrderSurvivesPageEntriesTests(unittest.TestCase):
    """The page's entries (text and pictures together) keep the column order: Art of AI's two-column
    index was read column by column and then sorted by height again, interleaving the columns."""

    def _two_column_page(self, image_blocks=()):
        blocks = []
        for row in range(3):
            y = 100 + row * 60
            blocks.append(_mk_block(block_number=2 * row + 1, text=_filler_prose(f"LEFT-{row + 1}"), x0=40, y0=y, x1=280, y1=y + 40))
            blocks.append(_mk_block(block_number=2 * row + 2, text=_filler_prose(f"RIGHT-{row + 1}"), x0=320, y0=y + 5, x1=560, y1=y + 45))
        return PdfPage(page_number=1, width=600.0, height=800.0, blocks=blocks, image_blocks=list(image_blocks))

    def _labels(self, entries):
        return [("IMG" if kind == "image" else block.text.split(" ", 1)[0]) for kind, block in entries]

    def test_text_entries_stay_column_by_column(self) -> None:
        entries = PdfStructureRecoveryService()._ordered_page_content_entries(self._two_column_page(), _book_profile())
        self.assertEqual(self._labels(entries), ["LEFT-1", "LEFT-2", "LEFT-3", "RIGHT-1", "RIGHT-2", "RIGHT-3"])

    def test_a_picture_keeps_its_place_in_its_column(self) -> None:
        from book_agent.ingestion.pdf.models import PdfImageBlock

        image = PdfImageBlock(page_number=1, block_number=9, bbox=(330.0, 152.0, 550.0, 158.0))
        entries = PdfStructureRecoveryService()._ordered_page_content_entries(self._two_column_page([image]), _book_profile())
        self.assertEqual(self._labels(entries), ["LEFT-1", "LEFT-2", "LEFT-3", "RIGHT-1", "IMG", "RIGHT-2", "RIGHT-3"])

    def test_stacked_blocks_with_different_indents_are_not_columns(self) -> None:
        """A heading set left of its indented paragraphs only stacks (Art of AI p69): keep the order."""
        blocks = []
        for row in range(4):
            y = 100 + row * 80
            blocks.append(_mk_block(block_number=2 * row + 1, text=_filler_prose(f"HEAD-{row + 1}"), x0=40, y0=y, x1=200, y1=y + 15))
            blocks.append(_mk_block(block_number=2 * row + 2, text=_filler_prose(f"BODY-{row + 1}"), x0=250, y0=y + 20, x1=560, y1=y + 70))
        page = PdfPage(page_number=1, width=600.0, height=800.0, blocks=blocks)
        entries = PdfStructureRecoveryService()._ordered_page_content_entries(page, _book_profile())
        self.assertEqual(self._labels(entries), [f"{kind}-{row}" for row in range(1, 5) for kind in ("HEAD", "BODY")])
