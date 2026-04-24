# ruff: noqa: E402
"""Tests for OCR re-extraction adapter wiring (PDF v2 M2.3a).

Three paths are exercised end-to-end through the synthetic PUA fixture:

  1. Default (no adapter)  → behaviour unchanged from M2.1; sanity-failed
     blocks keep their (corrupted) text and provenance=OCR advisory.
  2. NoOp adapter          → same outcome; adapter returned {} so nothing
     to replace.
  3. Fake adapter          → returns replacement text for every request;
     the block text is rewritten, sanity_ok flips True, provenance stays
     PROVENANCE_OCR, and reextracted_via tag is stamped.
"""

import os
import sys
import tempfile
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
    ParsedBlock,
    ParsedChapter,
)
from book_agent.domain.structure.ocr_reextraction import (
    NoOpOcrReextractionAdapter,
    OcrReextractionAdapter,
    OcrReextractionRequest,
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


def _mk_page(page_number: int, *, sanity_failed: bool) -> PdfPage:
    sanity = (
        {"ok": False, "reason": "pua_high", "metrics": {"pua_ratio": 0.8}}
        if sanity_failed
        else {"ok": True, "reason": None, "metrics": {}}
    )
    return PdfPage(
        page_number=page_number,
        width=600.0,
        height=800.0,
        blocks=[],
        image_blocks=[],
        text_layer_sanity=sanity,
    )


def _profile(page_count: int = 1) -> PdfFileProfile:
    return PdfFileProfile(
        pdf_kind="text",
        page_count=page_count,
        has_extractable_text=True,
        outline_present=False,
        layout_risk="normal",
        ocr_required=False,
        recovery_lane="default_book",
    )


class FakeReextractionAdapter:
    """Returns a deterministic replacement for every incoming anchor."""

    def __init__(self, replacement_by_anchor: dict[str, str]) -> None:
        self.replacement_by_anchor = replacement_by_anchor
        self.received_requests: list[OcrReextractionRequest] = []

    def reextract_blocks(
        self,
        pdf_path: str,
        requests: list[OcrReextractionRequest],
    ) -> dict[str, str]:
        self.received_requests = list(requests)
        return {
            req.block_anchor: self.replacement_by_anchor[req.block_anchor]
            for req in requests
            if req.block_anchor in self.replacement_by_anchor
        }


class OcrReextractionWiringTests(unittest.TestCase):
    def _build_chapters(self, service: PdfStructureRecoveryService) -> list[ParsedChapter]:
        pages = [_mk_page(1, sanity_failed=True)]
        recovered = [
            _mk_recovered_block(1, page_number=1, text="αβγδε corrupted glyphs here " * 4),
            _mk_recovered_block(2, page_number=1, text="ζηθ more corrupted text " * 4),
        ]
        return service._build_chapters(
            recovered,
            outline_entries=[],
            profile=_profile(),
            file_path="test.pdf",
            pages=pages,
        )

    def test_default_no_adapter_leaves_blocks_untouched(self) -> None:
        service = PdfStructureRecoveryService()  # no adapter
        chapters = self._build_chapters(service)
        # Caller mimics what recover() would do. Since no adapter is set,
        # _apply_ocr_reextraction is never invoked — verify directly that
        # the default field is None.
        self.assertIsNone(service._ocr_reextraction_adapter)
        # And the raw chapters carry sanity-failed provenance but
        # unchanged text.
        all_blocks = [b for ch in chapters for b in ch.blocks]
        self.assertTrue(all_blocks)
        for block in all_blocks:
            self.assertEqual(block.provenance, PROVENANCE_OCR)
            self.assertEqual(block.confidence_breakdown.get("sanity_ok"), False)
            self.assertIn("corrupted", block.text)

    def test_noop_adapter_leaves_blocks_untouched(self) -> None:
        service = PdfStructureRecoveryService(
            ocr_reextraction_adapter=NoOpOcrReextractionAdapter()
        )
        chapters = self._build_chapters(service)
        chapters = service._apply_ocr_reextraction(chapters, pdf_path="test.pdf")
        for block in (b for ch in chapters for b in ch.blocks):
            # Text, provenance, sanity_ok all unchanged.
            self.assertIn("corrupted", block.text)
            self.assertEqual(block.provenance, PROVENANCE_OCR)
            self.assertEqual(block.confidence_breakdown.get("sanity_ok"), False)
            self.assertNotIn("reextracted_via", block.confidence_breakdown)

    def test_fake_adapter_rewrites_sanity_failed_blocks(self) -> None:
        # Two blocks on page 1 both failed sanity — provide replacements
        # for both.
        fake = FakeReextractionAdapter(
            replacement_by_anchor={
                "p1-b1": "Clean OCR output for block one.",
                "p1-b2": "Clean OCR output for block two.",
            }
        )
        service = PdfStructureRecoveryService(ocr_reextraction_adapter=fake)
        chapters = self._build_chapters(service)
        chapters = service._apply_ocr_reextraction(chapters, pdf_path="test.pdf")

        # Adapter should have seen exactly two requests (both blocks were
        # on the sanity-failed page).
        self.assertEqual(len(fake.received_requests), 2)
        self.assertEqual(
            {req.block_anchor for req in fake.received_requests},
            {"p1-b1", "p1-b2"},
        )
        for req in fake.received_requests:
            self.assertEqual(req.page_number, 1)
            self.assertEqual(req.failure_reason, "pua_high")

        # Verify rewritten blocks.
        updated_by_anchor = {
            block.anchor: block
            for ch in chapters
            for block in ch.blocks
            if block.anchor in {"p1-b1", "p1-b2"}
        }
        self.assertEqual(len(updated_by_anchor), 2)
        for anchor, block in updated_by_anchor.items():
            self.assertEqual(block.text, fake.replacement_by_anchor[anchor])
            self.assertEqual(block.provenance, PROVENANCE_OCR)
            self.assertEqual(block.confidence_breakdown.get("sanity_ok"), True)
            self.assertEqual(
                block.confidence_breakdown.get("reextracted_via"),
                "ocr_adapter",
            )

    def test_adapter_partial_replacement_keeps_missing_blocks_unchanged(self) -> None:
        # Provide replacement for only one of two sanity-failed blocks.
        fake = FakeReextractionAdapter(
            replacement_by_anchor={
                "p1-b1": "Clean OCR output for block one.",
                # p1-b2 deliberately missing — simulates adapter declining.
            }
        )
        service = PdfStructureRecoveryService(ocr_reextraction_adapter=fake)
        chapters = self._build_chapters(service)
        chapters = service._apply_ocr_reextraction(chapters, pdf_path="test.pdf")

        block1 = next(
            b for ch in chapters for b in ch.blocks if b.anchor == "p1-b1"
        )
        block2 = next(
            b for ch in chapters for b in ch.blocks if b.anchor == "p1-b2"
        )
        # Replaced.
        self.assertEqual(block1.text, "Clean OCR output for block one.")
        self.assertEqual(block1.confidence_breakdown.get("sanity_ok"), True)
        # Untouched — still corrupted, sanity_ok still False.
        self.assertIn("corrupted", block2.text)
        self.assertEqual(block2.confidence_breakdown.get("sanity_ok"), False)
        self.assertNotIn("reextracted_via", block2.confidence_breakdown)

    def test_sanity_ok_blocks_never_sent_to_adapter(self) -> None:
        # A page where sanity passes — the adapter must receive zero requests.
        fake = FakeReextractionAdapter(replacement_by_anchor={})
        service = PdfStructureRecoveryService(ocr_reextraction_adapter=fake)
        pages = [_mk_page(1, sanity_failed=False)]
        recovered = [
            _mk_recovered_block(1, page_number=1, text="Clean English paragraph " * 4),
        ]
        chapters = service._build_chapters(
            recovered,
            outline_entries=[],
            profile=_profile(),
            file_path="test.pdf",
            pages=pages,
        )
        chapters = service._apply_ocr_reextraction(chapters, pdf_path="test.pdf")
        self.assertEqual(fake.received_requests, [])


if __name__ == "__main__":
    unittest.main()
