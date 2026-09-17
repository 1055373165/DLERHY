"""Table cells as translatable units: detection, segmentation, packets, and rendering (06 B-20)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from book_agent.domain.block_rules import block_is_context_translatable
from book_agent.domain.enums import BlockType
from book_agent.domain.structure.table_cells import is_translatable_cell, table_grid, translatable_cells
from book_agent.export.markup import (
    markdown_table_to_html,
    render_structured_table_html,
    render_table_html_metadata_aware,
    render_table_markdown_metadata_aware,
)

TABLE = "Tier | Latency | Notes\nBasic | 120 ms | Slow but cheap\nPro | 20 ms | API\nBasic | 90 ms | config_path=/etc/x"


class CellDetectionTests(unittest.TestCase):
    def test_words_are_translatable_numbers_codes_and_acronyms_are_not(self) -> None:
        for cell in ("Tier", "Slow but cheap", "Latency"):
            self.assertTrue(is_translatable_cell(cell), cell)
        for cell in ("", "120 ms", "20%", "API", "HTTP/2", "config_path=/etc/x", "https://x.io/a", "len(x)"):
            self.assertFalse(is_translatable_cell(cell), cell)

    def test_distinct_cells_in_reading_order_only_for_tables(self) -> None:
        text = TABLE
        self.assertEqual(translatable_cells("table", text, {}), ["Tier", "Latency", "Notes", "Basic", "Slow but cheap", "Pro"])
        self.assertEqual(translatable_cells(BlockType.TABLE, text, {"translatable": False}), [])
        self.assertEqual(translatable_cells("paragraph", text, {}), [])
        markdown = "| Name | Role |\n| --- | --- |\n| Alice | Maintainer |"
        self.assertEqual(table_grid("ignored", {"table_markdown": markdown}), [["Name", "Role"], ["Alice", "Maintainer"]])

    def test_tables_with_cell_text_join_translation_packets(self) -> None:
        text = TABLE
        table = SimpleNamespace(block_type=BlockType.TABLE, source_text=text, source_span_json={})
        numbers = SimpleNamespace(block_type=BlockType.TABLE, source_text="1 | 2\n3 | 4", source_span_json={})
        self.assertTrue(block_is_context_translatable(table))
        self.assertFalse(block_is_context_translatable(numbers))


class CellRenderingTests(unittest.TestCase):
    translations = {"Tier": "层级", "Slow but cheap": "慢但便宜"}

    def test_structured_and_markdown_tables_substitute_translations(self) -> None:
        text = TABLE
        html = render_structured_table_html(text, cell_translations=self.translations)
        self.assertIn(">层级</th>", html)
        self.assertIn(">慢但便宜</td>", html)
        self.assertIn(">120 ms</td>", html)
        self.assertNotIn("层级", render_structured_table_html(text))
        markdown = "| Tier | Latency |\n| --- | --- |\n| Basic | Slow but cheap |"
        self.assertIn("<th>层级</th>", markdown_table_to_html(markdown, cell_translations=self.translations))
        metadata = {"table_markdown": markdown, "table_cell_translations": self.translations}
        self.assertIn("<td>慢但便宜</td>", render_table_html_metadata_aware(metadata, ""))
        rendered_md = render_table_markdown_metadata_aware(metadata, "")
        self.assertIn("| 层级 | Latency |", rendered_md)
        self.assertIn("| --- | --- |", rendered_md)
        self.assertEqual(render_table_markdown_metadata_aware({"table_markdown": markdown}, ""), markdown)


if __name__ == "__main__":
    unittest.main()
