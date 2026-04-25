# ruff: noqa: E402
"""Tests for M3.4 image / figure modality."""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.structure.models import (
    TRANSLATE_ALL,
    TRANSLATE_NONE,
    ParsedBlock,
    ParsedChapter,
    ParsedDocument,
)
from book_agent.services.image_modality import (
    enhance_block_for_image,
    enhance_caption_block,
    enhance_document_image_modality,
)


def _block(
    *,
    block_type: str,
    text: str = "",
    metadata: dict | None = None,
    translatability: str = TRANSLATE_ALL,
    ordinal: int = 1,
    anchor: str | None = None,
) -> ParsedBlock:
    return ParsedBlock(
        block_type=block_type,
        text=text,
        source_path="x",
        ordinal=ordinal,
        anchor=anchor or f"b{ordinal}",
        metadata=metadata or {},
        translatability=translatability,
    )


class EnhanceBlockForImageTests(unittest.TestCase):
    def test_image_block_protected(self) -> None:
        block = _block(block_type="image", text="[Image]")
        out = enhance_block_for_image(block)
        self.assertEqual(out.translatability, TRANSLATE_NONE)

    def test_figure_block_protected(self) -> None:
        block = _block(block_type="figure", text="[Image]")
        out = enhance_block_for_image(block)
        self.assertEqual(out.translatability, TRANSLATE_NONE)

    def test_image_alt_promoted_to_canonical_when_text_empty(self) -> None:
        block = _block(
            block_type="figure",
            text="",
            metadata={"image_alt": "Architecture diagram"},
        )
        out = enhance_block_for_image(block)
        self.assertEqual(out.text, "Architecture diagram")
        self.assertEqual(
            out.metadata.get("image_canonical_alt"),
            "Architecture diagram",
        )

    def test_image_alt_does_not_overwrite_existing_text(self) -> None:
        block = _block(
            block_type="figure",
            text="A diagram of agent loops",
            metadata={"image_alt": "raw alt"},
        )
        out = enhance_block_for_image(block)
        self.assertEqual(out.text, "A diagram of agent loops")
        self.assertEqual(out.metadata.get("image_canonical_alt"), "raw alt")

    def test_empty_image_block_uses_placeholder(self) -> None:
        block = _block(block_type="image", text="")
        out = enhance_block_for_image(block)
        self.assertEqual(out.text, "[Image]")
        self.assertEqual(out.translatability, TRANSLATE_NONE)

    def test_non_image_block_passes_through(self) -> None:
        block = _block(block_type="paragraph", text="Plain prose.")
        out = enhance_block_for_image(block)
        self.assertIs(out, block)


class EnhanceCaptionBlockTests(unittest.TestCase):
    def test_caption_already_translatable_unchanged(self) -> None:
        block = _block(block_type="caption", text="Figure 1.1: Data flow")
        out = enhance_caption_block(block)
        self.assertIs(out, block)

    def test_caption_with_none_is_re_enabled(self) -> None:
        block = _block(
            block_type="caption",
            text="Figure 1.1: Data flow",
            translatability=TRANSLATE_NONE,
        )
        out = enhance_caption_block(block)
        self.assertEqual(out.translatability, TRANSLATE_ALL)

    def test_non_caption_block_unchanged(self) -> None:
        block = _block(block_type="paragraph", text="x", translatability=TRANSLATE_NONE)
        out = enhance_caption_block(block)
        self.assertIs(out, block)


class EnhanceDocumentImageModalityTests(unittest.TestCase):
    def _doc(self, blocks: list[ParsedBlock]) -> ParsedDocument:
        return ParsedDocument(
            title="T", author="A", language="en",
            chapters=[
                ParsedChapter(
                    chapter_id="ch1",
                    href="h1",
                    title="Chapter 1",
                    blocks=blocks,
                )
            ],
        )

    def test_doc_pass_protects_images_and_re_enables_captions(self) -> None:
        doc = self._doc(
            [
                _block(block_type="paragraph", text="Intro.", ordinal=1),
                _block(
                    block_type="figure",
                    text="",
                    metadata={"image_alt": "Diagram"},
                    ordinal=2,
                ),
                _block(
                    block_type="caption",
                    text="Figure 1.1 Data flow",
                    translatability=TRANSLATE_NONE,  # bug case
                    ordinal=3,
                ),
                _block(block_type="paragraph", text="Discussion.", ordinal=4),
            ]
        )
        rewritten, summary = enhance_document_image_modality(doc)
        blocks = list(rewritten.chapters[0].blocks)

        self.assertEqual(blocks[0].translatability, TRANSLATE_ALL)  # paragraph
        self.assertEqual(blocks[1].translatability, TRANSLATE_NONE)  # figure
        self.assertEqual(blocks[1].text, "Diagram")
        self.assertEqual(blocks[1].metadata.get("image_canonical_alt"), "Diagram")
        self.assertEqual(blocks[2].translatability, TRANSLATE_ALL)  # caption restored
        self.assertEqual(blocks[3].translatability, TRANSLATE_ALL)

        self.assertEqual(summary.image_blocks_protected, 1)
        self.assertEqual(summary.captions_re_enabled, 1)
        self.assertEqual(summary.alt_text_filled, 1)

    def test_doc_pass_idempotent(self) -> None:
        doc = self._doc(
            [
                _block(
                    block_type="figure",
                    text="Diagram",
                    metadata={"image_alt": "Diagram"},
                    ordinal=1,
                    translatability=TRANSLATE_NONE,
                ),
            ]
        )
        rewritten1, summary1 = enhance_document_image_modality(doc)
        rewritten2, summary2 = enhance_document_image_modality(rewritten1)
        # Second pass must report nothing-to-do.
        self.assertEqual(summary2.image_blocks_protected, 0)
        self.assertEqual(summary2.captions_re_enabled, 0)
        # ...and the blocks still satisfy the contract.
        self.assertEqual(
            rewritten2.chapters[0].blocks[0].translatability,
            TRANSLATE_NONE,
        )


if __name__ == "__main__":
    unittest.main()
