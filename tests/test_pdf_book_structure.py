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


def _titled_page(document: fitz.Document, title: str, *, running_head: str | None = None) -> fitz.Page:
    page = document.new_page(width=_PAGE[0], height=_PAGE[1])
    if running_head:
        page.insert_text((72, 30), running_head, fontname="hebo", fontsize=9)
    if title:
        page.insert_text((72, 120), title, fontname="tiit", fontsize=26)
    _body(page, 160)
    return page


def _write_front_matter_book(path: Path) -> None:
    """Front matter, parts and an appendix as top-level outline entries; chapters nested in parts."""
    document = fitz.open()
    title_page = document.new_page(width=_PAGE[0], height=_PAGE[1])
    title_page.insert_text((72, 200), "Synthetic Agents Book", fontname="tibo", fontsize=26)
    for title in ("preface", "acknowledgements", "about this book", "about the authors", "about the cover illustration"):
        _titled_page(document, title)
    part_one = document.new_page(width=_PAGE[0], height=_PAGE[1])
    part_one.insert_text((72, 150), "Part 1", fontname="tiit", fontsize=30)
    part_one.insert_text((72, 230), "Foundations", fontname="tiit", fontsize=30)
    _chapter_opener(document, "1", "Alpha basics")
    _titled_page(document, "", running_head="2 chapter 1 Alpha basics")
    _chapter_opener(document, "2", "Beta design")
    _titled_page(document, "appendix Setup")
    # The appendix's second page repeats its title as a running head.
    _titled_page(document, "", running_head="appendix Setup")
    document.set_toc(
        [
            [1, "Synthetic Agents Book", 1],
            [1, "preface", 2],
            [1, "acknowledgements", 3],
            [1, "about this book", 4],
            [1, "about the authors", 5],
            [1, "about the cover illustration", 6],
            [1, "Part 1 Foundations", 7],
            [2, "1 Alpha basics", 8],
            [2, "2 Beta design", 10],
            [1, "appendix Setup", 11],
        ]
    )
    document.save(path)


class FrontMatterOutlineTest(unittest.TestCase):
    """Each top-level front-matter outline entry is its own section, whichever lane the book takes.

    The outlined-book lane (chosen when a long book has a few multi-column
    pages) used to keep only a handful of front-matter titles from the top of
    the outline and fold the rest into their neighbours, so "about this book",
    "about the authors" and the part page ended up inside "acknowledgements".
    """

    _EXPECTED = [
        ("preface", 2, 2),
        ("acknowledgements", 3, 3),
        ("about this book", 4, 4),
        ("about the authors", 5, 5),
        ("about the cover illustration", 6, 6),
        ("Part 1 Foundations", 7, 7),
        ("1 Alpha basics", 8, 9),
        ("2 Beta design", 10, 10),
        ("appendix Setup", 11, 12),
    ]

    def _chapters(self, recovery_lane: str | None) -> list[tuple[str, int, int]]:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "front-matter.pdf"
            _write_front_matter_book(pdf_path)
            parser = PDFParser(image_output_dir=Path(tmpdir) / "images")
            profile = parser.profiler.profile(pdf_path).to_dict()
            profile["recovery_lane"] = recovery_lane
            parsed = parser.parse(pdf_path, profile=profile)
        return [
            (
                " ".join(chapter.title.split()),
                chapter.metadata["source_page_start"],
                chapter.metadata["source_page_end"],
            )
            for chapter in parsed.chapters
        ]

    def test_outlined_book_lane_keeps_front_matter_entries_apart(self) -> None:
        chapters = self._chapters("outlined_book")
        self.assertEqual([chapter for chapter in chapters if chapter[1] >= 2], self._EXPECTED)

    def test_default_lane_keeps_front_matter_entries_apart(self) -> None:
        chapters = self._chapters(None)
        self.assertEqual([chapter for chapter in chapters if chapter[1] >= 2], self._EXPECTED)

    def test_only_an_outline_with_parts_skips_the_flat_outline_collapse(self) -> None:
        from book_agent.ingestion.pdf.chapters import outline_groups_chapters_into_parts
        from book_agent.ingestion.pdf.models import PdfOutlineEntry

        parted = [
            PdfOutlineEntry(level=1, title="preface", page_number=2),
            PdfOutlineEntry(level=1, title="Part 1 Foundations", page_number=4),
            PdfOutlineEntry(level=2, title="1 Alpha basics", page_number=5),
        ]
        flat = [
            PdfOutlineEntry(level=1, title="Foreword", page_number=2),
            PdfOutlineEntry(level=1, title="Chapter 1 Durable Products", page_number=5),
            PdfOutlineEntry(level=1, title="Key Takeaways", page_number=6),
            PdfOutlineEntry(level=2, title="1.1 Details", page_number=6),
        ]
        self.assertTrue(outline_groups_chapters_into_parts(parted))
        self.assertFalse(outline_groups_chapters_into_parts(flat))
        self.assertFalse(outline_groups_chapters_into_parts([]))


