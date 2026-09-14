"""Font-emphasis driven heading recovery for PDF text layers that carry styles.

Books converted by tools like calibre put a bold section heading and the
following body lines into one text block. Splitting such blocks by word
patterns alone produced fake headings ("As John" | "Murphy explains ...",
"Relative Strength" | "Index or RSI ..."). When the text layer distinguishes
bold from plain lines, the split follows the styled lines instead.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import fitz

from book_agent.domain.enums import BlockType
from book_agent.domain.structure.pdf import PDFParser
from book_agent.ingestion.pdf.classify import (
    leading_emphasis_line_count,
    styled_heading_and_remainder,
)


def _write_styled_book_pdf(path: Path) -> None:
    document = fitz.open()
    document.new_page().insert_text((72, 200), "Momentum Trading Notes", fontname="hebo", fontsize=24)
    page = document.new_page()
    y = 100.0

    def line(text: str, *, font: str = "helv", size: float = 10.0, gap: float = 12.0) -> None:
        nonlocal y
        page.insert_text((72, y), text, fontname=font, fontsize=size)
        y += gap

    line("Why Momentum Matters", font="hebo", size=11.5)
    line("As John Murphy explains in his classic reference, momentum measures the rate")
    line("of change of prices rather than the price level itself.", gap=20.0)
    line("Relative Strength Index or RSI was invented by an engineer who described it")
    line("in a book about technical trading systems published in 1978.", gap=20.0)
    line("However, Andrew Cardwell reads the indicator differently and treats the")
    line("overbought zone as a sign of strength in an established uptrend.")
    document.save(path)


class LeadingEmphasisLineCountTest(unittest.TestCase):
    def test_counts_bold_prefix_followed_by_plain_lines(self) -> None:
        self.assertEqual(leading_emphasis_line_count(((11.5, True), (10.0, False), (10.0, False))), 1)

    def test_counts_larger_prefix_lines(self) -> None:
        self.assertEqual(leading_emphasis_line_count(((14.0, False), (14.0, False), (10.0, False))), 2)

    def test_rejects_uniform_or_interleaved_emphasis(self) -> None:
        self.assertEqual(leading_emphasis_line_count(((10.0, False), (10.0, False))), 0)
        self.assertEqual(leading_emphasis_line_count(((10.0, True), (10.0, True))), 0)
        self.assertEqual(leading_emphasis_line_count(((10.0, True), (10.0, False), (10.0, True))), 0)
        self.assertEqual(leading_emphasis_line_count(((10.0, True),)), 0)

    def test_styled_heading_requires_matching_prefix(self) -> None:
        self.assertEqual(
            styled_heading_and_remainder("What is RSI?\nSimply speaking, RSI is an oscillator.", "What is RSI?"),
            ("What is RSI?", "Simply speaking, RSI is an oscillator."),
        )
        self.assertIsNone(styled_heading_and_remainder("Other text first", "What is RSI?"))
        self.assertIsNone(styled_heading_and_remainder("What is RSI?", "What is RSI?"))


class StyledHeadingRecoveryTest(unittest.TestCase):
    def test_bold_line_becomes_heading_and_plain_prose_stays_intact(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "styled.pdf"
            _write_styled_book_pdf(pdf_path)
            parsed = PDFParser(image_output_dir=Path(tmpdir) / "images").parse(pdf_path)

        blocks = [block for chapter in parsed.chapters for block in chapter.blocks]
        headings = [block.text for block in blocks if block.block_type == BlockType.HEADING.value]
        paragraphs = [block.text for block in blocks if block.block_type == BlockType.PARAGRAPH.value]

        self.assertIn("Why Momentum Matters", headings)
        heading = next(block for block in blocks if block.text == "Why Momentum Matters")
        self.assertEqual(heading.metadata["pdf_heading_recovery_source"], "embedded_book_styled_heading_recovered")
        self.assertTrue(any(text.startswith("As John Murphy explains") for text in paragraphs))
        self.assertTrue(any(text.startswith("Relative Strength Index or RSI") for text in paragraphs))
        self.assertTrue(any(text.startswith("However, Andrew Cardwell") for text in paragraphs))
        self.assertNotIn("As John", headings)
        self.assertNotIn("Relative Strength", headings)


if __name__ == "__main__":
    unittest.main()
