"""A book's closing References: one block per entry, kept as written, grouped under its chapter labels.

How Large Language Models Work's References came out as a handful of blocks of up to 2,900
characters: its "[n]" markers were no list markers to the splitter, a wrapped DOI's digits
("0956797617742725") read as a contents page's number, and "[10]" is set a digit's width left of
"[9]". The translation then rendered some entries in Chinese and left others in English, and the
export dropped the "Chapter 3" labels that group the entries as if they were chapter-opening kickers.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from book_agent.domain.enums import BlockType, ProtectedPolicy
from book_agent.domain.structure.models import TRANSLATE_NONE, ParsedBlock
from book_agent.export.render_repair import render_mode_for_block, should_drop_book_heading_label
from book_agent.ingestion.pdf.chapters import _keep_reference_entries_in_source
from book_agent.ingestion.pdf.extract import _list_item_kinds


def _bboxes(xs: list[float]) -> list[tuple[float, float, float, float]]:
    return [(x, 100.0 + 12 * index, 500.0, 110.0 + 12 * index) for index, x in enumerate(xs)]


class BracketedReferenceListTests(unittest.TestCase):
    def test_bracketed_numbers_with_hanging_lines_are_list_items(self) -> None:
        lines = [
            "[1] Romeo, R. R., Leonard, J. A., et al. (2018). Beyond the 30-million-word gap. https://doi.org/10.1177/",
            "0956797617742725",
            "[2] Gilkerson, J., Richards, J. A., et al. (2017). Mapping the early language environment.",
        ]
        self.assertEqual(_list_item_kinds(lines, _bboxes([104.7, 125.7, 104.7])), ["number", "", "number"])

    def test_right_aligned_markers_share_an_indent(self) -> None:
        lines = ["[9] OpenAI. (2023). GPT-4 technical report.", "[10] Chowdhery, A., et al. (2022). PaLM.", "[11] Liang, W., et al. (2024)."]
        self.assertEqual(_list_item_kinds(lines, _bboxes([104.7, 99.6, 99.6])), ["number", "number", "number"])

    def test_markers_at_unrelated_indents_are_not_a_list(self) -> None:
        lines = ["[1] Denk, T. (2019). Linear relationships.", "[2] Raff, E. (2022). Inside Deep Learning."]
        self.assertEqual(_list_item_kinds(lines, _bboxes([104.7, 140.0])), ["", ""])


def _block(text: str, block_type: str = "list_item", role: str = "list_item") -> ParsedBlock:
    return ParsedBlock(
        block_type=block_type, text=text, source_path="book.pdf", ordinal=1,
        metadata={"pdf_block_role": role, "translatable": True, "nontranslatable_reason": None},
    )


class KeepReferenceEntriesTests(unittest.TestCase):
    def test_entries_and_a_page_cut_continuation_stay_as_written(self) -> None:
        blocks = _keep_reference_entries_in_source(
            [
                _block("Chapter 5", "heading", "heading"),
                _block("[8] Kolter, Z., and Madry, M. (n.d.). Adversarial robustness: Theory and practice."),
                _block("164 REFERENCES", "paragraph", "header"),
                _block("(2023, March 27). GPT-4 technical report. https://cdn.openai.com/papers/gpt-4.pdf", "paragraph", "body"),
                _block("Chapter 6", "heading", "heading"),
            ]
        )
        kept = [block.metadata.get("nontranslatable_reason") == "pdf_reference_entry" for block in blocks]
        self.assertEqual(kept, [False, True, False, True, False])
        self.assertEqual(blocks[1].translatability, TRANSLATE_NONE)

    def test_prose_after_a_heading_is_translated(self) -> None:
        blocks = _keep_reference_entries_in_source(
            [_block("Chapter 1", "heading", "heading"), _block("These works go deeper (2020).", "paragraph", "body")]
        )
        self.assertNotIn("pdf_reference_entry", [block.metadata.get("nontranslatable_reason") for block in blocks])


class ReferenceExportTests(unittest.TestCase):
    def test_a_kept_entry_renders_as_text_not_as_a_code_artifact(self) -> None:
        text = "[2] Raff, E. (2022). Inside Deep Learning. Manning."
        metadata = {"pdf_block_role": "list_item", "translatable": False, "nontranslatable_reason": "pdf_reference_entry"}
        block = SimpleNamespace(
            block_type=BlockType.LIST_ITEM, source_text=text, protected_policy=ProtectedPolicy.PROTECT, source_span_json=metadata
        )
        self.assertEqual(render_mode_for_block(block, [], metadata), "zh_primary_with_optional_source")

    def test_chapter_labels_head_the_reference_groups(self) -> None:
        bundle = SimpleNamespace(chapter=SimpleNamespace(title_src="References"))
        label = SimpleNamespace(source_text="Chapter 3")
        self.assertFalse(should_drop_book_heading_label(bundle, 4, label))
        body_chapter = SimpleNamespace(chapter=SimpleNamespace(title_src="3 Transformers"))
        self.assertTrue(should_drop_book_heading_label(body_chapter, 4, label))


if __name__ == "__main__":
    unittest.main()
