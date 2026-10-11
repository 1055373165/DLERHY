"""The source PDF a repair keeps open is read once: every attempt's QA pass reads the same tables
and figures again, which took 2–5 s a pass on the AI Product book."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import fitz

from book_agent.services.parse_health import _PdfPages


class PdfPagesCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.path = Path(self.tempdir.name) / "book.pdf"
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), "Table 1.1 Costs")
        document.set_toc([[1, "Chapter 1", 1]])
        document.save(self.path)
        document.close()

    def test_table_and_figure_reads_are_made_once(self) -> None:
        pages = _PdfPages(self.path)
        self.addCleanup(pages.close)
        with mock.patch("book_agent.ingestion.pdf.extract.ruled_table_rows_at", return_value=[["a", "b"]]) as rows, mock.patch(
            "book_agent.ingestion.pdf.figure_extent.figure_region_problems", return_value=["clipped"]
        ) as problems:
            for _ in range(3):
                self.assertEqual(pages.table_rows(1, (0, 0, 100, 100)), [["a", "b"]])
                self.assertEqual(pages.figure_problems(1, (0, 0, 50, 50), [(0, 0, 10, 10)]), ["clipped"])
            pages.table_rows(1, (0, 0, 100, 120))
        self.assertEqual(rows.call_count, 2)
        self.assertEqual(problems.call_count, 1)
        # A caller changing what it got does not change what the next pass reads.
        pages.table_rows(1, (0, 0, 100, 100))[0].append("x")
        self.assertEqual(pages.table_rows(1, (0, 0, 100, 100)), [["a", "b"]])

    def test_outline_and_page_sizes(self) -> None:
        pages = _PdfPages(self.path)
        self.addCleanup(pages.close)
        self.assertEqual([entry.title for entry in pages.outline()], ["Chapter 1"])
        self.assertEqual(pages.outline(), pages.outline())
        self.assertEqual(set(pages.page_sizes()), {1})


if __name__ == "__main__":
    unittest.main()
