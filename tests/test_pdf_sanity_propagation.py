# ruff: noqa: E402
"""Tests for sanity → provenance propagation (PDF v2 M2.1).

The text-layer sanity gate (M1.2) is worthless if its verdict never
reaches the DocIR layer where the downstream router will read it. This
suite drives `PdfStructureRecoveryService._build_chapters` with fabricated
`PdfPage` inputs carrying synthetic sanity verdicts and asserts that the
resulting `ParsedBlock.provenance` reflects the verdict.
"""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.enums import BlockType
from book_agent.domain.structure.models import (
    PROVENANCE_OCR,
    PROVENANCE_TEXT_LAYER,
)
from book_agent.domain.structure.pdf import (
    PdfFileProfile,
    PdfPage,
    PdfStructureRecoveryService,
    _RecoveredBlock,
)


def _mk_recovered_block(ordinal: int, page_number: int, text: str) -> _RecoveredBlock:
    return _RecoveredBlock(
        role="body",
        block_type=BlockType.PARAGRAPH,
        text=text,
        page_start=page_number,
        page_end=page_number,
        bbox_regions=[{"page_number": page_number, "bbox": [40, 100, 500, 140]}],
        reading_order_index=ordinal,
        parse_confidence=0.9,
        flags=[],
        metadata={},
        font_size_avg=10.0,
        source_path=f"pdf://page/{page_number}",
        anchor=f"p{page_number}-b{ordinal}",
    )


def _mk_page(
    page_number: int,
    *,
    sanity: dict | None,
) -> PdfPage:
    return PdfPage(
        page_number=page_number,
        width=600.0,
        height=800.0,
        blocks=[],
        image_blocks=[],
        text_layer_sanity=sanity or {},
    )


def _profile() -> PdfFileProfile:
    return PdfFileProfile(
        pdf_kind="text",
        page_count=2,
        has_extractable_text=True,
        outline_present=False,
        layout_risk="normal",
        ocr_required=False,
        recovery_lane="default_book",
    )


class PdfSanityPropagationTests(unittest.TestCase):
    def test_sanity_failed_page_marks_blocks_as_ocr_provenance(self) -> None:
        # Page 1: sanity OK; Page 2: sanity failed (simulated PUA corruption).
        pages = [
            _mk_page(1, sanity={"ok": True, "reason": None, "metrics": {}}),
            _mk_page(
                2,
                sanity={"ok": False, "reason": "pua_high", "metrics": {"pua_ratio": 0.8}},
            ),
        ]
        recovered = [
            _mk_recovered_block(1, page_number=1, text="Clean English paragraph one " * 4),
            _mk_recovered_block(2, page_number=1, text="Clean English paragraph two " * 4),
            _mk_recovered_block(3, page_number=2, text="Corrupted-looking content " * 4),
            _mk_recovered_block(4, page_number=2, text="More corrupted content " * 4),
        ]
        service = PdfStructureRecoveryService()

        chapters = service._build_chapters(
            recovered,
            outline_entries=[],
            profile=_profile(),
            file_path="test.pdf",
            pages=pages,
        )

        self.assertGreaterEqual(len(chapters), 1)
        blocks_by_page: dict[int, list] = {}
        for chapter in chapters:
            for block in chapter.blocks:
                page = block.metadata.get("source_page_start")
                blocks_by_page.setdefault(int(page or 0), []).append(block)

        page1_blocks = blocks_by_page.get(1, [])
        page2_blocks = blocks_by_page.get(2, [])

        self.assertTrue(page1_blocks, "page 1 produced no parsed blocks")
        self.assertTrue(page2_blocks, "page 2 produced no parsed blocks")

        for block in page1_blocks:
            self.assertEqual(
                block.provenance,
                PROVENANCE_TEXT_LAYER,
                f"page 1 block should stay text_layer, got {block.provenance}",
            )
            self.assertEqual(block.confidence_breakdown.get("sanity_ok"), True)

        for block in page2_blocks:
            self.assertEqual(
                block.provenance,
                PROVENANCE_OCR,
                f"page 2 block should be ocr after sanity failure, got {block.provenance}",
            )
            self.assertEqual(block.confidence_breakdown.get("sanity_ok"), False)
            self.assertEqual(
                block.confidence_breakdown.get("sanity_reason"),
                "pua_high",
            )

    def test_pages_without_sanity_default_to_text_layer(self) -> None:
        # Missing sanity info (legacy path) must not regress: provenance
        # stays text_layer, confidence_breakdown stays empty.
        pages = [_mk_page(1, sanity=None)]
        recovered = [
            _mk_recovered_block(1, page_number=1, text="A paragraph on this page " * 4),
        ]
        service = PdfStructureRecoveryService()

        chapters = service._build_chapters(
            recovered,
            outline_entries=[],
            profile=_profile(),
            file_path="test.pdf",
            pages=pages,
        )
        all_blocks = [b for ch in chapters for b in ch.blocks]
        self.assertTrue(all_blocks)
        for block in all_blocks:
            self.assertEqual(block.provenance, PROVENANCE_TEXT_LAYER)
            self.assertEqual(block.confidence_breakdown, {})


if __name__ == "__main__":
    unittest.main()
