# ruff: noqa: E402
"""Contract tests for the DocIR translatability protocol (PDF v2 M1.4 / M1.5).

These tests encode the invariant that non-translatable content (code,
equations, tables, figures, images, PDF header/footer/toc_entry roles, and
backmatter pages) NEVER ends up with translatability=translate_all. A
regression in parser-side labelling would cause silent mis-translation of
protected spans — exactly the failure mode called out in spec §3.1 failure 3.

The suite exercises `derive_translatability` directly (the single source
of truth bridging DocIR with the existing `block_rules` decision layer).
"""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.structure.models import (
    ParsedBlock,
    TRANSLATE_ALL,
    TRANSLATE_NONE,
    derive_translatability,
)


class TranslatabilityDeriveTests(unittest.TestCase):
    def test_code_block_is_non_translatable(self) -> None:
        self.assertEqual(derive_translatability("code"), TRANSLATE_NONE)
        self.assertEqual(derive_translatability("code", {}), TRANSLATE_NONE)

    def test_equation_block_is_non_translatable(self) -> None:
        self.assertEqual(derive_translatability("equation"), TRANSLATE_NONE)

    def test_table_block_is_non_translatable(self) -> None:
        self.assertEqual(derive_translatability("table"), TRANSLATE_NONE)

    def test_figure_block_is_non_translatable(self) -> None:
        self.assertEqual(derive_translatability("figure"), TRANSLATE_NONE)

    def test_image_block_is_non_translatable(self) -> None:
        self.assertEqual(derive_translatability("image"), TRANSLATE_NONE)

    def test_paragraph_is_translatable(self) -> None:
        self.assertEqual(derive_translatability("paragraph"), TRANSLATE_ALL)

    def test_heading_is_translatable(self) -> None:
        self.assertEqual(derive_translatability("heading"), TRANSLATE_ALL)

    def test_pdf_header_role_is_non_translatable(self) -> None:
        metadata = {"pdf_block_role": "header"}
        self.assertEqual(
            derive_translatability("paragraph", metadata),
            TRANSLATE_NONE,
        )

    def test_pdf_footer_role_is_non_translatable(self) -> None:
        metadata = {"pdf_block_role": "footer"}
        self.assertEqual(
            derive_translatability("paragraph", metadata),
            TRANSLATE_NONE,
        )

    def test_pdf_toc_entry_role_is_non_translatable(self) -> None:
        metadata = {"pdf_block_role": "toc_entry"}
        self.assertEqual(
            derive_translatability("paragraph", metadata),
            TRANSLATE_NONE,
        )

    def test_pdf_backmatter_page_family_is_non_translatable(self) -> None:
        metadata = {"pdf_page_family": "backmatter"}
        self.assertEqual(
            derive_translatability("paragraph", metadata),
            TRANSLATE_NONE,
        )

    def test_explicit_translatable_false_wins(self) -> None:
        # A parser may have already determined this block is in a non-
        # translatable region (e.g., header/footer) and stamped translatable
        # =False. Honour that verdict.
        metadata_false = {"translatable": False}
        self.assertEqual(
            derive_translatability("paragraph", metadata_false),
            TRANSLATE_NONE,
        )

    def test_explicit_translatable_true_does_not_override_block_type(self) -> None:
        # Critical invariant (spec §3.1 failure 3): even when the parser
        # says translatable=True (prose-region), a CODE/EQUATION/etc block
        # MUST stay non-translatable. Mirrors block_rules.translatability_for_block.
        metadata_true = {"translatable": True}
        self.assertEqual(
            derive_translatability("code", metadata_true),
            TRANSLATE_NONE,
        )
        self.assertEqual(
            derive_translatability("equation", metadata_true),
            TRANSLATE_NONE,
        )
        self.assertEqual(
            derive_translatability("table", metadata_true),
            TRANSLATE_NONE,
        )
        # ... and a regular paragraph in a translatable region stays translatable.
        self.assertEqual(
            derive_translatability("paragraph", metadata_true),
            TRANSLATE_ALL,
        )


class TranslatabilityLeakGuardTests(unittest.TestCase):
    """Guards: non-translatable blocks must never claim translate_all.

    If a downstream regression introduces a block where the metadata says
    one thing and the DocIR field says another, this guard fires at parse
    time — long before the translator would have a chance to mis-translate.
    """

    def _is_consistent(self, block: ParsedBlock) -> bool:
        expected = derive_translatability(block.block_type, block.metadata)
        return block.translatability == expected

    def test_constructed_code_block_is_consistent(self) -> None:
        block = ParsedBlock(
            block_type="code",
            text="for i in range(10):\n    print(i)",
            source_path="x",
            ordinal=1,
            translatability=derive_translatability("code"),
        )
        self.assertEqual(block.translatability, TRANSLATE_NONE)
        self.assertTrue(self._is_consistent(block))

    def test_constructed_equation_block_is_consistent(self) -> None:
        block = ParsedBlock(
            block_type="equation",
            text="E = mc^2",
            source_path="x",
            ordinal=1,
            translatability=derive_translatability("equation"),
        )
        self.assertEqual(block.translatability, TRANSLATE_NONE)
        self.assertTrue(self._is_consistent(block))

    def test_pdf_header_block_is_consistent(self) -> None:
        metadata = {"pdf_block_role": "header"}
        block = ParsedBlock(
            block_type="paragraph",
            text="Running head: INTRODUCTION",
            source_path="x",
            ordinal=1,
            metadata=metadata,
            translatability=derive_translatability("paragraph", metadata),
        )
        self.assertEqual(block.translatability, TRANSLATE_NONE)

    def test_pdf_backmatter_block_is_consistent(self) -> None:
        metadata = {"pdf_page_family": "backmatter"}
        block = ParsedBlock(
            block_type="paragraph",
            text="Smith, J. (2019). Example.",
            source_path="x",
            ordinal=1,
            metadata=metadata,
            translatability=derive_translatability("paragraph", metadata),
        )
        self.assertEqual(block.translatability, TRANSLATE_NONE)

    def test_default_paragraph_is_translate_all(self) -> None:
        block = ParsedBlock(
            block_type="paragraph",
            text="This is prose.",
            source_path="x",
            ordinal=1,
        )
        # Defaults should keep a normal paragraph translatable without the
        # parser having to set anything explicitly.
        self.assertEqual(block.translatability, TRANSLATE_ALL)
        self.assertTrue(self._is_consistent(block))


if __name__ == "__main__":
    unittest.main()
