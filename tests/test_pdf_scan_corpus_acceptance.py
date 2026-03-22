from __future__ import annotations

import unittest
from pathlib import Path

from scripts.pdf_scan_corpus_acceptance import (
    LARGER_CORPUS_ACCEPTANCE_THRESHOLDS,
    evaluate_larger_corpus_acceptance,
)


class PdfScanCorpusAcceptanceTests(unittest.TestCase):
    def test_locked_larger_corpus_acceptance_passes_phase3_thresholds(self) -> None:
        snapshot = evaluate_larger_corpus_acceptance(
            repo_root=Path(__file__).resolve().parents[1]
        )

        self.assertTrue(snapshot["overall_passed"])
        self.assertTrue(snapshot["checks"]["full_book_structure_floor"]["passed"])
        self.assertTrue(snapshot["checks"]["legacy_bootstrap_failure_classified"]["passed"])
        self.assertTrue(snapshot["checks"]["retry_resume_lineage_present"]["passed"])
        self.assertTrue(snapshot["checks"]["slice_repair_acceptance"]["passed"])
        self.assertTrue(snapshot["checks"]["final_repair_closure"]["passed"])
        self.assertTrue(snapshot["checks"]["readable_rescue_exports"]["passed"])
        self.assertTrue(snapshot["checks"]["lineage_structure_stability"]["passed"])

    def test_locked_larger_corpus_acceptance_snapshot_records_frozen_baseline_values(self) -> None:
        snapshot = evaluate_larger_corpus_acceptance(
            repo_root=Path(__file__).resolve().parents[1]
        )

        self.assertEqual(
            snapshot["tiers"]["tier_a_full_book"]["page_count"],
            458,
        )
        self.assertEqual(
            snapshot["tiers"]["tier_a_full_book"]["chapter_count"],
            97,
        )
        self.assertEqual(
            snapshot["tiers"]["tier_a_full_book"]["packet_count"],
            2170,
        )
        self.assertEqual(
            snapshot["tiers"]["tier_a_full_book"]["run_count"],
            2171,
        )
        self.assertEqual(
            snapshot["tiers"]["tier_c_slice_repair"]["failure_families"],
            ["repair_timeout"],
        )
        self.assertAlmostEqual(
            snapshot["tiers"]["tier_c_slice_repair"]["success_ratio"],
            33 / 36,
            places=6,
        )
        self.assertEqual(
            snapshot["tiers"]["tier_c_final_repair"]["success_ratio"],
            LARGER_CORPUS_ACCEPTANCE_THRESHOLDS["final_repair_required_success_ratio"],
        )


if __name__ == "__main__":
    unittest.main()
