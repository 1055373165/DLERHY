# ruff: noqa: E402
"""Tests for the CJK leak checks (locked-term matching is tested in test_term_enforcement)."""

import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.services.glossary_enforcement import (
    detect_non_translatable_leaks,
    has_cjk_characters,
)


class CjkDetectionTests(unittest.TestCase):
    def test_detects_common_cjk(self) -> None:
        self.assertTrue(has_cjk_characters("这是中文"))

    def test_does_not_flag_pure_ascii(self) -> None:
        self.assertFalse(has_cjk_characters("hello world"))

    def test_does_not_flag_empty(self) -> None:
        self.assertFalse(has_cjk_characters(""))


class NonTranslatableLeakDetectorTests(unittest.TestCase):
    @dataclass
    class FakeBlock:
        translatability: str
        target_text: str
        anchor: str

    def test_translate_none_with_clean_target_passes(self) -> None:
        blocks = [
            self.FakeBlock(
                translatability="translate_none",
                target_text="def foo(): pass",
                anchor="code-1",
            ),
        ]
        self.assertEqual(detect_non_translatable_leaks(blocks), [])

    def test_translate_none_with_cjk_flags_block(self) -> None:
        blocks = [
            self.FakeBlock(
                translatability="translate_none",
                target_text="def 函数(): pass",  # CJK leaked
                anchor="code-1",
            ),
            self.FakeBlock(
                translatability="translate_none",
                target_text="def bar(): pass",  # clean
                anchor="code-2",
            ),
        ]
        leaks = detect_non_translatable_leaks(blocks)
        self.assertEqual(leaks, ["code-1"])

    def test_translatable_block_with_cjk_is_ignored(self) -> None:
        # Normal prose block with CJK is the expected case; not a leak.
        blocks = [
            self.FakeBlock(
                translatability="translate_all",
                target_text="这是一段正常的中文译文。",
                anchor="para-1",
            ),
        ]
        self.assertEqual(detect_non_translatable_leaks(blocks), [])


if __name__ == "__main__":
    unittest.main()
