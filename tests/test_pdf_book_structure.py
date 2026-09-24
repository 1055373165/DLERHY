"""Book structure that a real Manning PDF exposed.

"A Simple Guide to Retrieval Augmented Generation" nests its chapters under
"Part N" entries in the PDF outline, continues its table of contents over
several pages, credits screenshot sources in figure captions and sets each
chapter title as a huge number above a two-line title. Each test below builds
a small PDF with one of those traits and checks the parsed structure.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import fitz

from book_agent.domain.enums import BlockType
from book_agent.domain.structure.pdf import PDFParser

_PAGE = (531, 666)
_BODY = (
    "Retrieval gives a language model facts it never saw during training, and this paragraph "
    "is long enough to read as ordinary running prose in the body of the chapter."
)


def _body(page: fitz.Page, top: float, count: int = 3) -> float:
    for index in range(count):
        page.insert_textbox(
            fitz.Rect(72, top, 460, top + 40),
            f"{_BODY} Paragraph {index + 1}.",
            fontname="tiro",
            fontsize=10,
        )
        top += 52
    return top


def _chapter_opener(document: fitz.Document, number: str, title: str) -> fitz.Page:
    page = document.new_page(width=_PAGE[0], height=_PAGE[1])
    # A huge number, then the title well below it: two text blocks.
    page.insert_text((72, 170), number, fontname="tiit", fontsize=120)
    page.insert_text((72, 260), title, fontname="tiit", fontsize=30)
    _body(page, 300)
    return page


def _write_parted_book(path: Path) -> None:
    document = fitz.open()
    title_page = document.new_page(width=_PAGE[0], height=_PAGE[1])
    title_page.insert_text((72, 200), "Synthetic Parts Book", fontname="tibo", fontsize=26)

    contents = document.new_page(width=_PAGE[0], height=_PAGE[1])
    contents.insert_text((72, 90), "contents", fontname="hebo", fontsize=18)
    for index, line in enumerate(
        [
            "Part 1 Foundations ........................ 4",
            "1 Alpha basics ............................ 5",
            "2 Beta design ............................. 7",
        ]
    ):
        contents.insert_text((72, 130 + 18 * index), line, fontname="tiro", fontsize=10)

    # The contents run on without a heading or dotted leaders: "title page" lines.
    continuation = document.new_page(width=_PAGE[0], height=_PAGE[1])
    for index, line in enumerate(
        ["Part 2 Advanced topics 8", "Gamma methods 9", "Gamma setup 10", "Delta checks 11"]
    ):
        continuation.insert_text((72, 90 + 40 * index), line, fontname="tiro", fontsize=10)

    part_one = document.new_page(width=_PAGE[0], height=_PAGE[1])
    part_one.insert_text((72, 150), "Part 1", fontname="tiit", fontsize=30)
    part_one.insert_text((72, 230), "Foundations", fontname="tiit", fontsize=30)
    _body(part_one, 238)

    _chapter_opener(document, "1", "Alpha basics")

    figures = document.new_page(width=_PAGE[0], height=_PAGE[1])
    top = _body(figures, 72, count=2)
    for index in (1, 2):
        figures.draw_rect(fitz.Rect(72, top, 400, top + 90), color=(0, 0, 0), width=1)
        figures.insert_text((80, top + 40), "Screenshot", fontname="helv", fontsize=8)
        figures.insert_textbox(
            fitz.Rect(72, top + 96, 460, top + 130),
            f"Figure 1.{index} The answer to the question, 'Who won in 2023?' Source: "
            "Screenshot of the author's account on https://chat.example.com.",
            fontname="hebo",
            fontsize=9,
        )
        top += 150

    _chapter_opener(document, "2", "Beta design")

    part_two = document.new_page(width=_PAGE[0], height=_PAGE[1])
    part_two.insert_text((72, 150), "Part 2", fontname="tiit", fontsize=30)
    part_two.insert_text((72, 230), "Advanced topics", fontname="tiit", fontsize=30)
    _body(part_two, 238)

    _chapter_opener(document, "3", "Gamma methods")

    document.set_toc(
        [
            [1, "contents", 2],
            [1, "Part 1 Foundations", 4],
            [2, "1 Alpha basics", 5],
            [2, "2 Beta design", 7],
            [1, "Part 2 Advanced topics", 8],
            [2, "3 Gamma methods", 9],
        ]
    )
    document.save(path)


def _parse(write, name: str):
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / name
        write(pdf_path)
        return PDFParser(image_output_dir=Path(tmpdir) / "images").parse(pdf_path)


class PartedBookChapterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.parsed = _parse(_write_parted_book, "parted.pdf")

    def _chapters(self) -> list[tuple[str, int, int]]:
        return [
            (chapter.title, chapter.metadata["source_page_start"], chapter.metadata["source_page_end"])
            for chapter in self.parsed.chapters
        ]

    def test_captions_crediting_a_source_do_not_make_a_references_section(self) -> None:
        caption_families = {
            block.metadata.get("pdf_page_family")
            for chapter in self.parsed.chapters
            for block in chapter.blocks
            if block.block_type == BlockType.CAPTION.value
        }
        self.assertEqual(caption_families, {"body"})
        self.assertNotIn("References", [title for title, _start, _end in self._chapters()])

    def test_display_title_set_in_pieces_is_one_heading(self) -> None:
        headings = {
            block.metadata["source_page_start"]: " ".join(block.text.split())
            for chapter in self.parsed.chapters
            for block in chapter.blocks
            if block.block_type == BlockType.HEADING.value
        }
        self.assertEqual(headings.get(5), "1 Alpha basics")
        self.assertEqual(headings.get(7), "2 Beta design")
        self.assertEqual(headings.get(9), "3 Gamma methods")
        self.assertEqual(headings.get(8), "Part 2 Advanced topics")
        stray_numbers = [
            block.text
            for chapter in self.parsed.chapters
            for block in chapter.blocks
            if block.block_type == BlockType.PARAGRAPH.value and block.text.strip() in {"1", "2", "3"}
        ]
        self.assertEqual(stray_numbers, [])


if __name__ == "__main__":
    unittest.main()
