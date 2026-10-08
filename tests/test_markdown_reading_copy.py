"""The Markdown reading copy draws what the HTML draws (the Agent book's Markdown, 2026-10-08).

The user's file had "- • " in front of 402 list items, every heading at "###", the printed contents
page with page numbers and "■" separators, English alt text on figures, and a SKILL.md listing
whose own "## Usage" line showed as a chapter in viewers that miss a four-backtick fence.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from book_agent.export import markup
from book_agent.export.models import MergedRenderBlock
from book_agent.services.export import ExportService


def block(block_type: str, source: str, target: str, mode: str = "zh_primary_with_optional_source", **metadata) -> MergedRenderBlock:
    return MergedRenderBlock(
        block_id="b1",
        chapter_id="c1",
        block_type=block_type,
        render_mode=mode,
        artifact_kind=None,
        title=None,
        source_text=source,
        target_text=target,
        source_metadata=dict(metadata),
        source_sentence_ids=[],
        target_segment_ids=[],
        is_expected_source_only=False,
        notice=None,
    )


class MarkdownBlockTests(unittest.TestCase):
    def test_a_list_item_has_one_marker(self) -> None:
        render = lambda item: markup.render_block_markdown(item, include_notice=False, include_source_caption=False)
        self.assertEqual(render(block("list_item", "• Developers who used LLM APIs", "• 用过 LLM API 的开发者")), "- 用过 LLM API 的开发者")
        # A dash sub-item the translation gave a bullet.
        dashed = block("list_item", "– Chapter 1 explores", "• 第 1 章探讨", list_kind="dash", list_level=2)
        self.assertEqual(render(dashed), "  - 第 1 章探讨")
        numbered = block("list_item", "1 Data layer", "• 数据层", list_kind="number")
        self.assertEqual(render(numbered), "1. 数据层")

    def test_heading_levels_follow_the_book(self) -> None:
        render = lambda level: markup.render_block_markdown(block("heading", "x", "标题", heading_level=level))
        self.assertEqual([render(2), render(3), render(4)], ["### 标题", "#### 标题", "##### 标题"])
        self.assertEqual(markup.render_block_markdown(block("heading", "x", "标题")), "### 标题")

    def test_code_with_its_own_fences_gets_a_tilde_fence(self) -> None:
        fenced = markup.markdown_fenced_block("# PDF Merge\n## Usage\n```python\nprint(1)\n```", language="markdown")
        self.assertTrue(fenced.startswith("~~~markdown\n"))
        self.assertTrue(fenced.endswith("\n~~~"))
        self.assertEqual(markup.markdown_fenced_block("print(1)"), "```\nprint(1)\n```")

    def test_a_figure_is_described_by_its_chinese_caption(self) -> None:
        figure = block(
            "figure",
            "Figure 2.2 LiteLLM, a unified wrapper",
            "图 2.2 LiteLLM，一个统一的封装层。后文接着讨论这张图。",
            mode="image_anchor_with_translated_caption",
        )
        rendered = markup.render_block_markdown(figure, {"b1": "assets/f.png"}, include_source_caption=False)
        self.assertTrue(rendered.startswith("![图 2.2 LiteLLM，一个统一的封装层。](assets/f.png)"), rendered)


class PrintedContentsTests(unittest.TestCase):
    def test_the_contents_chapter_is_recognized(self) -> None:
        service = ExportService.__new__(ExportService)
        chapter = lambda title, families=None: SimpleNamespace(
            chapter=SimpleNamespace(title_src=title, metadata_json={"pdf_page_family_counts": families or {}})
        )
        self.assertTrue(service._is_printed_contents_chapter(chapter("contents")))
        self.assertTrue(service._is_printed_contents_chapter(chapter("Brief Contents")))
        self.assertTrue(service._is_printed_contents_chapter(chapter("Front Matter", {"toc": 5, "body": 1})))
        self.assertFalse(service._is_printed_contents_chapter(chapter("1 What is an AI agent?", {"body": 20})))


if __name__ == "__main__":
    unittest.main()
