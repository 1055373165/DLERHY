# ruff: noqa: E402
"""Tests for the TATR adapter shell (TATR-a)."""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.services.tatr_extractor import (
    NoOpPageImageTableExtractor,
    PageTableExtractionRequest,
    TatrCell,
    TatrTable,
    TatrTableExtractor,
    _bbox_overlap_fraction,
    map_cell_text,
    tatr_table_to_markdown,
)


def _request(
    *,
    page_text_blocks: list[tuple] | None = None,
    page_number: int = 1,
) -> PageTableExtractionRequest:
    return PageTableExtractionRequest(
        pdf_path="/dev/null",
        page_number=page_number,
        page_dimensions=(612.0, 792.0),
        region_bbox=None,
        page_text_blocks=tuple(page_text_blocks or []),
    )


class BboxOverlapTests(unittest.TestCase):
    def test_full_containment(self) -> None:
        target = (10, 10, 50, 50)
        candidate = (0, 0, 100, 100)
        self.assertAlmostEqual(_bbox_overlap_fraction(target, candidate), 1.0, places=3)

    def test_no_overlap(self) -> None:
        self.assertEqual(
            _bbox_overlap_fraction((0, 0, 10, 10), (20, 20, 30, 30)),
            0.0,
        )

    def test_partial(self) -> None:
        # target 100x100=10000, intersection 50x100=5000 → 0.5
        target = (0, 0, 100, 100)
        candidate = (50, 0, 200, 100)
        self.assertAlmostEqual(
            _bbox_overlap_fraction(target, candidate), 0.5, places=3
        )


class MapCellTextTests(unittest.TestCase):
    def test_overlapping_block_is_concatenated(self) -> None:
        cell_bbox = (100, 100, 200, 130)
        blocks = [
            ((100, 100, 200, 130), "Alice"),
            ((300, 100, 400, 130), "Bob"),  # different cell
        ]
        self.assertEqual(map_cell_text(cell_bbox, blocks), "Alice")

    def test_partial_overlap_below_threshold_excluded(self) -> None:
        cell_bbox = (100, 100, 200, 130)
        # Block is 30 wide, only 10 inside the cell → 33% of block in
        # cell, below 0.4 threshold.
        blocks = [
            ((190, 100, 220, 130), "Spillover"),
        ]
        self.assertEqual(map_cell_text(cell_bbox, blocks), "")

    def test_multiple_blocks_joined_by_space(self) -> None:
        cell_bbox = (100, 100, 300, 130)
        blocks = [
            ((100, 100, 180, 130), "First"),
            ((200, 100, 280, 130), "Second"),
        ]
        self.assertEqual(map_cell_text(cell_bbox, blocks), "First Second")

    def test_empty_blocks_skipped(self) -> None:
        cell_bbox = (100, 100, 200, 130)
        blocks = [((100, 100, 200, 130), "  "), ((100, 100, 200, 130), "Real")]
        self.assertEqual(map_cell_text(cell_bbox, blocks), "Real")


class TatrTableToMarkdownTests(unittest.TestCase):
    def test_empty_cells_produces_empty_string(self) -> None:
        table = TatrTable(bbox=(0, 0, 100, 100), cells=(), confidence=0.9)
        self.assertEqual(tatr_table_to_markdown(table), "")

    def test_simple_2x2_table_renders(self) -> None:
        cells = (
            TatrCell(row=0, column=0, bbox=(0, 0, 50, 30), text="Name", is_header=True),
            TatrCell(row=0, column=1, bbox=(50, 0, 100, 30), text="Age", is_header=True),
            TatrCell(row=1, column=0, bbox=(0, 30, 50, 60), text="Alice"),
            TatrCell(row=1, column=1, bbox=(50, 30, 100, 60), text="30"),
        )
        table = TatrTable(bbox=(0, 0, 100, 60), cells=cells, confidence=0.9)
        md = tatr_table_to_markdown(table)
        lines = md.splitlines()
        self.assertEqual(len(lines), 3)  # header + sep + 1 row
        self.assertIn("Name", lines[0])
        self.assertIn("Age", lines[0])
        self.assertIn("---", lines[1])
        self.assertIn("Alice", lines[2])


class NoOpExtractorTests(unittest.TestCase):
    def test_returns_empty_list(self) -> None:
        extractor = NoOpPageImageTableExtractor()
        self.assertEqual(extractor.extract(_request()), [])


