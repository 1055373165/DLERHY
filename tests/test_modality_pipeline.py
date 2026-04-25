# ruff: noqa: E402
"""Tests for the M3 modality pipeline orchestrator (TATR-c)."""

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
    ParsedChapter,
    ParsedDocument,
)
from book_agent.services.modality_pipeline import (
    ModalityPipelineOptions,
    enhance_parsed_document,
)
from book_agent.services.tatr_extractor import (
    PageTableExtractionRequest,
    TatrCell,
    TatrTable,
    TatrTableExtractor,
)


def _block(
    *,
    block_type: str,
    text: str,
    ordinal: int,
    anchor: str | None = None,
    metadata: dict | None = None,
    translatability: str = TRANSLATE_ALL,
) -> ParsedBlock:
    return ParsedBlock(
        block_type=block_type,
        text=text,
        source_path="x",
        ordinal=ordinal,
        anchor=anchor or f"b{ordinal}",
        metadata=metadata or {},
        translatability=translatability,
    )


def _doc(
    chapters: list[tuple[str, list[ParsedBlock]]],
    *,
    metadata: dict | None = None,
) -> ParsedDocument:
    return ParsedDocument(
        title="T",
        author="A",
        language="en",
        chapters=[
            ParsedChapter(chapter_id=f"ch{i+1}", href=f"h{i+1}", title=t, blocks=b)
            for i, (t, b) in enumerate(chapters)
        ],
        metadata=metadata or {},
    )


class FakeTatrAdapter(TatrTableExtractor):
    def __init__(self, *, scripted: list[TatrTable], **kw) -> None:
        super().__init__(**kw)
        self._scripted_per_call = list(scripted)
        self._models_loaded = True

    def _run_tatr_inference(self, request):
        return list(self._scripted_per_call)


class DefaultsAreNoOpTests(unittest.TestCase):
    def test_no_options_means_no_changes(self) -> None:
        doc = _doc(
            [
                (
                    "Chapter 1",
                    [
                        _block(block_type="paragraph", text="Body.", ordinal=1),
                        _block(block_type="figure", text="[Image]", ordinal=2),
                    ],
                )
            ]
        )
        rewritten, summary = enhance_parsed_document(doc)
        # Default options: every modality skipped.
        self.assertEqual(
            sorted(summary.skipped_due_to_disabled),
            ["equations", "images", "references", "tables"],
        )
        # Document remains untouched (figure block translatability NOT flipped).
        self.assertEqual(
            rewritten.chapters[0].blocks[1].translatability,
            TRANSLATE_ALL,
        )


class ReferencesEnabledTests(unittest.TestCase):
    def test_references_protection_runs(self) -> None:
        doc = _doc(
            [
                (
                    "Chapter 1",
                    [
                        _block(block_type="paragraph", text="Intro.", ordinal=1),
                        _block(block_type="heading", text="References", ordinal=2),
                        _block(
                            block_type="paragraph",
                            text="Smith, J. (2018). Foo. Bar.",
                            ordinal=3,
                        ),
                    ],
                )
            ]
        )
        opts = ModalityPipelineOptions(enable_references=True)
        rewritten, summary = enhance_parsed_document(doc, options=opts)
        blocks = rewritten.chapters[0].blocks
        self.assertEqual(blocks[0].translatability, TRANSLATE_ALL)
        self.assertEqual(blocks[1].translatability, TRANSLATE_NONE)
        self.assertEqual(blocks[2].translatability, TRANSLATE_NONE)
        self.assertIsNotNone(summary.references)
        self.assertEqual(summary.references.block_count, 1)
        self.assertNotIn("references", summary.skipped_due_to_disabled)


class EquationsEnabledTests(unittest.TestCase):
    def test_equation_block_becomes_translate_none(self) -> None:
        doc = _doc(
            [
                (
                    "Chapter 1",
                    [
                        _block(
                            block_type="equation",
                            text="E = m c^2",
                            ordinal=1,
                        )
                    ],
                )
            ]
        )
        rewritten, summary = enhance_parsed_document(
            doc, options=ModalityPipelineOptions(enable_equations=True)
        )
        block = rewritten.chapters[0].blocks[0]
        self.assertEqual(block.translatability, TRANSLATE_NONE)
        self.assertIn("equation_render_mode", block.metadata)
        self.assertEqual(summary.equations_enhanced, 1)


