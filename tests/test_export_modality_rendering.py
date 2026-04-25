# ruff: noqa: E402
"""Tests for export-layer metadata-aware rendering.

Verifies that equation_render_mode == "latex" + equation_latex, and
table_markdown metadata keys, are surfaced into the merged-document
markdown and HTML outputs without disturbing the existing fallback
heuristics.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
os.environ.setdefault("BOOK_AGENT_TRANSLATION_BACKEND", "echo")
os.environ.setdefault("BOOK_AGENT_TRANSLATION_MODEL", "echo-worker")
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.services.export import ExportService


def _service() -> ExportService:
    # Helpers under test are pure — repository is unused. Provide a
    # MagicMock so __init__ doesn't crash on missing session.
    return ExportService(repository=MagicMock())


class EquationMarkdownAwareTests(unittest.TestCase):
    def test_latex_metadata_surfaces_dollar_block(self) -> None:
        out = _service()._render_equation_markdown_metadata_aware(
            {"equation_render_mode": "latex", "equation_latex": "E = mc^2"},
            "E = mc^2",
        )
        self.assertEqual(out, "$$\nE = mc^2\n$$")

    def test_empty_latex_falls_back_to_heuristic(self) -> None:
        out = _service()._render_equation_markdown_metadata_aware(
            {"equation_render_mode": "latex", "equation_latex": "  "},
            "x + y = z",
        )
        self.assertNotIn("$$", out)
        self.assertIn("x + y = z", out)

    def test_no_metadata_falls_back_to_heuristic(self) -> None:
        out = _service()._render_equation_markdown_metadata_aware(None, "x + y = z")
        self.assertIn("x + y = z", out)

    def test_non_latex_render_mode_falls_back(self) -> None:
        out = _service()._render_equation_markdown_metadata_aware(
            {"equation_render_mode": "verbatim_text"},
            "x + y = z",
        )
        # Falls through to heuristic; no $$ wrapping unless the text
        # itself contains LaTeX markers.
        self.assertNotIn("$$", out)


class TableMarkdownAwareTests(unittest.TestCase):
    def test_table_markdown_passthrough(self) -> None:
        md = "| Name | Age |\n| --- | --- |\n| Alice | 30 |"
        out = _service()._render_table_markdown_metadata_aware(
            {"table_markdown": md},
            "ignored source text",
        )
        self.assertEqual(out, md)

    def test_no_table_markdown_falls_back_to_heuristic(self) -> None:
        # When metadata absent, helper delegates to existing
        # _markdown_table_from_source_text. We can't predict its output
        # exactly without more setup, but we can assert no exception.
        out = _service()._render_table_markdown_metadata_aware(
            None,
            "Name           Age\nAlice          30",
        )
        # Either heuristic returned a table OR None — both are valid;
        # we just verify the call didn't raise.
        self.assertIsInstance(out, (str, type(None)))


class EquationHtmlAwareTests(unittest.TestCase):
    def test_latex_metadata_renders_katex_div(self) -> None:
        out = _service()._render_equation_html_metadata_aware(
            {"equation_render_mode": "latex", "equation_latex": "E = mc^2"},
            "E = mc^2",
        )
        self.assertIn("katex-display", out)
        self.assertIn("data-render-mode='latex-recovered'", out)
        self.assertIn("E = mc^2", out)

    def test_html_escapes_special_characters_in_latex(self) -> None:
        out = _service()._render_equation_html_metadata_aware(
            {"equation_render_mode": "latex", "equation_latex": "x < y & z > w"},
            "x < y & z > w",
        )
        self.assertIn("&lt;", out)
        self.assertIn("&gt;", out)
        self.assertIn("&amp;", out)


class TableHtmlAwareTests(unittest.TestCase):
    def test_markdown_table_passes_through_to_html(self) -> None:
        md = "| Name | Age |\n| --- | --- |\n| Alice | 30 |\n| Bob | 25 |"
        out = _service()._render_table_html_metadata_aware(
            {"table_markdown": md},
            "ignored",
        )
        self.assertIsNotNone(out)
        self.assertIn("<table", out)
        self.assertIn("Name", out)
        self.assertIn("Alice", out)
        self.assertIn("Bob", out)
        self.assertIn("data-source='m3-markdown-table'", out)

    def test_invalid_markdown_table_returns_none_or_falls_back(self) -> None:
        # Garbage markdown → helper either returns None (falls back
        # to existing structured-table heuristic on source_text) or
        # falls back internally.
        out = _service()._render_table_html_metadata_aware(
            {"table_markdown": "garbage no pipes"},
            "no source either",
        )
        # Existing fallback may also return None; the contract here is
        # "doesn't crash + doesn't emit broken HTML."
        if out is not None:
            self.assertNotIn("garbage no pipes", out)


class MarkdownToHtmlTableTests(unittest.TestCase):
    def test_well_formed_table_converted(self) -> None:
        md = "| h1 | h2 |\n| --- | --- |\n| a | b |"
        out = _service()._markdown_table_to_html(md)
        self.assertIn("<th>h1</th>", out)
        self.assertIn("<th>h2</th>", out)
        self.assertIn("<td>a</td>", out)
        self.assertIn("<td>b</td>", out)

    def test_too_few_lines_returns_none(self) -> None:
        out = _service()._markdown_table_to_html("| only one row |")
        self.assertIsNone(out)

    def test_missing_separator_returns_none(self) -> None:
        out = _service()._markdown_table_to_html("| a | b |\n| c | d |")
        self.assertIsNone(out)

    def test_html_escapes_cells(self) -> None:
        md = "| a | b |\n| --- | --- |\n| <script> | x&y |"
        out = _service()._markdown_table_to_html(md)
        self.assertIn("&lt;script&gt;", out)
        self.assertIn("x&amp;y", out)


if __name__ == "__main__":
    unittest.main()
