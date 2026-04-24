# ruff: noqa: E402
"""Decision-table tests for the PDF Extraction Router (PDF v2 M2.2)."""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.structure.extraction_router import (
    RouterContext,
    RouterDecision,
    route,
    summarize,
)
from book_agent.domain.structure.models import (
    PROVENANCE_OCR,
    PROVENANCE_TEXT_LAYER,
    PROVENANCE_VLM,
    TRANSLATE_ALL,
    TRANSLATE_NONE,
    ParsedBlock,
)


def _mk_block(
    *,
    block_type: str = "paragraph",
    translatability: str = TRANSLATE_ALL,
    provenance: str = PROVENANCE_TEXT_LAYER,
    confidence_breakdown: dict | None = None,
) -> ParsedBlock:
    return ParsedBlock(
        block_type=block_type,
        text="sample",
        source_path="pdf://page/1",
        ordinal=1,
        translatability=translatability,
        provenance=provenance,
        confidence_breakdown=confidence_breakdown or {},
    )


class ExtractionRouterDecisionTests(unittest.TestCase):
    def test_non_translatable_block_is_not_applicable(self) -> None:
        block = _mk_block(
            block_type="code",
            translatability=TRANSLATE_NONE,
        )
        rec = route(block)
        self.assertEqual(rec.decision, RouterDecision.NOT_APPLICABLE)
        self.assertEqual(rec.reason, "block_non_translatable")

    def test_pure_figure_block_is_skipped(self) -> None:
        block = _mk_block(block_type="figure")
        rec = route(block)
        self.assertEqual(rec.decision, RouterDecision.SKIP)
        self.assertIn("non_textual_block", rec.reason)

    def test_image_block_is_skipped(self) -> None:
        block = _mk_block(block_type="image")
        rec = route(block)
        self.assertEqual(rec.decision, RouterDecision.SKIP)

    def test_clean_text_layer_is_kept(self) -> None:
        block = _mk_block(
            confidence_breakdown={"sanity_ok": True},
        )
        rec = route(block)
        self.assertEqual(rec.decision, RouterDecision.KEEP)
        self.assertEqual(rec.reason, "text_layer_trusted")

    def test_missing_sanity_defaults_to_keep(self) -> None:
        # Backward compatibility: blocks from parsers that haven't been
        # wired to sanity propagation must not be over-escalated.
        block = _mk_block(confidence_breakdown={})
        rec = route(block)
        self.assertEqual(rec.decision, RouterDecision.KEEP)

    def test_sanity_failed_escalates_to_ocr_by_default(self) -> None:
        block = _mk_block(
            provenance=PROVENANCE_OCR,
            confidence_breakdown={"sanity_ok": False, "sanity_reason": "pua_high"},
        )
        rec = route(block)
        self.assertEqual(rec.decision, RouterDecision.ESCALATE_OCR)
        self.assertEqual(rec.reason, "sanity_failed:pua_high")

    def test_sanity_failed_escalates_to_vlm_when_enabled(self) -> None:
        block = _mk_block(
            provenance=PROVENANCE_OCR,
            confidence_breakdown={"sanity_ok": False, "sanity_reason": "entropy_low"},
        )
        rec = route(block, RouterContext(vlm_enabled=True))
        self.assertEqual(rec.decision, RouterDecision.ESCALATE_VLM)
        self.assertEqual(rec.reason, "sanity_failed:entropy_low")

    def test_ocr_provenance_with_sanity_ok_is_kept(self) -> None:
        # Block was re-extracted via OCR and sanity now passes — trust it.
        block = _mk_block(
            provenance=PROVENANCE_OCR,
            confidence_breakdown={"sanity_ok": True},
        )
        rec = route(block)
        self.assertEqual(rec.decision, RouterDecision.KEEP)
        self.assertEqual(rec.reason, "provenance_ocr_sanity_ok")

    def test_vlm_provenance_always_kept(self) -> None:
        # A VLM-extracted block is already the most-expensive path; never
        # re-escalate even if downstream somehow saw a sanity signal flip.
        block = _mk_block(
            provenance=PROVENANCE_VLM,
            confidence_breakdown={"sanity_ok": False},
        )
        rec = route(block)
        self.assertEqual(rec.decision, RouterDecision.KEEP)
        self.assertEqual(rec.reason, "provenance_vlm")

    def test_non_translatable_wins_over_sanity_failure(self) -> None:
        # Even if sanity claims the page is corrupt, a code/equation block
        # still gets NOT_APPLICABLE because the translator won't touch it
        # regardless of extraction path.
        block = _mk_block(
            block_type="code",
            translatability=TRANSLATE_NONE,
            confidence_breakdown={"sanity_ok": False, "sanity_reason": "pua_high"},
        )
        rec = route(block)
        self.assertEqual(rec.decision, RouterDecision.NOT_APPLICABLE)


class RouterSummaryTests(unittest.TestCase):
    def test_summary_counts_match_input(self) -> None:
        clean = _mk_block(confidence_breakdown={"sanity_ok": True})
        escalate = _mk_block(
            provenance=PROVENANCE_OCR,
            confidence_breakdown={"sanity_ok": False, "sanity_reason": "pua_high"},
        )
        skip = _mk_block(block_type="figure")
        not_applicable = _mk_block(
            block_type="code",
            translatability=TRANSLATE_NONE,
        )
        decisions = [
            route(clean),
            route(clean),
            route(escalate),
            route(skip),
            route(not_applicable),
        ]
        report = summarize(decisions)
        self.assertEqual(report["total"], 5)
        self.assertEqual(report["by_decision"]["keep"], 2)
        self.assertEqual(report["by_decision"]["escalate_ocr"], 1)
        self.assertEqual(report["by_decision"]["skip"], 1)
        self.assertEqual(report["by_decision"]["not_applicable"], 1)
        self.assertAlmostEqual(report["ocr_ratio"], 0.2, places=3)
        self.assertAlmostEqual(report["vlm_ratio"], 0.0, places=3)

    def test_summary_vlm_ratio_cap_enforcement(self) -> None:
        # Generate 20 blocks; 4 would escalate to VLM. Ratio 0.20 > 0.15 cap.
        ctx = RouterContext(vlm_enabled=True, vlm_block_ratio_cap=0.15)
        decisions = []
        for i in range(20):
            sanity = {"sanity_ok": False, "sanity_reason": "pua_high"} if i < 4 else {"sanity_ok": True}
            decisions.append(route(_mk_block(confidence_breakdown=sanity), ctx))
        report = summarize(decisions)
        self.assertAlmostEqual(report["vlm_ratio"], 0.20, places=3)
        self.assertGreater(report["vlm_ratio"], ctx.vlm_block_ratio_cap)

    def test_summary_empty_input(self) -> None:
        report = summarize([])
        self.assertEqual(report["total"], 0)
        self.assertEqual(report["vlm_ratio"], 0.0)
        self.assertEqual(report["ocr_ratio"], 0.0)


if __name__ == "__main__":
    unittest.main()