class TablesHeuristicEnabledTests(unittest.TestCase):
    TABLE_TEXT = (
        "Name           Age      Score\n"
        "Alice          30       95\n"
        "Bob            25       82\n"
        "Charlie        35       77"
    )

    def test_heuristic_recovers_markdown_for_clean_table(self) -> None:
        doc = _doc(
            [
                (
                    "Chapter 1",
                    [
                        _block(
                            block_type="table",
                            text=self.TABLE_TEXT,
                            ordinal=1,
                        )
                    ],
                )
            ]
        )
        rewritten, summary = enhance_parsed_document(
            doc, options=ModalityPipelineOptions(enable_tables=True)
        )
        block = rewritten.chapters[0].blocks[0]
        self.assertEqual(block.translatability, TRANSLATE_NONE)
        self.assertIn("table_markdown", block.metadata)
        self.assertEqual(summary.table_blocks_enhanced, 1)
        self.assertEqual(summary.table_markdowns_recovered, 1)

    def test_table_block_protected_even_without_recovery(self) -> None:
        doc = _doc(
            [
                (
                    "Chapter 1",
                    [
                        _block(
                            block_type="table",
                            text="ambiguous prose here",
                            ordinal=1,
                        )
                    ],
                )
            ]
        )
        rewritten, summary = enhance_parsed_document(
            doc, options=ModalityPipelineOptions(enable_tables=True)
        )
        block = rewritten.chapters[0].blocks[0]
        self.assertEqual(block.translatability, TRANSLATE_NONE)
        self.assertNotIn("table_markdown", block.metadata)
        self.assertEqual(summary.table_markdowns_recovered, 0)


class TatrPostPassTests(unittest.TestCase):
    def test_tatr_recovers_markdown_when_heuristic_misses(self) -> None:
        # Heuristic-unfriendly input that recovery still classified as
        # `table` (e.g., from upstream TSR detection).
        doc = _doc(
            [
                (
                    "Chapter 1",
                    [
                        _block(
                            block_type="table",
                            text="data",
                            ordinal=1,
                            anchor="t1",
                            metadata={
                                "source_page_start": 1,
                                "source_bbox_json": {
                                    "regions": [
                                        {
                                            "page_number": 1,
                                            "bbox": [50, 50, 500, 200],
                                        }
                                    ]
                                },
                            },
                        ),
                        _block(
                            block_type="paragraph",
                            text="Alice",
                            ordinal=2,
                            anchor="p1",
                            metadata={
                                "source_page_start": 1,
                                "source_bbox_json": {
                                    "regions": [
                                        {
                                            "page_number": 1,
                                            "bbox": [60, 90, 240, 110],
                                        }
                                    ]
                                },
                            },
                        ),
                        _block(
                            block_type="paragraph",
                            text="30",
                            ordinal=3,
                            anchor="p2",
                            metadata={
                                "source_page_start": 1,
                                "source_bbox_json": {
                                    "regions": [
                                        {
                                            "page_number": 1,
                                            "bbox": [260, 90, 480, 110],
                                        }
                                    ]
                                },
                            },
                        ),
                    ],
                )
            ],
            metadata={"source_path": "test.pdf"},
        )
        scripted = [
            TatrTable(
                bbox=(50, 50, 500, 200),
                cells=(
                    TatrCell(row=0, column=0, bbox=(60, 90, 240, 110), text=""),
                    TatrCell(row=0, column=1, bbox=(260, 90, 480, 110), text=""),
                ),
                confidence=0.95,
            )
        ]
        adapter = FakeTatrAdapter(scripted=scripted)
        opts = ModalityPipelineOptions(
            enable_tables=True,
            page_image_table_extractor=adapter,
        )
        rewritten, summary = enhance_parsed_document(doc, options=opts)
        table_block = rewritten.chapters[0].blocks[0]
        self.assertIn("table_markdown", table_block.metadata)
        self.assertIn("Alice", table_block.metadata["table_markdown"])
        self.assertIn("30", table_block.metadata["table_markdown"])
        self.assertEqual(table_block.metadata.get("table_recovered_via"), "tatr")
        self.assertEqual(summary.tatr_tables_recovered, 1)

    def test_tatr_does_not_override_heuristic_when_present(self) -> None:
        # The heuristic recovers markdown first; TATR must NOT overwrite it.
        clean_table_text = (
            "Name           Age      Score\n"
            "Alice          30       95\n"
            "Bob            25       82\n"
            "Charlie        35       77"
        )
        doc = _doc(
            [
                (
                    "Chapter 1",
                    [
                        _block(
                            block_type="table",
                            text=clean_table_text,
                            ordinal=1,
                            anchor="t1",
                            metadata={
                                "source_page_start": 1,
                                "source_bbox_json": {
                                    "regions": [
                                        {
                                            "page_number": 1,
                                            "bbox": [50, 50, 500, 200],
                                        }
                                    ]
                                },
                            },
                        )
                    ],
                )
            ],
            metadata={"source_path": "test.pdf"},
        )
        # Even though we provide a TATR adapter, heuristic produced
        # markdown so post-pass should be skipped.
        adapter = FakeTatrAdapter(
            scripted=[
                TatrTable(
                    bbox=(50, 50, 500, 200),
                    cells=(
                        TatrCell(row=0, column=0, bbox=(60, 60, 200, 90), text="X"),
                    ),
                    confidence=0.99,
                )
            ]
        )
        opts = ModalityPipelineOptions(
            enable_tables=True,
            page_image_table_extractor=adapter,
        )
        rewritten, summary = enhance_parsed_document(doc, options=opts)
        markdown = rewritten.chapters[0].blocks[0].metadata["table_markdown"]
        self.assertIn("Alice", markdown)
        self.assertNotIn("table_recovered_via", rewritten.chapters[0].blocks[0].metadata)
        self.assertEqual(summary.tatr_tables_recovered, 0)