_HYPHENATED_LINES = [
    "Each model provider returns a snippet of text, and tool calling is how the pro-",
    "vider sends back a snip-",
    "pet of metadata. The service is fault-",
    "tolerant, relies on tool-",
    "calling, runs GPT-",
    "4o and reported the Cricket World Cup tour­",
    "nament results. A fault-tolerant design matters when the tournament ends.",
]


def _write_hyphenated_paragraph(path: Path) -> None:
    document = fitz.open()
    page = document.new_page(width=_PAGE[0], height=_PAGE[1])
    writer = fitz.TextWriter(page.rect)
    font = fitz.Font("tiro")
    for index, line in enumerate(_HYPHENATED_LINES):
        # TextWriter keeps a soft hyphen (U+00AD) in the text layer, as typeset books do.
        writer.append((72, 100 + 13 * index), line, font=font, fontsize=10)
    writer.write_text(page)
    _body(page, 260, count=2)
    document.save(path)


class LineBreakHyphenTest(unittest.TestCase):
    """A word broken over two lines is one word again; a compound keeps its hyphen."""

    def test_line_break_hyphens_are_removed_and_compounds_kept(self) -> None:
        parsed = _parse(_write_hyphenated_paragraph, "hyphens.pdf")
        text = " ".join(block.text for chapter in parsed.chapters for block in chapter.blocks)
        text = " ".join(text.split())
        for joined in ("the provider sends", "a snippet of metadata", "Cricket World Cup tournament results"):
            self.assertIn(joined, text)
        for broken in ("pro-vider", "snip-pet", "tour nament", "tour-nament"):
            self.assertNotIn(broken, text)
        for compound in ("is fault-tolerant, relies", "on tool-calling, runs", "GPT-4o"):
            self.assertIn(compound, text)

    def test_evidence_comes_from_words_written_inside_a_line(self) -> None:
        from book_agent.ingestion.pdf.hyphenation import inline_word_counts, rejoin_line_break_hyphens

        counts = inline_word_counts(
            [
                "a model provider and a pro-",
                "vider with a network of generated answers to question-and-answer pairs",
                "we use tool calling in a state-of-the-art system",
            ]
        )
        # Fragments cut off by a line-end hyphen are not words of the book.
        self.assertEqual(counts["pro"], 0)
        self.assertEqual(counts["vider"], 0)
        self.assertEqual(counts["provider"], 1)
        cases = {
            "pro-vider": "provider",
            "execu-tion": "execution",
            "tool-calling": "tool-calling",
            "state-of-the-art": "state-of-the-art",
            "question-and-an-swer": "question-and-answer",
            "Neural-net-work-gener-ated": "Neural-network-generated",
            "LM-supervised": "LM-supervised",
            "GPT-4o": "GPT-4o",
            "see https://example.com/pro-vider": "see https://example.com/pro-vider",
        }
        for source, expected in cases.items():
            self.assertEqual(rejoin_line_break_hyphens(source, counts), expected, source)

    def test_table_rows_are_not_evidence_that_a_split_word_is_written_hyphenated(self) -> None:
        from types import SimpleNamespace

        from book_agent.domain.structure.pdf import _rejoin_line_break_hyphens_pass
        from book_agent.ingestion.pdf.models import PdfTextBlock, _RecoveredBlock

        def text_block(lines: list[str], *, ruled_table: bool = False) -> PdfTextBlock:
            return PdfTextBlock(
                page_number=1, block_number=0, text="\n".join(lines), bbox=(0, 0, 1, 1), line_texts=lines,
                span_count=1, line_count=len(lines), font_size_min=10, font_size_max=10, font_size_avg=10,
                ruled_table=ruled_table,
            )

        # A table cell joins its broken lines too, so its row reads "infor-mation" inside one line.
        pages = [SimpleNamespace(blocks=[
            text_block(["the flow of infor-", "mation matters, and information is data"]),
            text_block(["| Kind | infor-mation |"], ruled_table=True),
        ])]
        body = _RecoveredBlock(
            role="body", block_type=BlockType.PARAGRAPH, text="the flow of infor-mation matters", page_start=1,
            page_end=1, bbox_regions=[], reading_order_index=0, parse_confidence=1.0, flags=[], font_size_avg=10,
            source_path="x.pdf", anchor="p1",
        )
        context = SimpleNamespace(extraction=SimpleNamespace(pages=pages))
        [result] = _rejoin_line_break_hyphens_pass(None, [body], context)
        self.assertEqual(result.text, "the flow of information matters")
        self.assertIn("dehyphenated", result.flags)

    def test_word_broken_inside_a_table_cell_is_rejoined(self) -> None:
        from book_agent.ingestion.pdf.extract import _table_cell_text

        self.assertEqual(_table_cell_text("Stanford Ques­\ntion Answering\nDataset"), "Stanford Ques-tion Answering Dataset")
        self.assertEqual(_table_cell_text("GPT-\n4o | mini"), "GPT-4o / mini")


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