class FakeTatr(TatrTableExtractor):
    """Subclass overriding the inference hook with scripted output."""

    def __init__(
        self,
        *,
        scripted: list[TatrTable] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._scripted = list(scripted or [])
        # Bypass the real lazy-load probe so tests don't depend on torch.
        self._models_loaded = True

    def _run_tatr_inference(self, request):
        return list(self._scripted)


class TatrTableExtractorTests(unittest.TestCase):
    def test_no_deps_returns_empty_and_marks_metric(self) -> None:
        # Real adapter without subclassing — torch is missing in test
        # env, so deps probe should fail gracefully.
        adapter = TatrTableExtractor()
        result = adapter.extract(_request())
        self.assertEqual(result, [])
        self.assertTrue(adapter.last_metrics.deps_missing)

    def test_fake_inference_returns_table_with_mapped_cells(self) -> None:
        # Scripted TATR output: 2x2 table at (100,100)-(300,200)
        cells = (
            TatrCell(row=0, column=0, bbox=(100, 100, 200, 130), text="", is_header=True),
            TatrCell(row=0, column=1, bbox=(200, 100, 300, 130), text="", is_header=True),
            TatrCell(row=1, column=0, bbox=(100, 130, 200, 160), text=""),
            TatrCell(row=1, column=1, bbox=(200, 130, 300, 160), text=""),
        )
        scripted = [TatrTable(bbox=(100, 100, 300, 160), cells=cells, confidence=0.93)]
        page_text_blocks = [
            ((100, 100, 200, 130), "Name"),
            ((200, 100, 300, 130), "Age"),
            ((100, 130, 200, 160), "Alice"),
            ((200, 130, 300, 160), "30"),
        ]
        adapter = FakeTatr(scripted=scripted)
        results = adapter.extract(_request(page_text_blocks=page_text_blocks))
        self.assertEqual(len(results), 1)
        table = results[0]
        self.assertEqual(table.column_count, 2)
        self.assertEqual(len(table.cells), 2)
        self.assertIn("Name", table.markdown)
        self.assertIn("Alice", table.markdown)
        self.assertEqual(adapter.last_metrics.tables_returned, 1)
        self.assertEqual(adapter.last_metrics.tables_detected, 1)

    def test_cost_guard_caps_total_tables(self) -> None:
        many_tables = [
            TatrTable(
                bbox=(0, 0, 50, 50),
                cells=(
                    TatrCell(row=0, column=0, bbox=(0, 0, 25, 25), text="x"),
                    TatrCell(row=0, column=1, bbox=(25, 0, 50, 25), text="y"),
                ),
                confidence=0.9,
            )
            for _ in range(5)
        ]
        adapter = FakeTatr(scripted=many_tables, max_tables_per_doc=2)
        results = adapter.extract(_request())
        self.assertEqual(len(results), 2)
        self.assertTrue(adapter.last_metrics.cost_guard_tripped)

    def test_cost_guard_persists_across_calls(self) -> None:
        # The adapter caps cumulative output across multiple .extract calls.
        single = TatrTable(
            bbox=(0, 0, 50, 50),
            cells=(
                TatrCell(row=0, column=0, bbox=(0, 0, 25, 25), text="a"),
                TatrCell(row=0, column=1, bbox=(25, 0, 50, 25), text="b"),
            ),
            confidence=0.9,
        )
        adapter = FakeTatr(scripted=[single], max_tables_per_doc=2)
        self.assertEqual(len(adapter.extract(_request())), 1)
        self.assertEqual(len(adapter.extract(_request())), 1)
        # Third call → cap already met, returns empty + flag set.
        self.assertEqual(len(adapter.extract(_request())), 0)
        self.assertTrue(adapter.last_metrics.cost_guard_tripped)

    def test_no_text_in_any_cell_drops_table(self) -> None:
        scripted = [
            TatrTable(
                bbox=(0, 0, 100, 100),
                cells=(
                    TatrCell(row=0, column=0, bbox=(900, 900, 1000, 1000), text=""),
                ),
                confidence=0.5,
            )
        ]
        adapter = FakeTatr(scripted=scripted)
        results = adapter.extract(_request(page_text_blocks=[]))
        # Empty cells → empty markdown → table dropped.
        self.assertEqual(results, [])

    def test_inference_exception_logged_in_metrics(self) -> None:
        class ExplodingTatr(TatrTableExtractor):
            def __init__(self, **kw):
                super().__init__(**kw)
                self._models_loaded = True

            def _run_tatr_inference(self, request):
                raise RuntimeError("boom")

        adapter = ExplodingTatr()
        result = adapter.extract(_request())
        self.assertEqual(result, [])
        self.assertIsNotNone(adapter.last_metrics.error)
        self.assertIn("tatr_inference_failed", adapter.last_metrics.error)


if __name__ == "__main__":
    unittest.main()
