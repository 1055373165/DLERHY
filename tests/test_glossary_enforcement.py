# ruff: noqa: E402
"""Tests for M2.7 glossary adherence post-validator."""

import os
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
    detect_violations,
    has_cjk_characters,
)


class DetectViolationsTests(unittest.TestCase):
    GLOSSARY = {"Agent": "智能体", "Transformer": "变换器", "RAG": "检索增强生成"}

    def test_no_glossary_returns_empty(self) -> None:
        self.assertEqual(
            detect_violations("source", "target", {}),
            [],
        )

    def test_empty_source_returns_empty(self) -> None:
        self.assertEqual(
            detect_violations("", "任意译文", self.GLOSSARY),
            [],
        )

    def test_source_term_absent_does_not_fire(self) -> None:
        # "Agent" is in glossary but never appears in source — no violation.
        violations = detect_violations(
            "The transformer architecture uses attention.",
            "变换器架构使用了注意力。",
            {"Agent": "智能体"},
        )
        self.assertEqual(violations, [])

    def test_locked_term_honoured_is_clean(self) -> None:
        violations = detect_violations(
            "The Agent coordinates tools.",
            "智能体协调工具。",
            self.GLOSSARY,
        )
        self.assertEqual(violations, [])

    def test_hard_violation_no_target(self) -> None:
        violations = detect_violations(
            "The Agent coordinates tools.",
            "这个代理负责协调工具。",
            self.GLOSSARY,
        )
        self.assertEqual(len(violations), 1)
        v = violations[0]
        self.assertEqual(v.source_term, "Agent")
        self.assertEqual(v.expected_target, "智能体")
        self.assertEqual(v.source_match_count, 1)
        self.assertEqual(v.target_match_count, 0)
        self.assertEqual(v.severity_hint, "hard")

    def test_partial_violation_some_targets_missing(self) -> None:
        # "Agent" appears twice in source but only once as 智能体 in target.
        violations = detect_violations(
            "Agent one works with Agent two.",
            "第一个智能体和第二个代理协作。",
            {"Agent": "智能体"},
        )
        self.assertEqual(len(violations), 1)
        v = violations[0]
        self.assertEqual(v.source_match_count, 2)
        self.assertEqual(v.target_match_count, 1)
        self.assertEqual(v.severity_hint, "partial")

    def test_word_boundary_prevents_false_positive(self) -> None:
        # "Agent" must NOT match inside "agentic".
        violations = detect_violations(
            "An agentic pattern emerges.",
            "涌现出一种智能体式的模式。",  # contains the locked target anyway
            {"Agent": "智能体"},
        )
        self.assertEqual(violations, [])

    def test_case_insensitive_source_match(self) -> None:
        violations = detect_violations(
            "the agent system",
            "系统",  # target lacks 智能体
            {"Agent": "智能体"},
        )
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].source_match_count, 1)

    def test_multiple_terms_independently_checked(self) -> None:
        violations = detect_violations(
            "Agent uses the Transformer with RAG.",
            "智能体使用了变换器。",  # RAG missing
            self.GLOSSARY,
        )
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].source_term, "RAG")
        self.assertEqual(violations[0].severity_hint, "hard")

    def test_empty_entries_in_glossary_are_ignored(self) -> None:
        violations = detect_violations(
            "Agent does stuff.",
            "代理做事。",
            {"": "xx", "Agent": "", "Real": "真"},
        )
        self.assertEqual(violations, [])


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