def _png(width: int = 40, height: int = 40) -> bytes:
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, width, height), False)
    pixmap.set_rect(pixmap.irect, (40, 90, 160))
    return pixmap.tobytes("png")


def _write_image_edge_cases(path: Path) -> None:
    document = fitz.open()
    page = document.new_page(width=_PAGE[0], height=_PAGE[1])
    top = _body(page, 60, count=2)
    # A real picture, a hairline sliver pasted along its top edge and a second
    # sliver pasted beside it: only the picture has anything to show.
    page.insert_image(fitz.Rect(100, top + 20, 400, top + 200), stream=_png())
    page.insert_image(fitz.Rect(100, top + 19.5, 400, top + 20), stream=_png(40, 2))
    page.insert_image(fitz.Rect(401, top + 20, 401.4, top + 200), stream=_png(2, 40))
    page.insert_textbox(
        fitz.Rect(100, top + 206, 460, top + 230), "Figure 2.1 A picture with slivers.", fontname="hebo", fontsize=9
    )
    # A sidebar: a tinted panel painted as borderless strips behind its text.
    panel_top = top + 260
    for offset in (0, 28, 56):
        page.draw_rect(
            fitz.Rect(90, panel_top + offset, 460, panel_top + offset + 34), color=None, fill=(0.97, 0.96, 0.91)
        )
    page.insert_text((100, panel_top + 20), "Do you always need an indexing pipeline?", fontname="hebo", fontsize=11)
    page.insert_textbox(
        fitz.Rect(100, panel_top + 30, 450, panel_top + 90),
        "Offline indexing pipelines are typically used when a knowledge base with a large amount of data is built.",
        fontname="helv",
        fontsize=9,
    )
    document.save(path)


