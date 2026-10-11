"""A list collapsed onto one line is laid out again; prose with numbers, dashes and pluses is not.

The export splits a paragraph whose list items the parse ran together ("…文本内容。● 提示2：…") back
into its items. It took any "N." or "-" for an item marker, so in the AI Product book a sentence
naming "section 10.1.1" was set as "1 / 0. / 1.1" on three lines, as were arXiv ids, a year,
"Data Collection + Evaluation" and a hyphen inside a URL.
"""

from __future__ import annotations

import unittest

from book_agent.export import render_repair
from book_agent.export.models import MergedRenderBlock


def _render_block(source_text: str, target_text: str) -> MergedRenderBlock:
    return MergedRenderBlock(
        block_id="b1",
        chapter_id="c1",
        block_type="paragraph",
        render_mode="zh_primary_with_optional_source",
        artifact_kind=None,
        title=None,
        source_text=source_text,
        target_text=target_text,
        source_metadata={"pdf_page_family": "body"},
        source_sentence_ids=[],
        target_segment_ids=[],
        is_expected_source_only=False,
        notice=None,
    )


class InlineListSplitTest(unittest.TestCase):
    def test_tokens_with_digits_dashes_and_pluses_stay_whole(self) -> None:
        for text in [
            "来把握 10.1.1 节中识别出的自动化机会。正如 10.1.2 节所归纳的那样。",
            "arXiv 预印本 arXiv: 2310.19736，2023 年 10 月。",
            "Jan. 2024. [Online]. Available: https://arxiv.org/pdf/2401.08500.",
            "Google PAIR，《Data Collection + Evaluation》，People + AI Guidebook",
            "www.anthropic.com/news/contextual-retrieval",
            "section 10.1.1. These tools 2. next",
        ]:
            with self.subTest(text=text):
                self.assertEqual(render_repair.split_inline_list_target_lines(text), [text])

    def test_collapsed_items_split_at_their_markers(self) -> None:
        self.assertEqual(
            render_repair.split_inline_list_target_lines("● 提示1：提取文本内容。● 提示2：总结清理后的文本。"),
            ["● 提示1：提取文本内容。", "● 提示2：总结清理后的文本。"],
        )
        self.assertEqual(
            render_repair.split_inline_list_target_lines("步骤如下：1. 收集数据。2. 训练模型。3. 评估。"),
            ["步骤如下：", "1. 收集数据。", "2. 训练模型。", "3. 评估。"],
        )
        self.assertEqual(
            render_repair.split_inline_list_target_lines("Do this. - first item"),
            ["Do this.", "- first item"],
        )

    def test_a_sentence_naming_section_numbers_keeps_its_translation_on_one_line(self) -> None:
        block = _render_block(
            "This section will teach you the patterns to address the opportunities identified in section "
            "10.1.1. These tools will also help you, as summarized in section 10.1.2. Next, we cover each.",
            "本节将教你运用各种模式，来把握 10.1.1 节中识别出的机会。正如 10.1.2 节所归纳的那样。",
        )
        self.assertIs(render_repair.repair_collapsed_list_target_text(block), block)

    def test_a_collapsed_list_is_laid_out_like_its_source(self) -> None:
        block = _render_block(
            "● Prompt 1: Extract text content. ● Prompt 2: Summarize the cleaned text.",
            "● 提示1：提取文本内容。● 提示2：总结清理后的文本。",
        )
        repaired = render_repair.repair_collapsed_list_target_text(block)
        self.assertEqual(repaired.target_text, "● 提示1：提取文本内容。\n● 提示2：总结清理后的文本。")


if __name__ == "__main__":
    unittest.main()
