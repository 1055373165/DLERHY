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


def _write_split_title_pdf(path: Path) -> None:
    """Chapter openers whose large bold title lines are separate text blocks (sparse pages)."""
    document = fitz.open()
    document.new_page().insert_text((72, 200), "Momentum Trading Notes", fontname="hebo", fontsize=24)
    page = document.new_page()
    page.insert_text((214, 143), "CHAPTER 2: BIRTH AND", fontname="hebo", fontsize=20)
    page.insert_text((250, 167), "GROWTH OF RSI", fontname="hebo", fontsize=20)
    page.insert_text((126, 210), "'Momentum is perhaps the most important factor in trading.'", fontname="helv", fontsize=14)
    page.insert_text((126, 300), "The chapter covers what the indicator is and why it was invented.", fontname="helv")
    page = document.new_page()
    page.insert_text((180, 130), "CHAPTER 3: BREAKING THE MYTH:", fontname="hebo", fontsize=16.5)
    page.insert_text((200, 150), "RSI CAN REMAIN", fontname="hebo", fontsize=16.5)
    page.insert_text((200, 166), "OVERSOLD FOR", fontname="hebo", fontsize=16.5)
    page.insert_text((230, 190), "SEVERAL MONTHS?", fontname="hebo", fontsize=16.5)
    page.insert_text((126, 210), "Experts say that the indicator can stay in a zone for months at a time.", fontname="helv")
    page.insert_text((126, 240), "Well, that does not happen on daily charts of the index.", fontname="helv")
    document.save(path)


def _write_numbered_section_pdf(path: Path) -> None:
    """Numbered bold section headings, one sharing a block with its body and one on its own line."""
    document = fitz.open()
    document.new_page().insert_text((72, 200), "Momentum Trading Notes", fontname="hebo", fontsize=24)
    page = document.new_page()
    page.insert_text((72, 100), "1. Tops and Bottoms (top at 70 and bottom at 30)", fontname="hebo", fontsize=11.5)
    page.insert_text((72, 116), "According to Wilder, tops and bottoms are indicated when the reading crosses a zone.")
    page.insert_text((72, 128), "Such tops usually form before the actual market top.")
    page.insert_text((72, 170), "2. Failure Swings", fontname="hebo", fontsize=11.5)
    page.insert_text((72, 200), "When the indicator does not exceed its previous high, it is called a failure swing.")
    page.draw_circle((78.5, 236.5), 1.5, color=None, fill=(0, 0, 0))
    page.insert_text((90, 240), "Bullish Engulfing and Bearish Engulfing", fontname="hebo")
    page.insert_text((90, 252), "If you find them at the extremes of a divergence, the signal is more reliable.")
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

    def test_bold_title_lines_in_separate_blocks_form_one_chapter_heading(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "titles.pdf"
            _write_split_title_pdf(pdf_path)
            parsed = PDFParser(image_output_dir=Path(tmpdir) / "images").parse(pdf_path)

        blocks = [block for chapter in parsed.chapters for block in chapter.blocks]
        headings = [
            (block.text, block.metadata.get("heading_level"))
            for block in blocks
            if block.block_type == BlockType.HEADING.value
        ]
        self.assertIn(("CHAPTER 2: BIRTH AND GROWTH OF RSI", 1), headings)
        self.assertIn(("CHAPTER 3: BREAKING THE MYTH: RSI CAN REMAIN OVERSOLD FOR SEVERAL MONTHS?", 1), headings)
        self.assertIn("'Momentum is perhaps the most important factor in trading.'", [block.text for block in blocks])

    def test_numbered_bold_headings_split_from_list_items_but_bullets_do_not(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "numbered.pdf"
            _write_numbered_section_pdf(pdf_path)
            parsed = PDFParser(image_output_dir=Path(tmpdir) / "images").parse(pdf_path)

        blocks = [(block.block_type, " ".join(block.text.split())) for chapter in parsed.chapters for block in chapter.blocks]
        self.assertIn((BlockType.HEADING.value, "1. Tops and Bottoms (top at 70 and bottom at 30)"), blocks)
        self.assertIn((BlockType.HEADING.value, "2. Failure Swings"), blocks)
        self.assertTrue(
            any(block_type == BlockType.PARAGRAPH.value and text.startswith("According to Wilder") for block_type, text in blocks)
        )
        self.assertTrue(
            any(
                block_type == BlockType.LIST_ITEM.value and text.startswith("\u2022 Bullish Engulfing and Bearish Engulfing If you")
                for block_type, text in blocks
            )
        )


if __name__ == "__main__":
    unittest.main()
