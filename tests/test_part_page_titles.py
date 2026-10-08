"""A part page's name joins its "Part N" label instead of opening the part's introduction.

On the Agent book's part pages the name ("Building your first LLM agent", 30pt) sits under the
"Part 1" label and above a body paragraph that opens with a drop cap. The name ran into the
paragraph, so the reading copy titled the part just "第 1 部分" and its introduction began with the
part's name.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import fitz

from book_agent.domain.enums import BlockType
from book_agent.domain.structure.pdf import PDFParser

_INTRO = (
    "The term AI agent is used broadly, but the underlying idea is simple. An LLM serves as the brain, "
    "tools give it a way to act on the world, and a loop ties the two together so the agent can keep "
    "working until a task is done."
)


def _part_page(document: fitz.Document, label: str, name: list[str]) -> None:
    page = document.new_page()
    page.insert_text((380, 100), label, fontname="tiit", fontsize=30)
    page.insert_textbox(fitz.Rect(200, 130, 560, 230), "\n".join(name), fontname="tiit", fontsize=30, align=1)
    page.insert_textbox(fitz.Rect(126, 260, 480, 400), _INTRO, fontname="tiro", fontsize=10)


def _chapter_page(document: fitz.Document, title: str) -> None:
    page = document.new_page()
    page.insert_text((72, 100), title, fontname="hebo", fontsize=20)
    page.insert_textbox(fitz.Rect(72, 130, 520, 300), "An agent decides its next step from what it has seen so far. " * 4, fontsize=10)


class PartPageTitleTest(unittest.TestCase):
    def _parse(self, outline: list[list[object]]) -> list[tuple[str, str]]:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "parts.pdf"
            document = fitz.open()
            _part_page(document, "Part 1", ["Building your first", "LLM agent"])
            _chapter_page(document, "1 What is an AI agent?")
            _part_page(document, "Part 2", ["Developing advanced", "agent capabilities"])
            _chapter_page(document, "2 Adding memory")
            document.set_toc(outline)
            document.save(pdf_path)
            parsed = PDFParser(image_output_dir=Path(tmpdir) / "images").parse(pdf_path)
        return [(block.block_type, " ".join(block.text.split())) for chapter in parsed.chapters for block in chapter.blocks]

    def test_the_part_name_joins_its_label(self) -> None:
        blocks = self._parse(
            [
                [1, "Part 1 Building your first LLM agent", 1],
                [2, "1 What is an AI agent?", 2],
                # The outline names part 2 differently from its page.
                [1, "Part 2 Improving your agent", 3],
                [2, "2 Adding memory", 4],
            ]
        )
        headings = [text for kind, text in blocks if kind == BlockType.HEADING.value]
        self.assertIn("Part 1 Building your first LLM agent", headings)
        self.assertIn("Part 2 Developing advanced agent capabilities", headings)
        intros = [text for kind, text in blocks if kind == BlockType.PARAGRAPH.value and "underlying idea" in text]
        self.assertTrue(intros)
        self.assertTrue(all(text.startswith("The term AI agent") for text in intros), intros)


if __name__ == "__main__":
    unittest.main()
