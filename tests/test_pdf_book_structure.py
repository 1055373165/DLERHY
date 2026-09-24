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

    def test_chapters_nested_under_parts_become_chapters(self) -> None:
        chapters = self._chapters()
        by_title = {title: (start, end) for title, start, end in chapters}
        self.assertEqual(by_title.get("1 Alpha basics"), (5, 6))
        self.assertEqual(by_title.get("2 Beta design"), (7, 7))
        self.assertEqual(by_title.get("3 Gamma methods"), (9, 9))
        # The part's own opening page stays a small section of its own.
        self.assertIn(4, [start for title, start, _end in chapters if title.startswith("Part 1")])

    def test_contents_continuation_page_is_contents_not_chapters(self) -> None:
        blocks_on_page_three = [
            block
            for chapter in self.parsed.chapters
            for block in chapter.blocks
            if block.metadata["source_page_start"] == 3
        ]
        self.assertTrue(blocks_on_page_three)
        self.assertEqual({block.metadata.get("pdf_page_family") for block in blocks_on_page_three}, {"toc"})
        self.assertEqual([start for _title, start, _end in self._chapters() if start == 3], [])
        self.assertEqual(
            [(title, start, end) for title, start, end in self._chapters() if title == "contents"],
            [("contents", 2, 3)],
        )

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


def _write_symbol_bullet_page(path: Path) -> None:
    document = fitz.open()
    page = document.new_page(width=_PAGE[0], height=_PAGE[1])
    page.insert_text((72, 80), "Summary", fontname="hebo", fontsize=12.5)
    items = ["RAG gives a model facts it was never trained on.", "Chunking splits long documents into passages."]
    for index, item in enumerate(items):
        y = 104 + 20 * index
        # Wingdings-style bullet: a symbol-font glyph that decodes to an unrelated character.
        page.insert_text((80, y), "¡", fontname="symb", fontsize=8.5)
        page.insert_text((92, y), item, fontname="tiro", fontsize=10)
    page.insert_text((80, 164), "l", fontname="zadb", fontsize=8.5)
    page.insert_text((92, 164), "Dingbat bullets are list markers as well.", fontname="tiro", fontsize=10)
    _body(page, 200, count=2)
    document.save(path)


class SymbolFontBulletTest(unittest.TestCase):
    def test_symbol_font_bullet_becomes_a_list_marker(self) -> None:
        parsed = _parse(_write_symbol_bullet_page, "bullets.pdf")
        blocks = [(block.block_type, block.text) for chapter in parsed.chapters for block in chapter.blocks]
        self.assertEqual(
            [text for block_type, text in blocks if block_type == BlockType.LIST_ITEM.value],
            [
                "• RAG gives a model facts it was never trained on.",
                "• Chunking splits long documents into passages.",
                "• Dingbat bullets are list markers as well.",
            ],
        )
        self.assertFalse([text for _block_type, text in blocks if "¡" in text])
        self.assertIn("Summary", [text for _block_type, text in blocks])


def _write_code_listing_page(path: Path) -> None:
    document = fitz.open()
    page = document.new_page(width=_PAGE[0], height=_PAGE[1])
    top = _body(page, 72, count=2)
    for comment, code in [
        ("#Installing bs4 package", "%pip install bs4==0.0.2 --quiet"),
        ("#Importing the AsyncHtmlLoader", "from langchain_community.document_loaders import AsyncHtmlLoader"),
    ]:
        page.insert_text((72, top), comment, fontname="cobo", fontsize=9)
        page.insert_text((72, top + 11), code, fontname="cour", fontsize=9)
        top += 34
    _body(page, top + 10, count=1)
    document.save(path)


class CodeCommentTest(unittest.TestCase):
    def test_bold_comment_line_in_a_monospace_block_stays_code(self) -> None:
        parsed = _parse(_write_code_listing_page, "code.pdf")
        blocks = [block for chapter in parsed.chapters for block in chapter.blocks]
        self.assertFalse([block.text for block in blocks if block.block_type == BlockType.HEADING.value])
        code_texts = [block.text for block in blocks if block.block_type == BlockType.CODE.value]
        self.assertTrue(any("#Installing bs4 package" in text and "%pip install" in text for text in code_texts))
        self.assertTrue(any("#Importing the AsyncHtmlLoader" in text for text in code_texts))


class FigureGroupContextTest(unittest.TestCase):
    """Body text after a figure stays body text; export must not fold it into the figure."""

    _CAPTION = "Figure 1.2 ChatGPT (GPT 3.5) response to the question. Source: Screenshot of the author's account."
    _FOLLOWING_BODY = (
        "ChatGPT does not have any memory of the 2023 Cricket World Cup, and it tells you to check the "
        "information from other sources. This is not ideal, but at least ChatGPT is honest in its response. "
        "The same question asked again might also provide a factually inaccurate result. Look at the response "
        "in figure 1.3. ChatGPT falsely responds that India was the winner of the tournament."
    )

    def test_body_paragraph_about_another_figure_is_not_figure_context(self) -> None:
        from book_agent.domain.structure.artifact_grouping import looks_like_artifact_group_context_text

        for academic_paper in (False, True):
            self.assertFalse(
                looks_like_artifact_group_context_text(
                    self._FOLLOWING_BODY, "image", academic_paper=academic_paper, caption_text=self._CAPTION
                )
            )

    def test_book_body_needs_to_name_the_artifact_to_join_it(self) -> None:
        from book_agent.domain.structure.artifact_grouping import looks_like_artifact_group_context_text

        topic_word_only = "The retrieval pipeline shown here is what we will build step by step in this chapter."
        self.assertFalse(
            looks_like_artifact_group_context_text(
                topic_word_only, "image", academic_paper=False, caption_text=self._CAPTION
            )
        )
        explanation = "Table 1 reports the main translation results and highlights the compute gap between models."
        self.assertTrue(
            looks_like_artifact_group_context_text(
                explanation, "table", academic_paper=False, caption_text="Table 1. Translation quality and cost."
            )
        )

    def test_export_keeps_following_body_paragraph_out_of_the_figure(self) -> None:
        from types import SimpleNamespace

        from book_agent.domain.structure.artifact_grouping import resolve_artifact_group_context_ids

        def block(block_id, block_type, text, bbox, order, **extra):
            return SimpleNamespace(
                id=block_id,
                ordinal=order,
                block_type=block_type,
                source_text=text,
                source_span_json={
                    "source_page_start": 25,
                    "source_page_end": 25,
                    "reading_order_index": order,
                    "source_bbox_json": {"regions": [{"page_number": 25, "bbox": list(bbox)}]},
                    **extra,
                },
            )

        blocks = [
            block("fig", BlockType.FIGURE, "[Figure]", (60, 101, 508, 322), 1, pdf_block_role="figure",
                  linked_caption_block_id="cap"),
            block("cap", BlockType.CAPTION, self._CAPTION, (102, 293, 456, 312), 2, pdf_block_role="caption",
                  caption_for_block_id="fig"),
            block("body", BlockType.PARAGRAPH, self._FOLLOWING_BODY, (102, 336, 477, 400), 3, pdf_block_role="body"),
        ]
        self.assertEqual(resolve_artifact_group_context_ids(blocks, academic_paper=False), {})


if __name__ == "__main__":
    unittest.main()
