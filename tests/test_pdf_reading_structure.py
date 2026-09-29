"""Reading structure that two Manning PDFs set on the page and the parser flattened.

Each trait was seen in a reading copy: an identifier split by a space
("final_ answer"), dash sub-items run into their bullet or left after the
list, a numbered list read as one paragraph, a shaded sidebar whose title
read as a heading of the chapter, a sentence cut in two by a figure, and a
listing title left as a paragraph above its code. Each test builds a small
PDF with one trait and checks the parsed structure.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import fitz

from book_agent.domain.structure.pdf import PDFParser

_PAGE = (531, 666)
_BODY = (
    "Retrieval gives a language model facts it never saw during training, and this paragraph "
    "is long enough to read as ordinary running prose in the body of the chapter."
)


def _parse(write, name: str):
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / name
        write(pdf_path)
        return PDFParser(image_output_dir=Path(tmpdir) / "images").parse(pdf_path)


def _blocks(parsed):
    return [block for chapter in parsed.chapters for block in chapter.blocks]


def _text(parsed) -> str:
    return " ".join(" ".join(block.text for block in _blocks(parsed)).split())


def _write_split_identifier_page(path: Path) -> None:
    document = fitz.open()
    page = document.new_page(width=_PAGE[0], height=_PAGE[1])
    # Prose that breaks identifiers: inside a line and at a line end.
    for index, line in enumerate(
        [
            "Set is_solvable and give your answer in final_ answer when you can.",
            "Otherwise explain why in unsolvable_ reason, and keep the max_",
            "tokens budget small. The foo_ bar pair is not an identifier.",
        ]
    ):
        page.insert_text((72, 90 + 13 * index), line, fontname="tiro", fontsize=10)
    # The listing writes the identifiers whole.
    for index, line in enumerate(
        [
            "class GaiaOutput(BaseModel):",
            "    final_answer: str = ''",
            "    unsolvable_reason: str = ''",
            "client.create(max_tokens=100)",
        ]
    ):
        page.insert_text((90, 200 + 12 * index), line, fontname="cour", fontsize=9)
    document.save(path)


class SplitIdentifierTest(unittest.TestCase):
    """An identifier the book writes whole is one identifier again; other pairs keep their space."""

    def test_identifiers_split_by_a_space_are_rejoined(self) -> None:
        text = _text(_parse(_write_split_identifier_page, "identifiers.pdf"))
        for joined in ("answer in final_answer when", "why in unsolvable_reason, and", "the max_tokens budget"):
            self.assertIn(joined, text)
        self.assertIn("The foo_ bar pair", text)

    def test_evidence_comes_from_identifiers_written_inside_a_line(self) -> None:
        from book_agent.ingestion.pdf.identifiers import inline_identifier_counts, rejoin_split_identifiers

        counts = inline_identifier_counts(
            [
                "   final_answer: str = ''",
                "context.state['calls'] = [p.model_dump() for p in calls]",
                "return root._find_in_subtree(name)",
                "response = client.chat.completions.create(",
                "The max_",
                "tokens parameter, and the run_ experiment function",
            ]
        )
        # Parts cut off at a line end or split inside a line are not identifiers of the book.
        self.assertEqual(counts["max_"], 0)
        self.assertEqual(counts["run_"], 0)
        self.assertEqual(counts["model_dump"], 1)
        cases = {
            "answer in “final_ answer”.": "answer in “final_answer”.",
            "Pydantic’s model_ dump() method": "Pydantic’s model_dump() method",
            "call client.chat. completions.create() once": "call client.chat.completions.create() once",
            # A sentence that ends before one starting with a name is two sentences.
            "search the root. _find_in_subtree works recursively": "search the root. _find_in_subtree works recursively",
            # No evidence: the pair stays as printed.
            "the run_ experiment function": "the run_ experiment function",
            "a new_ idea": "a new_ idea",
        }
        for source, expected in cases.items():
            self.assertEqual(rejoin_split_identifiers(source, counts), expected, source)


if __name__ == "__main__":
    unittest.main()
