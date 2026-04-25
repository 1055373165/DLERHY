# ruff: noqa: E402
"""Tests for M3.3 equation modality."""

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
from book_agent.services.equation_extractor import (
    EQUATION_RENDER_IMAGE_ANCHOR,
    EQUATION_RENDER_LATEX,
    EQUATION_RENDER_VERBATIM_TEXT,
    EquationLatexResult,
    NoOpEquationLatexAdapter,
    enhance_block_for_equation,
    looks_like_equation,
)


class FakeLatexAdapter:
    """Returns a scripted LaTeX result for any input."""

    def __init__(self, scripted: EquationLatexResult | None) -> None:
        self.scripted = scripted
        self.calls: list[str] = []

    def extract(self, text):
        self.calls.append(text)
        return self.scripted


class LooksLikeEquationTests(unittest.TestCase):
    def test_dense_operators_qualify(self) -> None:
        self.assertTrue(looks_like_equation("x = a + b ≤ 5 ∑ y"))

    def test_prose_with_single_equal_does_not_qualify(self) -> None:
        self.assertFalse(
            looks_like_equation(
                "The variable x = 5 in the example, but otherwise the prose flows naturally."
            )
        )

    def test_empty_text_rejected(self) -> None:
        self.assertFalse(looks_like_equation(""))


class NoOpAdapterTests(unittest.TestCase):
    def test_noop_returns_none(self) -> None:
        adapter = NoOpEquationLatexAdapter()
        self.assertIsNone(adapter.extract("E = mc^2"))


class EnhanceBlockForEquationTests(unittest.TestCase):
    def _equation_block(self, *, metadata: dict | None = None) -> ParsedBlock:
        return ParsedBlock(
            block_type="equation",
            text="E = mc^2",
            source_path="x",
            ordinal=1,
            anchor="eq1",
            translatability=TRANSLATE_ALL,  # incorrectly initially
            metadata=metadata or {},
        )

    def test_equation_always_becomes_translate_none(self) -> None:
        out = enhance_block_for_equation(self._equation_block())
        self.assertEqual(out.translatability, TRANSLATE_NONE)

    def test_no_adapter_recovery_no_image_falls_back_to_verbatim(self) -> None:
        out = enhance_block_for_equation(self._equation_block())
        self.assertEqual(
            out.metadata["equation_render_mode"],
            EQUATION_RENDER_VERBATIM_TEXT,
        )
        self.assertNotIn("equation_latex", out.metadata)

    def test_image_attached_falls_back_to_image_anchor(self) -> None:
        out = enhance_block_for_equation(
            self._equation_block(metadata={"image_path": "assets/eq1.png"})
        )
        self.assertEqual(
            out.metadata["equation_render_mode"],
            EQUATION_RENDER_IMAGE_ANCHOR,
        )

    def test_successful_latex_recovery_uses_latex_render_mode(self) -> None:
        adapter = FakeLatexAdapter(
            EquationLatexResult(latex="E = mc^2", is_display=True, confidence=0.95)
        )
        out = enhance_block_for_equation(
            self._equation_block(),
            adapter=adapter,
        )
        self.assertEqual(out.metadata["equation_render_mode"], EQUATION_RENDER_LATEX)
        self.assertEqual(out.metadata["equation_latex"], "E = mc^2")
        self.assertTrue(out.metadata["equation_is_display"])
        self.assertAlmostEqual(out.metadata["equation_confidence"], 0.95)
        self.assertEqual(out.translatability, TRANSLATE_NONE)
        self.assertEqual(adapter.calls, ["E = mc^2"])

    def test_adapter_returning_empty_string_does_not_use_latex_mode(self) -> None:
        adapter = FakeLatexAdapter(EquationLatexResult(latex="   ", confidence=0.1))
        out = enhance_block_for_equation(
            self._equation_block(),
            adapter=adapter,
        )
        self.assertNotEqual(out.metadata["equation_render_mode"], EQUATION_RENDER_LATEX)

    def test_non_equation_block_passes_through_unchanged(self) -> None:
        para = ParsedBlock(
            block_type="paragraph",
            text="Plain prose.",
            source_path="x",
            ordinal=1,
            anchor="p1",
        )
        out = enhance_block_for_equation(para)
        self.assertIs(out, para)
        self.assertEqual(out.translatability, TRANSLATE_ALL)

    def test_adapter_exception_falls_back_safely(self) -> None:
        class ExplodingAdapter:
            def extract(self, _text):
                raise RuntimeError("boom")

        out = enhance_block_for_equation(
            self._equation_block(),
            adapter=ExplodingAdapter(),
        )
        # Translatability still flipped; render mode falls back.
        self.assertEqual(out.translatability, TRANSLATE_NONE)
        self.assertEqual(
            out.metadata["equation_render_mode"],
            EQUATION_RENDER_VERBATIM_TEXT,
        )


if __name__ == "__main__":
    unittest.main()
