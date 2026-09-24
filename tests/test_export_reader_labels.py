"""Reader-facing exports carry the book's text only, never the export's own notes to reviewers."""

from __future__ import annotations

import unittest

from book_agent.export import markup
from book_agent.export.models import MergedRenderBlock

NOTES = ("图片锚点保留", "图片原样保留", "代码保持原样", "公式保持原样", "表格原结构保留", "尾部资料页保留原样")


def _block(render_mode: str, artifact_kind: str | None, notice: str, text: str = "print('x')") -> MergedRenderBlock:
    return MergedRenderBlock(
        block_id="b1",
        chapter_id="c1",
        block_type="code" if artifact_kind == "code" else "image",
        render_mode=render_mode,
        artifact_kind=artifact_kind,
        title=None,
        source_text=text,
        target_text=None,
        source_metadata={},
        source_sentence_ids=[],
        target_segment_ids=[],
        is_expected_source_only=True,
        notice=notice,
    )


class ReaderLabelTests(unittest.TestCase):
    def test_html_markdown_and_epub_leave_the_notes_out(self) -> None:
        blocks = [
            _block("source_artifact_full_width", "code", "代码保持原样"),
            _block("source_artifact_full_width", "image", "图片原样保留", text="Figure 1"),
            _block("image_anchor_with_translated_caption", "image", "图片锚点保留", text="Figure 2"),
        ]
        for block in blocks:
            rendered = [
                markup.render_block_html(block),
                markup.render_block_markdown(block),
                markup.render_block_rebuilt_epub_xhtml(block),
            ]
            for output in rendered:
                for note in NOTES:
                    self.assertNotIn(note, output, (block.render_mode, output[:200]))
                self.assertNotIn("artifact-note'>" + "代码", output)

    def test_a_review_view_can_still_ask_for_them(self) -> None:
        block = _block("source_artifact_full_width", "code", "代码保持原样")
        self.assertIn("代码保持原样", markup.render_block_html(block, include_notice=True))


if __name__ == "__main__":
    unittest.main()
