"""Inline code a translation marks with backticks reads as inline code in the reading exports.

The translation writes "`find_dotenv()` 函数会向上逐级搜索父目录来定位 `.env` 文件";
merged HTML, the bilingual HTML and the PDF (printed from merged HTML) used to
show the backticks literally. Markdown keeps them, since there they are Markdown.
"""

from __future__ import annotations

import unittest

from book_agent.export import markup
from book_agent.export.models import MergedRenderBlock

_TARGET = "`find_dotenv()` 函数会向上逐级搜索父目录来定位 `.env` 文件"
_SOURCE = "The find_dotenv() function searches parent directories to locate the .env file."


def _block(block_type: str = "paragraph", *, source: str = _SOURCE, target: str | None = _TARGET) -> MergedRenderBlock:
    return MergedRenderBlock(
        block_id="b1",
        chapter_id="c1",
        block_type=block_type,
        render_mode="translated",
        artifact_kind=None,
        title=None,
        source_text=source,
        target_text=target,
        source_metadata={},
        source_sentence_ids=["s1"],
        target_segment_ids=["t1"],
        is_expected_source_only=False,
        notice=None,
    )


class InlineCodeSpanTest(unittest.TestCase):
    def test_backtick_spans_in_a_translation_become_inline_code(self) -> None:
        html_text = markup.render_block_html(_block())
        self.assertIn(
            "<div class='zh'><code class='inline-token'>find_dotenv()</code> 函数会向上逐级搜索父目录来定位 "
            "<code class='inline-token'>.env</code> 文件</div>",
            html_text,
        )
        self.assertNotIn("`", html_text)

    def test_merged_reading_edition_renders_the_same(self) -> None:
        html_text = markup.render_block_html(_block(), include_source_toggle=False, include_notice=False)
        self.assertIn("<code class='inline-token'>find_dotenv()</code>", html_text)
        self.assertNotIn("`", html_text)

    def test_code_is_escaped_and_carries_no_markup(self) -> None:
        rendered = markup.format_inline_text("用 `<div class=\"x\">&` 和 `**bold**` 包裹", code_spans=True)
        self.assertEqual(
            rendered,
            "用 <code class='inline-token'>&lt;div class=&quot;x&quot;&gt;&amp;</code> 和 "
            "<code class='inline-token'>**bold**</code> 包裹",
        )

    def test_only_balanced_pairs_become_code(self) -> None:
        self.assertEqual(markup.format_inline_text("a lone ` backtick", code_spans=True), "a lone ` backtick")
        self.assertEqual(
            markup.format_inline_text("调用 `run()` 后再看 `step", code_spans=True),
            "调用 <code class='inline-token'>run()</code> 后再看 `step",
        )
        self.assertEqual(markup.format_inline_text("`` `x` ``", code_spans=True), "<code class='inline-token'>`x`</code>")
        # A span never runs across lines.
        self.assertEqual(markup.format_inline_text("`a\nb`", code_spans=True), "`a<br/>b`")

    def test_source_text_and_list_items(self) -> None:
        # The English source is shown as written.
        self.assertEqual(markup.format_inline_text("use `x`"), "use `x`")
        list_html = markup.render_block_html(_block("list_item", source="• Call run()", target="• 调用 `run()` 方法"))
        self.assertIn("调用 <code class='inline-token'>run()</code> 方法", list_html)

    def test_markdown_export_keeps_backticks(self) -> None:
        self.assertIn(_TARGET, markup.render_block_markdown(_block()))


if __name__ == "__main__":
    unittest.main()
