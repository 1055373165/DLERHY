"""Worker output guardrail: coverage, echo, empty and length-ratio checks and their error codes."""

import unittest

from book_agent.translation.contracts import AlignmentSuggestion, TranslationTargetSegment, TranslationWorkerOutput
from book_agent.translation.output_validation import (
    COVERAGE_INCOMPLETE,
    LENGTH_RATIO_ABNORMAL,
    SOURCE_ECHOED,
    TARGET_EMPTY,
    OutputValidator,
)

LONG_SOURCE = "Context engineering is the discipline of deciding what a model sees before it answers a question."


def _output(*, alignments, segments=("t1",), texts=None, segment_type="sentence") -> TranslationWorkerOutput:
    texts = texts or {}
    return TranslationWorkerOutput(
        packet_id="packet-1",
        target_segments=[
            TranslationTargetSegment(
                temp_id=temp_id,
                text_zh=texts.get(temp_id, "译文"),
                segment_type=segment_type,
                source_sentence_ids=[],
                confidence=0.9,
            )
            for temp_id in segments
        ],
        alignment_suggestions=[
            AlignmentSuggestion(source_sentence_ids=sources, target_temp_ids=targets, relation_type="1:1", confidence=0.9)
            for sources, targets in alignments
        ],
    )


class OutputValidatorTests(unittest.TestCase):
    def test_full_coverage_is_ok(self) -> None:
        report = OutputValidator().validate(["s1", "s2"], _output(alignments=[(["s1", "s2"], ["t1"])]))

        self.assertTrue(report.ok)
        self.assertIsNone(report.error_code)

    def test_sentence_without_alignment_is_uncovered(self) -> None:
        report = OutputValidator().validate(["s1", "s2"], _output(alignments=[(["s1"], ["t1"])]))

        self.assertEqual(report.uncovered_sentence_ids, ("s2",))
        self.assertEqual(report.error_code, COVERAGE_INCOMPLETE)

    def test_alignment_to_unknown_target_does_not_cover(self) -> None:
        report = OutputValidator().validate(["s1"], _output(alignments=[(["s1", "stray"], ["missing"])]))

        self.assertEqual(report.uncovered_sentence_ids, ("s1",))
        self.assertEqual(report.unknown_source_sentence_ids, ("stray",))
        self.assertEqual(report.unknown_target_temp_ids, ("missing",))

    def test_echoed_source_is_rejected(self) -> None:
        report = OutputValidator().validate(
            ["s1"],
            _output(alignments=[(["s1"], ["t1"])], texts={"t1": LONG_SOURCE.upper()}),
            source_texts={"s1": LONG_SOURCE},
        )

        self.assertEqual(report.echoed_sentence_ids, ("s1",))
        self.assertEqual(report.error_code, SOURCE_ECHOED)
        self.assertIn("untranslated", report.findings({"s1": "S1"})[0])
        self.assertIn("S1", report.findings({"s1": "S1"})[0])

    def test_short_identifiers_may_be_copied_verbatim(self) -> None:
        report = OutputValidator().validate(
            ["s1"],
            _output(alignments=[(["s1"], ["t1"])], texts={"t1": "RSI 14"}),
            source_texts={"s1": "RSI 14"},
        )

        self.assertTrue(report.ok)

    def test_empty_target_is_rejected(self) -> None:
        report = OutputValidator().validate(
            ["s1"],
            _output(alignments=[(["s1"], ["t1"])], texts={"t1": "   "}),
            source_texts={"s1": "A sentence."},
        )

        self.assertEqual(report.empty_target_temp_ids, ("t1",))
        self.assertEqual(report.error_code, TARGET_EMPTY)

    def test_truncated_translation_is_rejected_by_length_ratio(self) -> None:
        report = OutputValidator().validate(
            ["s1"],
            _output(alignments=[(["s1"], ["t1"])], texts={"t1": "上下文。"}),
            source_texts={"s1": LONG_SOURCE},
        )

        self.assertEqual(report.error_code, LENGTH_RATIO_ABNORMAL)
        self.assertEqual(report.length_ratio_findings[0].source_sentence_ids, ("s1",))
        self.assertIn("truncation", report.findings()[0])

    def test_runaway_translation_is_rejected_by_length_ratio(self) -> None:
        report = OutputValidator().validate(
            ["s1"],
            _output(alignments=[(["s1"], ["t1"])], texts={"t1": "重复" * 200}),
            source_texts={"s1": LONG_SOURCE},
        )

        self.assertEqual(report.error_code, LENGTH_RATIO_ABNORMAL)
        self.assertIn("runaway", report.findings()[0])

    def test_short_source_and_code_segments_skip_the_ratio_check(self) -> None:
        short = OutputValidator().validate(
            ["s1"],
            _output(alignments=[(["s1"], ["t1"])], texts={"t1": "好"}),
            source_texts={"s1": "A short heading here"},
        )
        code = OutputValidator().validate(
            ["s1"],
            _output(alignments=[(["s1"], ["t1"])], texts={"t1": "x"}, segment_type="code"),
            source_texts={"s1": LONG_SOURCE},
        )

        self.assertTrue(short.ok)
        self.assertTrue(code.ok)

    def test_coverage_wins_the_error_code_and_all_codes_are_listed(self) -> None:
        report = OutputValidator().validate(
            ["s1", "s2"],
            _output(alignments=[(["s1"], ["t1"])], texts={"t1": ""}),
            source_texts={"s1": "A sentence.", "s2": "Another."},
        )

        self.assertEqual(report.error_code, COVERAGE_INCOMPLETE)
        self.assertEqual(report.error_codes, (COVERAGE_INCOMPLETE, TARGET_EMPTY))
        self.assertEqual(report.to_json()["error_codes"], [COVERAGE_INCOMPLETE, TARGET_EMPTY])


if __name__ == "__main__":
    unittest.main()
