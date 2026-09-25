"""A run-in subheading is not the rest of the paragraph before it (the Agent book's 22 subheadings).

Manning books set a subheading in bold at the start of its paragraph ("how the agent loop works
The loop is necessary ..."). The reading copy took a paragraph that starts in lowercase for the
rest of a sentence broken by the page and glued it onto the paragraph before, with no space:
"... it over time.how the agent loop works The loop ...". After a finished sentence a lowercase
start is not a continuation; and a merge of English text never glues two words.
"""

from __future__ import annotations

import unittest
from dataclasses import replace

from book_agent.export.models import MergedRenderBlock
from book_agent.export.render_repair import merge_render_text_fragments, should_merge_adjacent_book_paragraph_fragments


def paragraph(block_id: str, text: str, order: int) -> MergedRenderBlock:
    return MergedRenderBlock(
        block_id=block_id,
        chapter_id="c1",
        block_type="paragraph",
        render_mode="zh_primary_with_optional_source",
        artifact_kind=None,
        title=None,
        source_text=text,
        target_text="译文",
        source_metadata={"source_page_start": 26, "reading_order_index": order, "pdf_page_family": "body"},
        source_sentence_ids=[],
        target_segment_ids=[],
        is_expected_source_only=False,
        notice=None,
    )


class RunInHeadingMergeTests(unittest.TestCase):
    def test_a_lowercase_start_after_a_finished_sentence_is_not_merged(self) -> None:
        previous = paragraph("p1", "To summarize, the loop unfolds it over time.", 1)
        heading = paragraph("p2", "how the agent loop works The loop is necessary because it is hard to know.", 2)
        self.assertFalse(should_merge_adjacent_book_paragraph_fragments(previous, heading))

    def test_a_sentence_broken_by_the_page_is_still_merged(self) -> None:
        previous = paragraph("p1", "The model predicts the next token from the", 1)
        rest = paragraph("p2", "tokens before it, one at a time.", 2)
        self.assertTrue(should_merge_adjacent_book_paragraph_fragments(previous, rest))
        after_abbreviation = replace(previous, source_text="Tools extend what an agent can do, e.g.")
        self.assertTrue(should_merge_adjacent_book_paragraph_fragments(after_abbreviation, rest))

    def test_a_heading_number_translated_on_its_own_keeps_its_space(self) -> None:
        from book_agent.export.render_repair import inline_join_target_text

        # The Agent book: "1.4.1" and "需要 LLM 的任务" came back as two segments of one heading.
        self.assertEqual(inline_join_target_text(["1.4.1", "需要 LLM 的任务"]), "1.4.1 需要 LLM 的任务")
        self.assertEqual(inline_join_target_text(["第一句。", "第二句。"]), "第一句。第二句。")
        # Segments meeting as Latin and Chinese keep a space, as inside a segment.
        self.assertEqual(inline_join_target_text(["本书的选择：OpenAI API", "在本书中我们使用它。"]), "本书的选择：OpenAI API 在本书中我们使用它。")

    def test_english_fragments_never_glue_two_words(self) -> None:
        self.assertEqual(merge_render_text_fragments("over time.", "how it works"), "over time. how it works")
        self.assertEqual(merge_render_text_fragments("pre-", "trained"), "pre-trained")
        self.assertEqual(merge_render_text_fragments("随着时间展开。", "循环如何运作"), "随着时间展开。循环如何运作")


if __name__ == "__main__":
    unittest.main()
