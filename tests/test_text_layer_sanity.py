# ruff: noqa: E402
"""Tests for the PDF text-layer sanity gate (PDF v2 M1.2)."""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.structure.text_layer_sanity import SanityReport, assess_text


CLEAN_ENGLISH_PAGE = (
    "Chapter 1. Introduction\n\n"
    "This book explores the design patterns used in modern software systems. "
    "The first chapter introduces the fundamental concepts and provides an "
    "overview of the topics covered in subsequent chapters. We begin by "
    "examining how developers approach common problems, and how abstract "
    "thinking leads to better code. Throughout this text we will revisit the "
    "same ideas from different angles, each time deepening the reader's "
    "understanding of why these patterns matter. The examples are drawn from "
    "real production systems that have been running for many years under "
    "heavy load. The goal of this chapter is to set the stage for later "
    "discussions about system design, trade-offs, and evolution."
)


def _corrupted_pua(clean_text: str) -> str:
    """Map every ASCII letter to a PUA codepoint to simulate a broken ToUnicode map."""
    mapped: list[str] = []
    for ch in clean_text:
        if ch.isalpha():
            mapped.append(chr(0xE000 + (ord(ch) & 0xFF)))
        else:
            mapped.append(ch)
    return "".join(mapped)


class TextLayerSanityTests(unittest.TestCase):
    def test_clean_english_page_passes(self) -> None:
        report = assess_text(CLEAN_ENGLISH_PAGE)
        self.assertIsInstance(report, SanityReport)
        self.assertTrue(report.ok, f"clean page rejected: {report.reason} {report.metrics}")
        self.assertIsNone(report.reason)
        self.assertGreater(report.metrics["unicode_entropy"], 4.0)
        self.assertGreater(report.metrics["dict_hit_rate"], 0.2)
        self.assertLess(report.metrics["pua_ratio"], 0.001)

    def test_empty_input_defaults_to_ok(self) -> None:
        report = assess_text("")
        self.assertTrue(report.ok)
        self.assertEqual(report.reason, "empty")

    def test_short_input_defaults_to_ok(self) -> None:
        report = assess_text("Fig. 3.1")
        self.assertTrue(report.ok)
        self.assertEqual(report.reason, "insufficient_text")

    def test_pua_mapped_text_fails(self) -> None:
        corrupted = _corrupted_pua(CLEAN_ENGLISH_PAGE)
        report = assess_text(corrupted)
        self.assertFalse(report.ok, f"PUA corruption not caught: metrics={report.metrics}")
        self.assertEqual(report.reason, "pua_high")
        self.assertGreater(report.metrics["pua_ratio"], 0.02)

    def test_reference_list_is_tolerated_despite_low_dict_hit(self) -> None:
        # Bibliography pages are dominated by proper nouns and conference
        # names that miss the common-word dictionary; PUA=0 and entropy is
        # English-like, so M1 gate should NOT fire. See module docstring.
        ref_list = (
            "1. Devlin, J., Chang, M., Lee, K., & Toutanova, K. (2019). "
            "BERT: Pre-training of Deep Bidirectional Transformers for "
            "Language Understanding. NAACL.\n"
            "2. Brown, T., Mann, B., Ryder, N., et al. (2020). Language "
            "Models are Few-Shot Learners. NeurIPS.\n"
            "3. Vaswani, A., Shazeer, N., Parmar, N., et al. (2017). "
            "Attention Is All You Need. NeurIPS.\n"
            "4. LeCun, Y., Bengio, Y., & Hinton, G. (2015). Deep learning. "
            "Nature, 521(7553), 436-444.\n"
            "5. Radford, A., Narasimhan, K., Salimans, T., & Sutskever, I. "
            "(2018). Improving Language Understanding by Generative "
            "Pre-Training. OpenAI Technical Report."
        )
        report = assess_text(ref_list)
        self.assertTrue(
            report.ok,
            f"reference list mis-flagged as corrupted: {report.reason} {report.metrics}",
        )
        # but dict_hit should still be relatively low (emitted as observability).
        self.assertLess(report.metrics["dict_hit_rate"], 0.30)

    def test_highly_repetitive_text_fails_entropy(self) -> None:
        # A page of a single letter repeated is obviously broken.
        repeated = "a" * 500
        report = assess_text(repeated)
        self.assertFalse(report.ok)
        self.assertEqual(report.reason, "entropy_low")

    def test_metrics_included_even_on_pass(self) -> None:
        report = assess_text(CLEAN_ENGLISH_PAGE)
        self.assertIn("unicode_entropy", report.metrics)
        self.assertIn("pua_ratio", report.metrics)
        self.assertIn("dict_hit_rate", report.metrics)
        self.assertIn("alpha_chars", report.metrics)
        self.assertIn("total_chars", report.metrics)
        self.assertIn("non_ws_chars", report.metrics)


if __name__ == "__main__":
    unittest.main()
