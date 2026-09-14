"""Bulleted lists whose bullets are vector shapes rather than text glyphs.

Some converters draw list bullets as small filled circles; the text layer then
holds only the item text, so a whole list arrived as a single paragraph with
items run together. Items are now split at lines preceded by such a mark.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import fitz

from book_agent.domain.enums import BlockType
from book_agent.domain.structure.pdf import PDFParser


def _write_vector_bullet_pdf(path: Path) -> None:
    document = fitz.open()
    document.new_page().insert_text((72, 200), "Momentum Trading Notes", fontname="hebo", fontsize=24)
    page = document.new_page()
    page.insert_text((72, 100), "The indicator panel has three zones that every trader should learn to read quickly.")
    items = [
        ["The shaded area from 30 to 70 shows the normal range, where the reading is neither", "overbought nor oversold."],
        ["Readings below 30 are considered oversold."],
        ["The centre line sits at 50, the exact middle of the scale."],
    ]
    y = 124.0
    for item in items:
        page.draw_circle((78.5, y - 3.5), 1.5, color=None, fill=(0, 0, 0))
        for line in item:
            page.insert_text((90, y), line)
            y += 12
    page.insert_text((72, y + 14), "Now we will watch how the line moves between these zones on a daily chart.")
    document.save(path)


class VectorBulletListTest(unittest.TestCase):
    def test_vector_bullets_split_block_into_list_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "bullets.pdf"
            _write_vector_bullet_pdf(pdf_path)
            parsed = PDFParser(image_output_dir=Path(tmpdir) / "images").parse(pdf_path)

        blocks = [(block.block_type, block.text) for chapter in parsed.chapters for block in chapter.blocks]
        self.assertIn(
            (BlockType.PARAGRAPH.value, "The indicator panel has three zones that every trader should learn to read quickly."),
            blocks,
        )
        self.assertEqual(
            [text for block_type, text in blocks if block_type == BlockType.LIST_ITEM.value],
            [
                "• The shaded area from 30 to 70 shows the normal range, where the reading is neither\n"
                "overbought nor oversold.",
                "• Readings below 30 are considered oversold.",
                "• The centre line sits at 50, the exact middle of the scale.",
            ],
        )
        self.assertIn(
            (BlockType.PARAGRAPH.value, "Now we will watch how the line moves between these zones on a daily chart."),
            blocks,
        )


if __name__ == "__main__":
    unittest.main()
