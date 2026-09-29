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


def _write_nested_list_page(path: Path) -> None:
    """Build an AI Agent 1.6: a bullet with dash sub-items, a label inside the list, the body after it."""
    document = fitz.open()
    page = document.new_page(width=_PAGE[0], height=_PAGE[1])
    page.insert_text((102, 80), "To follow the exercises you need the following things set up first:", fontname="tiro", fontsize=10)
    # One text block holds the bullet, its sub-items, a label, more sub-items and the body after the list.
    lines = [
        (118, "• Development-environment setup—To complete the exercises effectively, a local"),
        (130, "Python development environment is necessary:"),
        (130, "– Install the latest version of Python in your local environment (recommended:"),
        (142, "Python 3.12 or later)."),
        (130, "– Prepare a code editor or IDE (recommended: Visual Studio Code)."),
        (130, "Cost considerations:"),
        (130, "– OpenAI—The total cost of completing all exercises should be less than $10"),
        (102, "When you have completed these preparations, you are ready to begin your journey into"),
        (102, "agent development with this book and the chapters that follow it."),
    ]
    writer = fitz.TextWriter(page.rect)
    font = fitz.Font("tiro")
    for index, (x, line) in enumerate(lines):
        writer.append((x, 110 + 13 * index), line, font=font, fontsize=10)
    writer.write_text(page)
    document.save(path)


def _write_numbered_list_page(path: Path) -> None:
    """A Simple Guide to RAG 2.6: numbers set apart from the items, each line its own text block."""
    document = fitz.open()
    page = document.new_page(width=_PAGE[0], height=_PAGE[1])
    page.insert_text((93, 80), "The infrastructure can be understood in several layers:", fontname="tiro", fontsize=10)
    font = fitz.Font("tiro")
    top = 110
    for number, text, wrap in [
        (1, "Data layer—Tools and platforms used to process and store data in the form of", "embeddings"),
        (2, "Model layer—Providers of proprietary or open source LLMs", None),
        (3, "Prompt layer—Tools offering maintenance and evaluation of prompts", None),
    ]:
        # Each item and each wrapped line is a text block of its own, as the book's text layer has it.
        writer = fitz.TextWriter(page.rect)
        writer.append((105, top), f"{number}  {text}", font=font, fontsize=10)
        writer.write_text(page)
        top += 16
        if wrap:
            page.insert_text((117, top), wrap, fontname="tiro", fontsize=10)
            top += 16
    page.insert_text((93, top + 10), "Chapter 7 explores the various layers of infrastructure that support RAG.", fontname="tiro", fontsize=10)
    document.save(path)


class NestedListTest(unittest.TestCase):
    """Dash sub-items are items of their bullet's list; the body after the list is a paragraph again."""

    def test_sub_items_nest_under_their_bullet(self) -> None:
        blocks = _blocks(_parse(_write_nested_list_page, "nested.pdf"))
        shape = [
            (block.block_type, block.metadata.get("list_level"), " ".join(block.text.split())[:30])
            for block in blocks
            if block.metadata.get("list_level") or block.text.startswith("When you")
        ]
        self.assertEqual(
            shape,
            [
                ("list_item", 1, "• Development-environment setu"),
                ("list_item", 2, "– Install the latest version o"),
                ("list_item", 2, "– Prepare a code editor or IDE"),
                ("paragraph", 1, "Cost considerations:"),
                ("list_item", 2, "– OpenAI—The total cost of com"),
                ("paragraph", None, "When you have completed these "),
            ],
        )
        self.assertEqual(blocks[-1].metadata.get("list_continuation"), None)
        label = next(block for block in blocks if block.text.startswith("Cost"))
        self.assertTrue(label.metadata["list_continuation"])
        # A wrapped sub-item keeps its wrapped line.
        install = next(block for block in blocks if block.text.startswith("– Install"))
        self.assertIn("Python 3.12 or later).", install.text)

    def test_numbered_items_set_one_per_line_are_list_items(self) -> None:
        blocks = _blocks(_parse(_write_numbered_list_page, "numbered.pdf"))
        items = [block for block in blocks if block.block_type == "list_item"]
        self.assertEqual([block.metadata.get("list_kind") for block in items], ["number"] * 3)
        self.assertEqual(" ".join(items[0].text.split()), "1 Data layer—Tools and platforms used to process and store data in the form of embeddings")
        closing = next(block for block in blocks if block.text.startswith("Chapter 7"))
        self.assertEqual(closing.block_type, "paragraph")


if __name__ == "__main__":
    unittest.main()
