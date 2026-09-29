"""PDF list items and sidebars as HTML: bullets and numbers as hanging markers, sidebars as one box."""

from __future__ import annotations

import unittest

from book_agent.export.markup import render_block_html
from book_agent.export.models import MergedRenderBlock


def _list_item(
    source_text: str,
    target_text: str | None,
    metadata: dict[str, object] | None = None,
    *,
    block_type: str = "list_item",
) -> MergedRenderBlock:
    return MergedRenderBlock(
        block_id="b1",
        chapter_id="c1",
        block_type=block_type,
        render_mode="translated",
        artifact_kind=None,
        title=None,
        source_text=source_text,
        target_text=target_text,
        source_metadata=dict(metadata or {}),
        source_sentence_ids=[],
        target_segment_ids=[],
        is_expected_source_only=False,
        notice=None,
    )


class BulletedListItemHtmlTest(unittest.TestCase):
    def test_bullet_glyph_becomes_marker_class(self) -> None:
        html = render_block_html(_list_item("• Closing price decides RSI level", "• 收盘价决定 RSI 水平"))
        self.assertIn("<section class='block list_item bulleted'>", html)
        self.assertIn("<div class='zh'>收盘价决定 RSI 水平</div>", html)
        # The source toggle keeps the original text.
        self.assertIn("• Closing price decides RSI level", html)

    def test_an_item_whose_translation_dropped_the_bullet_keeps_its_marker(self) -> None:
        # Build an AI Agent, "This chapter covers": one of five items came back without its "•".
        html = render_block_html(_list_item("• Workflows versus agents and when to use each", "工作流与智能体的对比及各自适用场景"))
        self.assertIn("<section class='block list_item bulleted'>", html)
        self.assertIn("<div class='zh'>工作流与智能体的对比及各自适用场景</div>", html)

    def test_numbered_items_are_left_alone(self) -> None:
        html = render_block_html(_list_item("1. Tops and Bottoms", "1. 顶部与底部"))
        self.assertIn("<section class='block list_item'>", html)
        self.assertIn("<div class='zh'>1. 顶部与底部</div>", html)


class NestedListItemHtmlTest(unittest.TestCase):
    """Build an AI Agent 1.6: dash sub-items under a bullet, and a label set inside the list."""

    def test_dash_sub_item_is_nested_under_its_bullet(self) -> None:
        html = render_block_html(
            _list_item(
                "– Basic data structures such as variables",
                "– 基本数据结构，如变量",
                {"list_kind": "dash", "list_level": 2},
            )
        )
        self.assertIn("<section class='block list_item dashed sub-item'>", html)
        self.assertIn("<div class='zh' data-marker='–'>基本数据结构，如变量</div>", html)

    def test_numbered_item_draws_its_number(self) -> None:
        # A Simple Guide to RAG 2.6: "1 Data layer—..." (the number set apart from the text).
        html = render_block_html(
            _list_item("1 Data layer—Tools and platforms", "1 数据层——工具与平台", {"list_kind": "number", "list_level": 1})
        )
        self.assertIn("<section class='block list_item numbered'>", html)
        self.assertIn("<div class='zh' data-marker='1.'>数据层——工具与平台</div>", html)

    def test_text_set_inside_a_list_is_indented_with_it(self) -> None:
        html = render_block_html(
            _list_item("Cost considerations:", "成本考量：", {"list_level": 1, "list_continuation": True}, block_type="paragraph")
        )
        self.assertIn("<section class='block paragraph list-continuation'>", html)


class SidebarHtmlTest(unittest.TestCase):
    """A Simple Guide to RAG 2.3: a sidebar's blocks share one box, its title set as the box's title."""

    def test_sidebar_blocks_render_in_one_aside_with_a_title(self) -> None:
        from book_agent.export.markup import render_blocks_html

        blocks = [
            _list_item("Before the sidebar.", "侧栏之前。", block_type="paragraph"),
            _list_item(
                "Do you always need an indexing pipeline?",
                "你总是需要索引流水线吗？",
                {"sidebar_id": "s1", "sidebar_title": True},
                block_type="paragraph",
            ),
            _list_item("Offline indexing pipelines are typical.", "离线索引流水线很常见。", {"sidebar_id": "s1"}, block_type="paragraph"),
            _list_item("(continued)", "（续）", {"sidebar_id": "s1", "sidebar_continued_label": True}, block_type="paragraph"),
            _list_item("After the sidebar.", "侧栏之后。", block_type="paragraph"),
        ]
        html = render_blocks_html(blocks, render_block_html)
        self.assertEqual(html.count("<aside class='sidebar'>"), 1)
        self.assertEqual(html.count("</aside>"), 1)
        inside = html.split("<aside class='sidebar'>", 1)[1].split("</aside>", 1)[0]
        self.assertIn("<section class='block paragraph sidebar-title'><div class='zh'>你总是需要索引流水线吗？</div>", inside)
        self.assertIn("离线索引流水线很常见。", inside)
        self.assertNotIn("（续）", html)
        self.assertNotIn("侧栏之后", inside)


if __name__ == "__main__":
    unittest.main()
