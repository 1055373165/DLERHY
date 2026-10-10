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


class PipeTableParsingTest(unittest.TestCase):
    def test_delimited_rows_keep_empty_cells_in_their_columns(self) -> None:
        self.assertEqual(
            parse_structured_table_rows("| Day | Close | Gain |\n| 1 | 80 | |\n| 2 | | 20 |"),
            (["Day", "Close", "Gain"], [["1", "80", ""], ["2", "", "20"]]),
        )


if __name__ == "__main__":
    unittest.main()
