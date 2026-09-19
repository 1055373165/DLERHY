"""Shared locked-term matcher: one decision for the translation guardrail, violation events and review."""

import unittest

from book_agent.domain.terminology.enforcement import LockedTerm, find_term_violations
from book_agent.translation.contracts import AlignmentSuggestion, TranslationTargetSegment, TranslationWorkerOutput
from book_agent.translation.output_validation import LOCKED_TERM_VIOLATED, OutputValidator

RSI = LockedTerm("RSI divergence", ("RSI 背离", "RSI背离现象"))
BULL = LockedTerm("bull", ("多头",))
HEAD = LockedTerm("Head & Shoulders", ("头肩形态",))
INVERTED = LockedTerm("Inverted Head and Shoulders", ("反转头肩形态",))


class FindTermViolationsTests(unittest.TestCase):
    def test_accepted_rendering_ignoring_spacing_and_case_is_honoured(self) -> None:
        units = [("s1", "Watch for RSI divergences.", "注意 rsi背离。"), ("s2", "RSI divergence again.", "再次出现RSI背离现象")]
        self.assertEqual(find_term_violations(units, [RSI]), [])

    def test_missing_rendering_is_a_violation_with_the_expected_term_first(self) -> None:
        violations = find_term_violations([("s1", "An RSI divergence appeared.", "出现了相对强弱背驰。")], [RSI])
        self.assertEqual(len(violations), 1)
        violation = violations[0]
        self.assertEqual((violation.unit_id, violation.term_index), ("s1", 0))
        self.assertEqual(violation.expected_target_term, "RSI 背离")
        self.assertEqual(violation.accepted_renderings, ("RSI 背离", "RSI背离现象"))
        self.assertEqual(violation.source_occurrences, 1)

    def test_whole_tokens_only_and_longest_term_owns_the_position(self) -> None:
        self.assertEqual(find_term_violations([("s1", "A bullish close.", "收盘偏强。")], [BULL]), [])
        units = [("s1", "An Inverted Head & Shoulders formed.", "形成了反转头肩形态。")]
        self.assertEqual(find_term_violations(units, [HEAD, INVERTED]), [])

    def test_empty_targets_are_skipped_only_on_request(self) -> None:
        units = [("s1", "The bull ran.", "")]
        self.assertEqual(len(find_term_violations(units, [BULL])), 1)
        self.assertEqual(find_term_violations(units, [BULL], skip_empty_targets=True), [])

    def test_terms_without_a_rendering_are_ignored(self) -> None:
        self.assertEqual(find_term_violations([("s1", "The bull ran.", "牛跑了")], [LockedTerm("bull", ("",))]), [])


class GuardrailLockedTermTests(unittest.TestCase):
    def _output(self, text: str) -> TranslationWorkerOutput:
        return TranslationWorkerOutput(
            packet_id="p1",
            target_segments=[TranslationTargetSegment(temp_id="t1", text_zh=text, segment_type="sentence", source_sentence_ids=["s1"])],
            alignment_suggestions=[AlignmentSuggestion(source_sentence_ids=["s1"], target_temp_ids=["t1"], relation_type="1:1")],
        )

    def test_violation_rejects_the_output_with_an_alias_finding(self) -> None:
        report = OutputValidator().validate(
            ["s1"],
            self._output("出现了相对强弱背驰。"),
            source_texts={"s1": "An RSI divergence appeared."},
            locked_terms=[RSI],
        )
        self.assertFalse(report.ok)
        self.assertEqual(report.error_code, LOCKED_TERM_VIOLATED)
        finding = report.findings({"s1": "S1"})[0]
        self.assertIn('S1 contains the locked term "RSI divergence"', finding)
        self.assertIn('"RSI 背离"', finding)
        self.assertEqual(report.to_json()["term_violations"][0]["sentence_id"], "s1")

    def test_honoured_term_passes(self) -> None:
        report = OutputValidator().validate(
            ["s1"], self._output("出现了 RSI 背离。"), source_texts={"s1": "An RSI divergence appeared."}, locked_terms=[RSI]
        )
        self.assertTrue(report.ok)


if __name__ == "__main__":
    unittest.main()
