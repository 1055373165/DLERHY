"""Figures in a scanned book turned into text and pictures (The Wall Street Gang).

* The OCR font's metrics are broken (ascender 3.06): a 7pt line measured 27pt tall and reached into
  the chart above it, so a clean crop was reported as cutting text.
* A scanned chart's picture reaches a few points into the line of body text above it; the crop
  carried a slice of that line.
* A stretch of the foreword's printed lines was left as a picture while its words are in the text
  layer too: as a figure it showed the lines twice, cut at its edges.
"""

from __future__ import annotations

import unittest

import fitz

from book_agent.ingestion.pdf.figure_extent import (
    _glyph_height_bbox,
    complete_figure_region,
    figure_region_problems,
    is_printed_text_patch,
)

LINE = "the rally and the series of fluctuations that preceded the rally, and more of the same here."


def _png() -> bytes:
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 30), False)
    pixmap.set_rect(pixmap.irect, (200, 200, 200))
    return pixmap.tobytes("png")


class BrokenFontMetricsTests(unittest.TestCase):
    def test_a_line_in_a_font_with_broken_metrics_is_as_tall_as_its_glyphs(self) -> None:
        span = {"text": "NOTE: The term", "size": 6.9, "ascender": 3.062, "descender": -0.815,
                "origin": (72.9, 476.1), "bbox": (72.9, 454.9, 386.7, 481.8)}
        x0, y0, x1, y1 = _glyph_height_bbox((72.9, 454.9, 386.7, 481.8), [span])
        self.assertAlmostEqual(y0, 476.1 - 0.9 * 6.9, places=3)
        self.assertAlmostEqual(y1, 476.1 + 0.25 * 6.9, places=3)
        self.assertEqual((x0, x1), (72.9, 386.7))

    def test_a_line_in_a_sound_font_keeps_its_box(self) -> None:
        span = {"text": "body", "size": 10.6, "ascender": 0.77, "descender": -0.32,
                "origin": (81.1, 117.7), "bbox": (81.1, 109.5, 182.2, 121.0)}
        self.assertEqual(_glyph_height_bbox((81.1, 109.5, 182.2, 121.0), [span]), (81.1, 109.5, 182.2, 121.0))


class ScannedFigureEdgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = fitz.open()
        self.page = self.document.new_page(width=468, height=666)
        # A line of body text, then a scanned chart whose picture starts 4pt into that line.
        self.page.insert_text((60, 120), LINE, fontsize=10)
        self.page.insert_image(fitz.Rect(120, 117, 340, 300), stream=_png())
        self.page.insert_text((60, 320), LINE, fontsize=10)
        # Three printed lines, the middle of them also set as a picture.
        for y in (420, 432, 444):
            self.page.insert_text((60, y), LINE, fontsize=10)
        self.page.insert_image(fitz.Rect(70, 410, 160, 447), stream=_png())

    def tearDown(self) -> None:
        self.document.close()

    def test_the_crop_moves_off_the_line_of_text_it_reaches_into(self) -> None:
        self.assertIn("cut_text", {problem.kind for problem in figure_region_problems(self.page, (120, 117, 340, 300))})
        region = complete_figure_region(self.page, (120, 117, 340, 300))
        self.assertGreater(region[1], 117)
        self.assertLess(region[1], 135)  # a line's height at most
        self.assertEqual(figure_region_problems(self.page, region), [])

    def test_printed_lines_set_as_a_picture_are_not_a_figure(self) -> None:
        self.assertTrue(is_printed_text_patch(self.page, (70, 410, 160, 447)))
        self.assertFalse(is_printed_text_patch(self.page, (120, 117, 340, 300)))


    def test_a_word_or_a_column_of_page_numbers_set_as_a_picture_is_not_a_figure(self) -> None:
        # One line runs through a word-sized picture; page numbers sit inside a narrow one.
        self.page.insert_text((60, 520), LINE, fontsize=10)
        self.assertTrue(is_printed_text_patch(self.page, (150, 511, 186, 522)))
        self.page.insert_text((380, 600), "xiii", fontsize=8)
        self.assertTrue(is_printed_text_patch(self.page, (378, 592, 392, 640)))

    def test_lines_set_smaller_than_the_body_still_count(self) -> None:
        # A quotation's indented lines (not body prose) running through the picture.
        for y in (560, 572, 584):
            self.page.insert_text((80, y), "Wall Street made a pilgrimage to Washington trying to find somebody", fontsize=8)
        self.assertTrue(is_printed_text_patch(self.page, (120, 552, 134, 590)))

if __name__ == "__main__":
    unittest.main()
