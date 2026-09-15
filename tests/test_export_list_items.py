"""PDF list items keep their bullet glyph in the text; HTML exports draw it as a hanging marker."""

from __future__ import annotations

import unittest

from book_agent.export.markup import render_block_html
from book_agent.export.models import MergedRenderBlock


def _list_item(source_text: str, target_text: str | None) -> MergedRenderBlock:
    return MergedRenderBlock(
        block_id="b1",
        chapter_id="c1",
        block_type="list_item",
        render_mode="translated",
        artifact_kind=None,
        title=None,
        source_text=source_text,
        target_text=target_text,
        source_metadata={},
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

    def test_numbered_items_are_left_alone(self) -> None:
        html = render_block_html(_list_item("1. Tops and Bottoms", "1. 顶部与底部"))
        self.assertIn("<section class='block list_item'>", html)
        self.assertIn("<div class='zh'>1. 顶部与底部</div>", html)


if __name__ == "__main__":
    unittest.main()