class ImagesEnabledTests(unittest.TestCase):
    def test_image_pass_protects_figures_and_re_enables_captions(self) -> None:
        doc = _doc(
            [
                (
                    "Chapter 1",
                    [
                        _block(
                            block_type="figure",
                            text="",
                            ordinal=1,
                            metadata={"image_alt": "Diagram"},
                        ),
                        _block(
                            block_type="caption",
                            text="Figure 1",
                            ordinal=2,
                            translatability=TRANSLATE_NONE,
                        ),
                    ],
                )
            ]
        )
        rewritten, summary = enhance_parsed_document(
            doc, options=ModalityPipelineOptions(enable_images=True)
        )
        blocks = rewritten.chapters[0].blocks
        self.assertEqual(blocks[0].translatability, TRANSLATE_NONE)
        self.assertEqual(blocks[1].translatability, TRANSLATE_ALL)
        self.assertIsNotNone(summary.images)


class FullPipelineTests(unittest.TestCase):
    def test_all_modalities_compose_correctly(self) -> None:
        doc = _doc(
            [
                (
                    "Chapter 1",
                    [
                        _block(block_type="paragraph", text="Intro.", ordinal=1),
                        _block(
                            block_type="equation",
                            text="E = mc^2",
                            ordinal=2,
                        ),
                        _block(
                            block_type="figure",
                            text="",
                            ordinal=3,
                            metadata={"image_alt": "Diagram"},
                        ),
                        _block(block_type="heading", text="References", ordinal=4),
                        _block(
                            block_type="paragraph",
                            text="Smith, J. (2018). Foo. Bar.",
                            ordinal=5,
                        ),
                    ],
                )
            ]
        )
        opts = ModalityPipelineOptions(
            enable_references=True,
            enable_equations=True,
            enable_tables=True,
            enable_images=True,
        )
        rewritten, summary = enhance_parsed_document(doc, options=opts)
        blocks = rewritten.chapters[0].blocks
        self.assertEqual(blocks[0].translatability, TRANSLATE_ALL)  # paragraph
        self.assertEqual(blocks[1].translatability, TRANSLATE_NONE)  # equation
        self.assertEqual(blocks[2].translatability, TRANSLATE_NONE)  # figure
        self.assertEqual(blocks[3].translatability, TRANSLATE_NONE)  # heading "References"
        self.assertEqual(blocks[4].translatability, TRANSLATE_NONE)  # ref entry
        self.assertEqual(summary.skipped_due_to_disabled, [])
        self.assertEqual(summary.equations_enhanced, 1)
        self.assertIsNotNone(summary.references)
        self.assertIsNotNone(summary.images)


if __name__ == "__main__":
    unittest.main()
