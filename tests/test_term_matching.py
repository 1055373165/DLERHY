"""Variant-tolerant source/target term matching."""

from __future__ import annotations

import unittest

from book_agent.domain.terminology.matching import (
    SourceTermIndex,
    normalize_target,
    singularize,
    source_term_key,
    target_has_rendering,
)


class SourceTermKeyTest(unittest.TestCase):
    def test_spelling_variants_share_a_key(self) -> None:
        for variants in (
            ("Timeframe", "time-frame", "time frames", "TIME FRAME"),
            ("Head & Shoulders", "head and shoulder", "Head-and-Shoulders"),
            ("anti-trend bias", "Anti Trend biases"),
            ("RSI divergence", "RSI Divergences"),
        ):
            with self.subTest(variants=variants):
                self.assertEqual(len({source_term_key(variant) for variant in variants}), 1)

    def test_distinct_words_keep_distinct_keys(self) -> None:
        self.assertNotEqual(source_term_key("bull"), source_term_key("bullish"))
        self.assertNotEqual(source_term_key("head & shoulders"), source_term_key("inverted head & shoulders"))

    def test_singularize(self) -> None:
        self.assertEqual(
            [singularize(word) for word in ("bias", "biases", "news", "ideas", "strategies", "boxes", "indices", "swings")],
            ["bias", "bias", "news", "idea", "strategy", "box", "index", "swing"],
        )


class SourceTermIndexTest(unittest.TestCase):
    def test_longest_match_owns_the_tokens(self) -> None:
        index = SourceTermIndex(["head & shoulders", "inverted head & shoulders", "RSI divergence", "divergence"])
        self.assertEqual(
            [occurrence.key for occurrence in index.find("An inverted head and shoulders, then a Head & Shoulders.")],
            [source_term_key("inverted head & shoulders"), source_term_key("head & shoulders")],
        )
        self.assertEqual(
            index.count(["Two RSI divergences and one divergence."]),
            {source_term_key("RSI divergence"): 1, source_term_key("divergence"): 1},
        )

    def test_whole_tokens_and_split_compounds(self) -> None:
        index = SourceTermIndex(["bull", "timeframe"])
        self.assertEqual(index.count(["Bullish traders are not bulls, but a bull is."]), {"bull": 2})
        self.assertEqual(index.count(["Weekly time frame versus daily time-frames."]), {"timeframe": 2})


class TargetMatchingTest(unittest.TestCase):
    def test_whitespace_punctuation_and_case_are_ignored(self) -> None:
        self.assertEqual(normalize_target("RSI 背离（看涨）"), "rsi背离看涨")
        self.assertTrue(target_has_rendering("形成了rsi背离。", ["RSI 背离"]))
        self.assertTrue(target_has_rendering("出现头肩形。", ["头肩形态", "头肩形"]))
        self.assertFalse(target_has_rendering("出现了失败摇摆。", ["失败摆动"]))


if __name__ == "__main__":
    unittest.main()
