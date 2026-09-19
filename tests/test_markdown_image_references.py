"""Markdown image lines must render in common viewers: no escaped brackets in alt text."""

from __future__ import annotations

import re
import unittest

from book_agent.export.markup import MARKDOWN_IMAGE_PLACEHOLDER_ALT, markdown_image_reference

# The shape Typora, Quick Look and most editors render as an image.
_PLAIN_IMAGE = re.compile(r"^!\[[^\[\]\\]*\]\([^()\s]+\)$")


class MarkdownImageReferenceTests(unittest.TestCase):
    def test_placeholders_become_a_plain_label(self) -> None:
        for placeholder in ("[Image]", "[Figure]", "", "  "):
            line = markdown_image_reference(placeholder, "assets/pdf-images/ab86.png")
            self.assertEqual(line, f"![{MARKDOWN_IMAGE_PLACEHOLDER_ALT}](assets/pdf-images/ab86.png)")
            self.assertRegex(line, _PLAIN_IMAGE)

    def test_captions_keep_their_words_without_brackets_backslashes_or_line_breaks(self) -> None:
        line = markdown_image_reference("Fig. 6.3 [RSI]\nfailure \\ swing", "assets/pdf images/f.png")
        self.assertEqual(line, "![Fig. 6.3 (RSI) failure / swing](assets/pdf%20images/f.png)")
        self.assertRegex(line, _PLAIN_IMAGE)

    def test_ordinary_captions_are_unchanged(self) -> None:
        self.assertEqual(
            markdown_image_reference("Agent loop architecture", "assets/agent-loop.png"),
            "![Agent loop architecture](assets/agent-loop.png)",
        )


if __name__ == "__main__":
    unittest.main()
