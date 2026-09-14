"""Worker output coverage validation and the error code it leaves on translation runs."""

import unittest

from book_agent.translation.contracts import AlignmentSuggestion, TranslationTargetSegment, TranslationWorkerOutput
from book_agent.translation.output_validation import COVERAGE_INCOMPLETE, OutputValidator


def _output(*, alignments, segments=("t1",)) -> TranslationWorkerOutput:
    return TranslationWorkerOutput(
        packet_id="packet-1",
        target_segments=[
            TranslationTargetSegment(temp_id=temp_id, text_zh="译文", segment_type="sentence", source_sentence_ids=[], confidence=0.9)
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


if __name__ == "__main__":
    unittest.main()
