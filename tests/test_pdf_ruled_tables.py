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


class PipeTableParsingTest(unittest.TestCase):
    def test_delimited_rows_keep_empty_cells_in_their_columns(self) -> None:
        self.assertEqual(
            parse_structured_table_rows("| Day | Close | Gain |\n| 1 | 80 | |\n| 2 | | 20 |"),
            (["Day", "Close", "Gain"], [["1", "80", ""], ["2", "", "20"]]),
        )


if __name__ == "__main__":
    unittest.main()