class ImageEdgeCaseTest(unittest.TestCase):
    def test_slivers_and_panel_backgrounds_are_not_pictures(self) -> None:
        parsed = _parse(_write_image_edge_cases, "images.pdf")
        pictures = [
            block
            for chapter in parsed.chapters
            for block in chapter.blocks
            if block.block_type in {BlockType.IMAGE.value, BlockType.FIGURE.value}
        ]
        self.assertEqual(len(pictures), 1, [block.metadata.get("source_bbox_json") for block in pictures])
        x0, y0, x1, y1 = pictures[0].metadata["source_bbox_json"]["regions"][0]["bbox"]
        self.assertGreater(x1 - x0, 250)
        self.assertGreater(y1 - y0, 150)
        texts = [block.text for chapter in parsed.chapters for block in chapter.blocks]
        self.assertTrue(any("Offline indexing pipelines" in text for text in texts))

    def test_visible_image_bbox_drops_hairlines_and_off_page_placements(self) -> None:
        from book_agent.ingestion.pdf.extract import _visible_image_bbox

        self.assertIsNone(_visible_image_bbox((183.43, 342.81, 183.45, 354.84), 531, 666))
        self.assertIsNone(_visible_image_bbox((0.0, 781.3, -3988.9, 666.0), 531, 666))
        self.assertEqual(_visible_image_bbox((-5, 10, 100, 700), 531, 666), (0.0, 10.0, 100.0, 666.0))

    def test_background_panel_needs_borderless_same_colour_strips(self) -> None:
        from book_agent.ingestion.pdf.extract import _is_background_panel

        def rect(x0, y0, x1, y1, fill=(0.9, 0.9, 0.8), color=None, kind="re"):
            return {"rect": (x0, y0, x1, y1), "fill": fill, "color": color, "items": [(kind,)]}

        panel = [rect(77, 292, 329, 322), rect(77, 317, 329, 339), rect(77, 331, 329, 353)]
        self.assertTrue(_is_background_panel((77, 292, 329, 353), panel))
        bars = [rect(77, 292, 120, 353), rect(130, 310, 170, 353), rect(180, 330, 220, 353)]
        self.assertFalse(_is_background_panel((77, 292, 220, 353), bars))
        boxed = [*panel, rect(90, 300, 300, 340, fill=None, color=(0, 0, 0))]
        self.assertFalse(_is_background_panel((77, 292, 329, 353), boxed))
        drawn = [*panel, rect(90, 300, 200, 300, kind="l")]
        self.assertFalse(_is_background_panel((77, 292, 329, 353), drawn))



def _write_styled_headings_book(path: Path) -> None:
    """Manning's heading styles: "1.1" at 12.5pt bold, "1.1.1" at 10.5pt bold, "Summary" set like "1.1",
    a small bold run-in heading over its paragraph, and listing titles in the heading face."""
    document = fitz.open()
    title_page = document.new_page(width=_PAGE[0], height=_PAGE[1])
    title_page.insert_text((72, 200), "Synthetic Styles Book", fontname="tibo", fontsize=26)
    _chapter_opener(document, "1", "Alpha basics")
    for number in range(1, 4):
        page = document.new_page(width=_PAGE[0], height=_PAGE[1])
        page.insert_text((72, 30), f"{number + 2}  Alpha basics", fontname="tiro", fontsize=9)
        page.insert_text((72, 80), f"1.{number} Section {number} title", fontname="hebo", fontsize=12.5)
        top = _body(page, 92, count=2)
        page.insert_text((72, top + 14), f"1.{number}.1 Subsection {number} title", fontname="hebo", fontsize=10.5)
        top = _body(page, top + 22, count=1)
        # A run-in heading: a short bold line in a smaller size, directly over its paragraph.
        page.insert_text((72, top + 14), "how the loop works", fontname="hebo", fontsize=8)
        _body(page, top + 18, count=1)
    listing = document.new_page(width=_PAGE[0], height=_PAGE[1])
    listing.insert_text((72, 30), "6  Alpha basics", fontname="tiro", fontsize=9)
    top = _body(listing, 60, count=1)
    listing.insert_text((84, top + 14), "Listing 1.1 Loading the settings", fontname="hebo", fontsize=9)
    listing.insert_text((72, top + 34), "from dotenv import load_dotenv", fontname="cour", fontsize=8)
    listing.insert_text((72, top + 44), "load_dotenv()", fontname="cour", fontsize=8)
    summary = document.new_page(width=_PAGE[0], height=_PAGE[1])
    summary.insert_text((72, 30), "7  Summary", fontname="tiro", fontsize=9)
    # The section heading opens the page, under the running head that repeats its word.
    summary.insert_text((72, 60), "Summary", fontname="hebo", fontsize=12.5)
    summary.insert_text((72, 80), "Data loading", fontname="hebo", fontsize=10.5)
    _body(summary, 88, count=2)
    later = document.new_page(width=_PAGE[0], height=_PAGE[1])
    later.insert_text((72, 30), "8  Summary", fontname="tiro", fontsize=9)
    _body(later, 60, count=3)
    document.set_toc([[1, "1 Alpha basics", 2]])
    document.save(path)


