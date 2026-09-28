"""Section, subsection and run-in headings render at their own sizes, not all as one."""

from __future__ import annotations

import unittest

from book_agent.export import markup
from book_agent.export.models import MergedRenderBlock


def _heading(level: int | None) -> MergedRenderBlock:
    return MergedRenderBlock(
        block_id="h1",
        chapter_id="c1",
        block_type="heading",
        render_mode="zh_primary_with_optional_source",
        artifact_kind=None,
        title=None,
        source_text="1.1.1 LLMs are not trained for facts",
        target_text="1.1.1 LLM 并非为事实而训练",
        source_metadata={} if level is None else {"heading_level": level},
        source_sentence_ids=[],
        target_segment_ids=[],
        is_expected_source_only=False,
        notice="",
    )


class HeadingLevelTests(unittest.TestCase):
    def test_the_level_is_a_class(self) -> None:
        self.assertIn("class='block heading level-3'", markup.render_block_html(_heading(3)))
        self.assertIn("class='block heading level-2'", markup.render_block_html(_heading(2)))

    def test_a_heading_without_a_level_keeps_the_plain_class(self) -> None:
        self.assertIn("class='block heading'", markup.render_block_html(_heading(None)))


if __name__ == "__main__":
    unittest.main()
