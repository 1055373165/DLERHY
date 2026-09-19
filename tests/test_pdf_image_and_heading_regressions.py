"""Regressions found in a translated trading book.

* Two different pictures on one page were exported as the same picture: the
  embedded-image lookup returned the first already-materialized image on the
  page instead of the image nearest to the block.
* After the parse was fixed, re-exporting kept the wrong picture: export copied
  an asset only when no file of that (block-id) name existed yet.
* A bold numbered section title ending in "etc." was recognised as a heading by
  the parser and then demoted to a paragraph by the export's prose-shape rules.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import fitz

from book_agent.domain.enums import BlockType
from book_agent.domain.structure.pdf import PDFParser
from book_agent.export.models import MergedRenderBlock
from book_agent.export.render_repair import should_demote_book_heading_to_paragraph
from book_agent.services.export import _copy_asset_if_changed


def _solid_png(color: tuple[int, int, int]) -> bytes:
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 30), False)
    pixmap.set_rect(pixmap.irect, color)
    return pixmap.tobytes("png")


class SamePageImagesTest(unittest.TestCase):
    def test_each_image_block_gets_its_own_embedded_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "images.pdf"
            document = fitz.open()
            document.new_page().insert_text((72, 200), "Momentum Trading Notes", fontname="hebo", fontsize=24)
            page = document.new_page()
            page.insert_text((72, 90), "If you have not seen the pattern before, see the picture below.")
            page.insert_image(fitz.Rect(72, 100, 372, 300), stream=_solid_png((200, 30, 30)))
            page.insert_text((72, 320), "The inverted pattern is just the opposite, as you can see below.")
            page.insert_image(fitz.Rect(72, 330, 372, 530), stream=_solid_png((30, 30, 200)))
            document.save(pdf_path)

            parsed = PDFParser(image_output_dir=Path(tmpdir) / "images").parse(pdf_path)
            images = [
                block
                for chapter in parsed.chapters
                for block in chapter.blocks
                if block.block_type in {BlockType.IMAGE.value, BlockType.FIGURE.value}
            ]
            xrefs = [block.metadata.get("image_xref") for block in images]
            contents = {Path(block.metadata["image_path"]).read_bytes() for block in images}

        self.assertEqual(len(images), 2)
        self.assertEqual(len(set(xrefs)), 2)
        self.assertEqual(len(contents), 2)


class ExportAssetCopyTest(unittest.TestCase):
    def test_changed_source_replaces_a_stale_export_asset(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source, target = root / "source.png", root / "block-1.png"
            target.write_bytes(b"old picture")
            source.write_bytes(b"new picture")
            _copy_asset_if_changed(source, target)
            self.assertEqual(target.read_bytes(), b"new picture")
            _copy_asset_if_changed(source, target)  # identical: left as is
            self.assertEqual(target.read_bytes(), b"new picture")


def _heading(text: str, flags: list[str]) -> MergedRenderBlock:
    return MergedRenderBlock(
        block_id="b1",
        chapter_id="c1",
        block_type="heading",
        render_mode="zh_primary_with_optional_source",
        artifact_kind=None,
        title=None,
        source_text=text,
        target_text=None,
        source_metadata={"recovery_flags": flags, "pdf_page_family": "body"},
        source_sentence_ids=[],
        target_segment_ids=[],
        is_expected_source_only=False,
        notice=None,
    )


class StyledHeadingDemotionTest(unittest.TestCase):
    TITLE = "7. Chart patterns like Triangles, Head & Shoulders, Inverted H&S etc."
    BUNDLE = SimpleNamespace(chapter=SimpleNamespace(title_src="CHAPTER 4: THE TOP 10 SIGNALS OF RSI"))

    def test_font_emphasis_heading_is_not_demoted_by_prose_shape(self) -> None:
        block = _heading(self.TITLE, ["embedded_book_styled_heading_recovered"])
        self.assertFalse(should_demote_book_heading_to_paragraph(self.BUNDLE, 5, block))

    def test_heading_without_font_evidence_keeps_the_prose_shape_rule(self) -> None:
        self.assertTrue(should_demote_book_heading_to_paragraph(self.BUNDLE, 5, _heading(self.TITLE, [])))


if __name__ == "__main__":
    unittest.main()
