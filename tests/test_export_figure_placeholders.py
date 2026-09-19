"""Placeholder text of PDF image/figure blocks must not be rendered as a caption.

Clustered vector figures carry the text "[Figure]"; the renderers only knew
"[Image]", so bilingual chapter exports showed a literal "[Figure]" caption.
"""

from __future__ import annotations

import unittest

from book_agent.export.markup import render_block_html, render_block_markdown, render_block_rebuilt_epub_xhtml
from book_agent.export.models import MergedRenderBlock


def _figure_block(source_text: str) -> MergedRenderBlock:
    return MergedRenderBlock(
        block_id="b1",
        chapter_id="c1",
        block_type="figure",
        render_mode="source_artifact_full_width",
        artifact_kind="figure",
        title=None,
        source_text=source_text,
        target_text=None,
        source_metadata={},
        source_sentence_ids=[],
        target_segment_ids=[],
        is_expected_source_only=True,
        notice=None,
    )


class FigurePlaceholderCaptionTest(unittest.TestCase):
    def test_placeholder_text_is_not_a_caption(self) -> None:
        assets = {"b1": "assets/figure.png"}
        for placeholder in ("[Figure]", "[Image]"):
            block = _figure_block(placeholder)
            with self.subTest(placeholder=placeholder):
                self.assertNotIn("figcaption", render_block_html(block, assets))
                self.assertNotIn("figcaption", render_block_rebuilt_epub_xhtml(block, assets))
                self.assertNotIn(f"*{placeholder}*", render_block_markdown(block, assets))

    def test_real_caption_is_kept(self) -> None:
        block = _figure_block("RSI panel with overbought and oversold zones")
        self.assertIn("<figcaption>RSI panel with overbought and oversold zones</figcaption>", render_block_html(block, {"b1": "a.png"}))


if __name__ == "__main__":
    unittest.main()
