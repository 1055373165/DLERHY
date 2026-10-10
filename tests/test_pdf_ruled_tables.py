"""Ruled (vector-lined) tables recovered as pipe rows instead of per-cell text.

PyMuPDF emits every cell of a ruled table as its own text line, so a
spreadsheet-style table used to reach recovery as a column of numbers that the
export layout gate rejected (TABLE_STRUCTURE_UNRENDERABLE), blocking the whole
book export. Tables continuing on the next page never merged because block
source paths are per page.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import fitz

from book_agent.domain.enums import BlockType
from book_agent.domain.structure.pdf import PDFParser
from book_agent.export.markup import parse_structured_table_rows

_ROW_HEIGHT = 18.0
_COLUMN_X = (72.0, 172.0, 272.0, 372.0)


def _draw_table(page: fitz.Page, top: float, rows: list[list[str]]) -> float:
    bottom = top + _ROW_HEIGHT * len(rows)
    for index in range(len(rows) + 1):
        y = top + _ROW_HEIGHT * index
        page.draw_line((_COLUMN_X[0], y), (_COLUMN_X[-1], y))
    for x in _COLUMN_X:
        page.draw_line((x, top), (x, bottom))
    for row_index, row in enumerate(rows):
        for column_index, cell in enumerate(row):
            if cell:
                page.insert_text(
                    (_COLUMN_X[column_index] + 4, top + _ROW_HEIGHT * (row_index + 1) - 5), cell, fontsize=9
                )
    return bottom


def _write_ruled_table_pdf(path: Path) -> None:
    document = fitz.open()
    document.new_page().insert_text((72, 200), "Momentum Trading Notes", fontname="hebo", fontsize=24)

    page = document.new_page()
    page.insert_text((72, 100), "The readings below compare the indicator across chart timeframes.")
    bottom = _draw_table(
        page,
        130,
        [["Timeframe", "RSI level", "Trend"], ["Hourly", "56.25", ""], ["Daily", "30.23", "Down"], ["Weekly", "", "Up"]],
    )
    page.insert_text((72, bottom + 30), "Notice how the shorter timeframes swing more widely than the weekly chart.")
    _draw_table(page, 690, [["Day", "Close", "Gain"], ["1", "80", ""]])

    page = document.new_page()
    _draw_table(page, 60, [["2", "100", "20"], ["3", "120", "20"]])
    page.insert_text((72, 140), "The gains stay constant, so the average gain keeps rising steadily.")
    document.save(path)


class RuledTableRecoveryTest(unittest.TestCase):
    def test_ruled_grids_become_pipe_table_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "tables.pdf"
            _write_ruled_table_pdf(pdf_path)
            parsed = PDFParser(image_output_dir=Path(tmpdir) / "images").parse(pdf_path)

        blocks = [block for chapter in parsed.chapters for block in chapter.blocks]
        tables = [block.text for block in blocks if block.block_type == BlockType.TABLE.value]
        paragraphs = [block.text for block in blocks if block.block_type == BlockType.PARAGRAPH.value]

        self.assertEqual(
            tables,
            [
                "| Timeframe | RSI level | Trend |\n| Hourly | 56.25 | |\n| Daily | 30.23 | Down |\n| Weekly | | Up |",
                "| Day | Close | Gain |\n| 1 | 80 | |\n| 2 | 100 | 20 |\n| 3 | 120 | 20 |",
            ],
        )
        self.assertTrue(any(text.startswith("The readings below compare") for text in paragraphs))
        self.assertTrue(any(text.startswith("Notice how the shorter timeframes") for text in paragraphs))
        self.assertTrue(any(text.startswith("The gains stay constant") for text in paragraphs))
        self.assertFalse(any(text.strip() in {"56.25", "Hourly"} for text in paragraphs))


def _write_shared_body_cell_pdf(path: Path) -> None:
    """A table ruled only above, under the header and at the bottom (booktabs style): its two body
    rows share one cell box, set apart by vertical space; one cell wraps onto a second line."""
    document = fitz.open()
    document.new_page().insert_text((72, 200), "Search Notes", fontname="hebo", fontsize=24)
    page = document.new_page()
    page.insert_text((72, 100), "The comparison below shows what the search tool is worth on these questions.")
    top, header_bottom, bottom = 130.0, 148.0, 210.0
    for y in (top, header_bottom, bottom):
        page.draw_line((_COLUMN_X[0], y), (_COLUMN_X[-1], y))
    for x in _COLUMN_X:
        page.draw_line((x, top), (x, bottom))
    for column, text in enumerate(["Condition", "Correct", "Total cost"]):
        page.insert_text((_COLUMN_X[column] + 4, top + 13), text, fontsize=9)
    first, second = header_bottom + 13, header_bottom + 13 + 26
    for column, text in enumerate(["With web search", "7/10", "$1.89"]):
        page.insert_text((_COLUMN_X[column] + 4, first), text, fontsize=9)
    page.insert_text((_COLUMN_X[0] + 4, first + 10), "and browsing", fontsize=9)
    for column, text in enumerate(["Without web search", "2/10", "$0.26"]):
        page.insert_text((_COLUMN_X[column] + 4, second), text, fontsize=9)
    page.insert_text((72, bottom + 30), "Searching the web lifted the score by fifty points at a modest cost.")
    document.save(path)


class SharedBodyCellTableTest(unittest.TestCase):
    def test_body_rows_sharing_one_cell_box_are_split_by_their_line_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "booktabs.pdf"
            _write_shared_body_cell_pdf(pdf_path)
            parsed = PDFParser(image_output_dir=Path(tmpdir) / "images").parse(pdf_path)

        tables = [block.text for chapter in parsed.chapters for block in chapter.blocks if block.block_type == BlockType.TABLE.value]
        self.assertEqual(
            tables,
            [
                "| Condition | Correct | Total cost |\n"
                "| With web search and browsing | 7/10 | $1.89 |\n"
                "| Without web search | 2/10 | $0.26 |"
            ],
        )

    def test_split_needs_a_gap_wrapped_lines_stay_in_their_row(self) -> None:
        from book_agent.ingestion.pdf.extract import split_merged_table_row

        cells = [(90.0, 530.0, 150.0, 600.0), (150.0, 530.0, 250.0, 600.0)]
        wrapped_only = [
            (92.0, 534.8, 140.0, 545.0, "Benchmark"),
            (152.0, 534.8, 240.0, 545.0, "Standardized public evalu-"),
            (152.0, 544.8, 240.0, 555.0, "ation sets"),
        ]
        self.assertIsNone(split_merged_table_row(wrapped_only, cells))
        two_rows = [
            *wrapped_only,
            (92.0, 570.4, 140.0, 580.6, "Custom"),
            (152.0, 570.4, 240.0, 580.6, "Internally constructed"),
            (152.0, 580.4, 240.0, 590.6, "evaluation sets"),
        ]
        self.assertEqual(
            split_merged_table_row(two_rows, cells),
            [["Benchmark", "Standardized public evalu-ation sets"], ["Custom", "Internally constructed evaluation sets"]],
        )

    def test_body_cells_find_tables_leaves_out_are_read_from_the_header_columns(self) -> None:
        """Art of AI p42: with only a header rule, find_tables boxed the body's middle column alone
        and returned None for the others; their text ("Miro AI", "AI generates ...") was lost."""
        from types import SimpleNamespace

        from book_agent.ingestion.pdf.extract import table_rows

        header = [(114.0, 281.0, 184.0, 297.0), (184.0, 281.0, 322.0, 297.0), (322.0, 281.0, 461.0, 297.0)]
        body = [None, (184.0, 297.0, 322.0, 434.0), None]
        table = SimpleNamespace(
            rows=[SimpleNamespace(cells=header), SimpleNamespace(cells=body)],
            extract=lambda: [["Miro features", "Human strengths", "AI strengths"], [None, "Users provide input. Team members apply expertise.", None]],
        )
        lines = [
            (118.0, 285.0, 170.0, 294.0, "Miro features"),
            (220.0, 285.0, 290.0, 294.0, "Human strengths"),
            (370.0, 285.0, 430.0, 294.0, "AI strengths"),
            (118.0, 303.0, 150.0, 312.0, "Miro AI"),
            (190.0, 303.0, 310.0, 312.0, "Users provide input."),
            (328.0, 303.0, 455.0, 312.0, "AI generates drafts."),
            (118.0, 340.0, 170.0, 349.0, "AI Sidekicks"),
            (190.0, 340.0, 310.0, 349.0, "Team members apply"),
            (190.0, 349.5, 310.0, 358.5, "expertise."),
            (328.0, 340.0, 455.0, 349.0, "AI offers advice."),
        ]
        self.assertEqual(
            table_rows(table, lines),
            [
                ["Miro features", "Human strengths", "AI strengths"],
                ["Miro AI", "Users provide input.", "AI generates drafts."],
                ["AI Sidekicks", "Team members apply expertise.", "AI offers advice."],
            ],
        )


_LONG = (
    "Trains the model to understand the terminology, tone and knowledge of one industry so that its drafts "
    "read like the work of someone in the field."
)


def _write_captioned_long_cell_pdf(path: Path, *, caption: str, below: bool, cell: str = _LONG) -> None:
    """Art of AI's tables: ruled under the header and at the bottom only, body cells long enough to
    read as prose, three body rows set apart by space, a caption above (Table) or below (Figure)."""
    document = fitz.open()
    document.new_page().insert_text((72, 200), "Product Notes", fontname="hebo", fontsize=24)
    page = document.new_page()
    page.insert_text((72, 90), "The techniques below are the ones the team compared before choosing one.")
    top = 130.0
    if not below:
        page.insert_text((72, top - 8), caption, fontsize=9)
    header_bottom = top + 18
    row_height = 46.0
    bottom = header_bottom + row_height * 3
    for y in (top, header_bottom, bottom):
        page.draw_line((_COLUMN_X[0], y), (_COLUMN_X[-1], y))
    for x in _COLUMN_X:
        page.draw_line((x, top), (x, bottom))
    for column, text in enumerate(["Technique", "What it does", "Pain point"]):
        page.insert_text((_COLUMN_X[column] + 4, top + 13), text, fontsize=9)
    for row in range(3):
        y = header_bottom + 6 + row * row_height
        page.insert_text((_COLUMN_X[0] + 4, y + 7), f"Technique {row + 1}", fontsize=8)
        for column in (1, 2):
            page.insert_textbox(
                fitz.Rect(_COLUMN_X[column] + 3, y, _COLUMN_X[column + 1] - 3, y + row_height - 8), cell, fontsize=6
            )
    if below:
        page.insert_text((72, bottom + 14), caption, fontsize=9)
    page.insert_text((72, bottom + 50), "As the table shows, each approach answers a different pain point.")
    document.save(path)


class TablesWithoutRowRulesTest(unittest.TestCase):
    """Art of AI: tables ruled only under the header and at the bottom read as prose."""

    def _parse(self, caption: str, *, below: bool, cell: str = _LONG):
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "tables.pdf"
            _write_captioned_long_cell_pdf(pdf_path, caption=caption, below=below, cell=cell)
            parsed = PDFParser(image_output_dir=Path(tmpdir) / "images").parse(pdf_path)
        return [block for chapter in parsed.chapters for block in chapter.blocks]

    def test_a_captioned_table_with_long_cells_is_a_table_of_its_rows(self) -> None:
        blocks = self._parse("Table 8.1 Fine-tuning techniques and how they are used", below=False)
        tables = [block for block in blocks if block.block_type == BlockType.TABLE.value]
        self.assertEqual(len(tables), 1)
        header, body = parse_structured_table_rows(tables[0].text)
        self.assertEqual(header, ["Technique", "What it does", "Pain point"])
        self.assertEqual([row[0] for row in body], ["Technique 1", "Technique 2", "Technique 3"])
        self.assertEqual(tables[0].metadata.get("linked_caption_text", "").split(" ")[:2], ["Table", "8.1"])
        self.assertFalse(any(block.block_type in {"image", "figure"} for block in blocks))

    def test_a_figure_caption_under_a_table_with_no_picture_is_the_table_s(self) -> None:
        # A scoring matrix: short cells, so the grid reads as a table without a "Table" caption.
        blocks = self._parse("Figure 2.8 This simple prioritization matrix scores each technique", below=True, cell="Score 4")
        (table,) = [block for block in blocks if block.block_type == BlockType.TABLE.value]
        caption = next(block for block in blocks if block.text.startswith("Figure 2.8"))
        self.assertEqual(caption.metadata.get("caption_for_role"), "table")
        self.assertEqual(table.metadata.get("linked_caption_text"), caption.text)

    def test_contents_and_index_lines_read_as_entries(self) -> None:
        from book_agent.domain.structure.pdf import _reads_as_listing_entries

        self.assertTrue(_reads_as_listing_entries(["4", "• Predictive AI 61", "5", "• Exploring language models 82"]))
        self.assertTrue(_reads_as_listing_entries(["AI fairness and bias mitigation 325", "GDPR 115, 254, 257, 306", "lexical search 148"]))
        self.assertFalse(_reads_as_listing_entries(["Model", "Mean", "score Omni-", "0.819", "0.835", "GPT-5 mini (2025-08-07)"]))


class SideCaptionTest(unittest.TestCase):
    def test_a_caption_beside_a_picture_level_with_its_foot_is_its_caption(self) -> None:
        """Art of AI figure 10.7: the caption is set right of the screenshot, ending level with it."""
        from book_agent.ingestion.pdf.models import PdfExtraction, PdfFileProfile, PdfImageBlock, PdfPage, PdfTextBlock

        def text(number, body, bbox, size=10.0):
            lines = body.split("\n")
            return PdfTextBlock(
                page_number=1, block_number=number, text=body, bbox=bbox, line_texts=lines, span_count=len(body),
                line_count=len(lines), font_size_min=size, font_size_max=size, font_size_avg=size,
            )

        page = PdfPage(
            page_number=1,
            width=504.0,
            height=666.0,
            blocks=[
                text(1, "To configure the output, he can adjust the length of the draft with a slider.", (57.0, 230.0, 460.0, 250.0)),
                text(2, "Figure 10.7 Before\ngenerating a draft, users\ncan configure its length\nand the desired style.", (356.0, 397.0, 445.0, 437.0), 8.0),
                text(3, "He clicks the Generate Draft button, and the AI gets to work on the section.", (57.0, 470.0, 460.0, 490.0)),
            ],
            image_blocks=[PdfImageBlock(page_number=1, block_number=4, bbox=(93.0, 263.0, 342.0, 437.0))],
        )
        extraction = PdfExtraction(title="UX", author="A", metadata={"pdf_extractor": "basic"}, outline_entries=[], pages=[page])
        profile = PdfFileProfile(
            pdf_kind="text_pdf", page_count=1, has_extractable_text=True, outline_present=False,
            layout_risk="low", ocr_required=False, extractor_kind="basic",
        )
        from book_agent.domain.structure.pdf import PdfStructureRecoveryService

        parsed = PdfStructureRecoveryService().recover("side-caption.pdf", extraction, profile)
        blocks = [block for chapter in parsed.chapters for block in chapter.blocks]
        image = next(block for block in blocks if block.block_type in {"image", "figure"})
        self.assertTrue(str(image.metadata.get("linked_caption_text", "")).startswith("Figure 10.7"))


class PipeTableParsingTest(unittest.TestCase):
    def test_delimited_rows_keep_empty_cells_in_their_columns(self) -> None:
        self.assertEqual(
            parse_structured_table_rows("| Day | Close | Gain |\n| 1 | 80 | |\n| 2 | | 20 |"),
            (["Day", "Close", "Gain"], [["1", "80", ""], ["2", "", "20"]]),
        )


if __name__ == "__main__":
    unittest.main()