class StyledHeadingTest(unittest.TestCase):
    """Headings found by the book's own heading styles take the level of the numbered headings set that way."""

    @classmethod
    def setUpClass(cls) -> None:
        parsed = _parse(_write_styled_headings_book, "styles.pdf")
        cls.blocks = [block for chapter in parsed.chapters for block in chapter.blocks]

    def _headings(self) -> dict[str, int | None]:
        return {
            " ".join(block.text.split()): block.metadata.get("heading_level")
            for block in self.blocks
            if block.block_type == BlockType.HEADING.value
        }

    def test_summary_and_its_subheading_are_headings_of_their_styles_levels(self) -> None:
        headings = self._headings()
        self.assertEqual(headings.get("Summary"), 2)
        self.assertEqual(headings.get("Data loading"), 3)
        self.assertEqual(headings.get("1.1 Section 1 title"), 2)
        self.assertEqual(headings.get("1.1.1 Subsection 1 title"), 3)

    def test_a_run_in_heading_set_smaller_is_a_level_below_the_numbered_ones(self) -> None:
        self.assertEqual(self._headings().get("how the loop works"), 4)

    def test_a_listing_title_in_the_heading_face_is_not_a_heading(self) -> None:
        self.assertFalse([text for text in self._headings() if text.startswith("Listing")])



class DemiboldFaceTest(unittest.TestCase):
    def test_the_short_name_of_a_demibold_cut_is_bold(self) -> None:
        from book_agent.ingestion.pdf.extract import _span_is_bold

        # Manning sets its headings in ITC Franklin Gothic's demibold cut, named "-Dem".
        self.assertTrue(_span_is_bold({"font": "ITCFranklinGothicStd-Dem", "flags": 0}))
        self.assertTrue(_span_is_bold({"font": "FranklinGothic-DemiBold", "flags": 0}))
        self.assertFalse(_span_is_bold({"font": "ITCFranklinGothicStd-Book", "flags": 0}))
        self.assertFalse(_span_is_bold({"font": "Democratica-Regular", "flags": 0}))



def _write_listing_then_prose_page(path: Path) -> None:
    """A listing, then the prose explaining it with identifiers set in the code face (Agent book p49)."""
    document = fitz.open()
    page = document.new_page(width=_PAGE[0], height=_PAGE[1])
    top = _body(page, 60, count=1)
    for index, line in enumerate(
        [
            "import asyncio",
            "semaphore = asyncio.Semaphore(10)",
            "async def call_llm(prompt: str) -> str:",
            "    async with semaphore:",
            "        return await acompletion(model=MODEL, messages=prompt)",
        ]
    ):
        page.insert_text((72, top + 12 + 10 * index), line, fontname="cour", fontsize=8)
    y = top + 80
    for parts in (
        [("tiro", "The "), ("cour", "call_llm"), ("tiro", " function wraps the call with both protections. The semaphore")],
        [("tiro", "ensures that only ten calls run at the same time. When one task completes, the next")],
        [("tiro", "waiting task proceeds. The "), ("cour", "num_retries"), ("tiro", " parameter retries failed requests.")],
    ):
        x = 72.0
        for font, text in parts:
            size = 8 if font == "cour" else 10
            page.insert_text((x, y), text, fontname=font, fontsize=size)
            x += fitz.get_text_length(text, fontname=font, fontsize=size)
        y += 13
    document.save(path)


class ListingProseTest(unittest.TestCase):
    def test_prose_after_a_listing_with_code_identifiers_stays_prose(self) -> None:
        parsed = _parse(_write_listing_then_prose_page, "listing-prose.pdf")
        blocks = [block for chapter in parsed.chapters for block in chapter.blocks]
        prose = [block for block in blocks if "function wraps the call" in block.text]
        self.assertEqual([block.block_type for block in prose], [BlockType.PARAGRAPH.value])
        self.assertTrue(any("semaphore = asyncio.Semaphore(10)" in block.text for block in blocks if block.block_type == BlockType.CODE.value))


if __name__ == "__main__":
    unittest.main()
