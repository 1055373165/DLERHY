import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.segmentation.sentences import EnglishSentenceSegmenter
from book_agent.domain.structure.models import ParsedBlock


class SentenceSegmenterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.segmenter = EnglishSentenceSegmenter()

    def test_handles_abbreviations_and_decimals(self) -> None:
        text = "Dr. Smith paid 3.14 dollars. He left at 5 p.m. It was late."
        result = self.segmenter.segment_text(text)
        self.assertEqual(
            result,
            ["Dr. Smith paid 3.14 dollars.", "He left at 5 p.m.", "It was late."],
        )

    def test_heading_is_single_sentence(self) -> None:
        block = ParsedBlock(
            block_type="heading",
            text="Chapter 1: Introduction",
            source_path="chapter1.xhtml",
            ordinal=1,
        )
        segmented = self.segmenter.segment_block(block)
        self.assertEqual(len(segmented.sentences), 1)
        self.assertEqual(segmented.sentences[0].text, "Chapter 1: Introduction")


if __name__ == "__main__":
    unittest.main()
