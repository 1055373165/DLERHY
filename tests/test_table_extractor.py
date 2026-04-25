# ruff: noqa: E402
"""Tests for M3.2 table modality."""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.structure.models import (
    TRANSLATE_ALL,
    TRANSLATE_NONE,
    ParsedBlock,
)
from book_agent.services.table_extractor import (
    HeuristicTableExtractor,
    TableStructure,
    enhance_block_for_table,
    extract_table_structure,
    looks_like_table,
)


CLEAN_TABLE_TEXT = """\
Name           Age      Score
Alice          30       95
Bob            25       82
Charlie        35       77
"""


class LooksLikeTableTests(unittest.TestCase):
    def test_clear_table_qualifies(self) -> None:
        self.assertTrue(looks_like_table(CLEAN_TABLE_TEXT))

    def test_normal_prose_rejected(self) -> None:
        prose = "This is a normal paragraph. It has no columnar structure to speak of."
        self.assertFalse(looks_like_table(prose))

    def test_too_few_rows_rejected(self) -> None:
        self.assertFalse(looks_like_table("Name    Age\nAlice    30"))

    def test_empty_input_rejected(self) -> None:
        self.assertFalse(looks_like_table(""))


class ExtractTableStructureTests(unittest.TestCase):
    def test_three_column_table_extracted(self) -> None:
        result = extract_table_structure(CLEAN_TABLE_TEXT)
        self.assertIsNotNone(result)
        self.assertEqual(result.column_count, 3)
        self.assertEqual(len(result.cells), 4)  # header + 3 rows
        self.assertEqual(result.cells[0][0], "Name")
        self.assertEqual(result.cells[1][0], "Alice")
        self.assertEqual(result.cells[2][2], "82")
        self.assertGreater(result.confidence, 0.6)

    def test_markdown_rendering_has_pipes_and_separator(self) -> None:
        result = extract_table_structure(CLEAN_TABLE_TEXT)
        self.assertIsNotNone(result)
        lines = result.markdown.splitlines()
        self.assertTrue(lines[0].startswith("|"))
        self.assertTrue(lines[1].startswith("|"))
        self.assertIn("---", lines[1])
        self.assertEqual(lines[1].count("---"), 3)

    def test_inconsistent_columns_rejected(self) -> None:
        # Lines with wildly different column counts → no table.
        text = "alpha beta gamma\nonecolumn\nx y z w v u t"
        self.assertIsNone(extract_table_structure(text))

    def test_prose_returns_none(self) -> None:
        prose = (
            "This paragraph has multiple words but no columnar layout, "
            "and recovery should not invent a table out of it."
        )
        self.assertIsNone(extract_table_structure(prose))

    def test_too_short_returns_none(self) -> None:
        self.assertIsNone(extract_table_structure("a   b\n1   2"))


class HeuristicAdapterTests(unittest.TestCase):
    def test_adapter_returns_table_for_clean_input(self) -> None:
        adapter = HeuristicTableExtractor()
        result = adapter.extract(CLEAN_TABLE_TEXT)
        self.assertIsInstance(result, TableStructure)

    def test_adapter_returns_none_for_prose(self) -> None:
        adapter = HeuristicTableExtractor()
        self.assertIsNone(adapter.extract("Just a paragraph."))


class EnhanceBlockTests(unittest.TestCase):
    def _table_block(self) -> ParsedBlock:
        return ParsedBlock(
            block_type="table",
            text=CLEAN_TABLE_TEXT,
            source_path="x",
            ordinal=1,
            anchor="t1",
            translatability=TRANSLATE_ALL,  # incorrectly initially
        )

    def test_table_block_becomes_translate_none(self) -> None:
        block, _structure = enhance_block_for_table(self._table_block())
        self.assertEqual(block.translatability, TRANSLATE_NONE)

    def test_table_block_metadata_carries_markdown(self) -> None:
        block, structure = enhance_block_for_table(self._table_block())
        self.assertIsNotNone(structure)
        self.assertIn("table_markdown", block.metadata)
        self.assertIn("|", block.metadata["table_markdown"])
        self.assertEqual(block.metadata["table_column_count"], 3)
        self.assertGreater(block.metadata["table_confidence"], 0.6)

    def test_non_table_block_passes_through_unchanged(self) -> None:
        para = ParsedBlock(
            block_type="paragraph",
            text="Plain prose.",
            source_path="x",
            ordinal=1,
            anchor="p1",
        )
        out, structure = enhance_block_for_table(para)
        self.assertIs(out, para)
        self.assertIsNone(structure)

    def test_table_block_with_unrecoverable_text_still_protected(self) -> None:
        # Ambiguous text classified as table — even when structure
        # recovery fails, we must still flip translatability to NONE
        # because half-translated table cells are worse than verbatim.
        block = ParsedBlock(
            block_type="table",
            text="ambiguous prose with no columns at all just words",
            source_path="x",
            ordinal=1,
            anchor="t1",
        )
        out, structure = enhance_block_for_table(block)
        self.assertEqual(out.translatability, TRANSLATE_NONE)
        self.assertIsNone(structure)
        self.assertNotIn("table_markdown", out.metadata)


if __name__ == "__main__":
    unittest.main()
