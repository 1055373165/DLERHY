"""Prose that merely resembles code statements must not be detected as code.

A trading book had ordinary sentences promoted to code blocks (and so left
untranslated): a line starting "use RSI smoothed indicator, ..." matched the
Rust/PHP ``use`` import pattern, and "Note: However, common sense ..." matched
the ``key: value`` data-line rule. At export time "If you want to ... simple):"
matched the Python ``if ...:`` statement pattern case-insensitively.
"""

from __future__ import annotations

import unittest

from book_agent.export.code_text import looks_like_single_line_codeish_text
from book_agent.ingestion.text import (
    _CODE_IMPORT_LINE_PATTERN,
    _looks_like_code,
    _looks_like_embedded_code_line,
    _looks_like_labeled_prose_line,
)


class ImportLinePatternTest(unittest.TestCase):
    def test_code_statements_still_match(self) -> None:
        for line in (
            "use std::collections::HashMap;",
            "use App\\Models\\User;",
            "using System.Text;",
            "include Comparable",
            'require "json"',
            "require_once 'config.php';",
            "package main",
            "package com.example.app;",
            "module Foo",
            "import numpy as np",
            "from pathlib import Path",
        ):
            with self.subTest(line=line):
                self.assertIsNotNone(_CODE_IMPORT_LINE_PATTERN.match(line))

    def test_english_sentences_starting_with_statement_verbs_do_not_match(self) -> None:
        for line in (
            "use RSI smoothed indicator, you come close.",
            "using RSI with price action works better",
            "include the ones below in your watchlist",
            "require a lot of patience from the trader",
            "module 3 covers the basics of trading",
        ):
            with self.subTest(line=line):
                self.assertIsNone(_CODE_IMPORT_LINE_PATTERN.match(line))

    def test_wrapped_prose_paragraph_is_not_code(self) -> None:
        text = (
            "So if you trade on the signal of that website, you are going to trade wrong. However, if you tweak the settings and\n"
            "use RSI smoothed indicator, you come close."
        )
        self.assertFalse(_looks_like_code(text, 2))


class LabeledProseLineTest(unittest.TestCase):
    def test_callout_labels_followed_by_sentences_are_prose(self) -> None:
        for line in (
            "Note: However, common sense tells us that failure swings often happen outside oversold/overbought",
            "Important Tip: Always check the weekly chart before you enter a trade on the daily one",
        ):
            with self.subTest(line=line):
                self.assertTrue(_looks_like_labeled_prose_line(line))
                self.assertFalse(_looks_like_embedded_code_line(line))

    def test_data_keys_stay_code_like(self) -> None:
        for line in ("name: my-service", "description: A small service", "timeout: 30"):
            with self.subTest(line=line):
                self.assertFalse(_looks_like_labeled_prose_line(line))
                self.assertTrue(_looks_like_embedded_code_line(line))


class ExportSingleLineCodeishTest(unittest.TestCase):
    def test_capitalized_prose_is_not_a_python_statement(self) -> None:
        for line in (
            "If you want to understand the inside secrets of RSI, let us go and check how it is calculated "
            "(Calculation sheet is straight from the Wilder's book; I have used prices in round figures):",
            "From here on we look at the weekly chart.",
            "Return on capital matters more than the win rate.",
        ):
            with self.subTest(line=line):
                self.assertFalse(looks_like_single_line_codeish_text(line))

    def test_statements_still_look_like_code(self) -> None:
        for line in ("if x > 3:", "for item in items:", "from pathlib import Path", "print(result)"):
            with self.subTest(line=line):
                self.assertTrue(looks_like_single_line_codeish_text(line))


if __name__ == "__main__":
    unittest.main()
