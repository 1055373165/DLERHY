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
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import fitz

from book_agent.domain.enums import BlockType
from book_agent.domain.structure.pdf import PDFParser
from book_agent.export.models import MergedRenderBlock
from book_agent.export.render_repair import (
    should_demote_book_code_block_to_paragraph,
    should_demote_book_heading_to_paragraph,
    should_demote_book_heading_with_prose_target,
)
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



class SectionHeadingTargetTest(unittest.TestCase):
    """The Manning pilots: every "1.2.1" heading was demoted because its translation "had two sentence stops"."""

    BUNDLE = SimpleNamespace(chapter=SimpleNamespace(title_src="1 LLMs and the need for RAG"))

    def _translated(self, source: str, target: str, **metadata: object) -> MergedRenderBlock:
        block = _heading(source, [])
        return replace(block, target_text=target, source_metadata={**block.source_metadata, **metadata})

    def test_the_dots_of_a_section_number_end_no_sentence(self) -> None:
        for source, target in (
            ("1.1.1 LLMs are not trained for facts", "1.1.1 LLM 并非为事实而训练"),
            ("2.1 What does a RAG system look like?", "2.1 RAG 系统长什么样？"),
            ("2.4.1 Why GAIA: Setting goals for GPT-4.1 agents", "2.4.1 为什么选择 GAIA：为 GPT-4.1 智能体设定目标"),
        ):
            with self.subTest(source=source):
                self.assertFalse(should_demote_book_heading_with_prose_target(self.BUNDLE, self._translated(source, target)))

    def test_a_heading_translated_into_prose_is_still_demoted(self) -> None:
        block = self._translated("How it works", "它是这样工作的。模型先读上下文。然后调用工具。")
        self.assertTrue(should_demote_book_heading_with_prose_target(self.BUNDLE, block))

    def test_a_heading_made_by_a_structure_edit_keeps_its_type(self) -> None:
        # A run-in heading the repair split off reads like a phrase ("how the agent loop works").
        block = _heading("core definition: autonomous control flow", [])
        self.assertTrue(should_demote_book_heading_to_paragraph(self.BUNDLE, 5, block))
        edited = replace(block, source_metadata={**block.source_metadata, "structure_edit_previous_type": "paragraph"})
        self.assertFalse(should_demote_book_heading_to_paragraph(self.BUNDLE, 5, edited))



class CodeFaceSampleTest(unittest.TestCase):
    """The Agent book's GAIA sample: a dict of long English strings, printed as code."""

    SAMPLE = (
        "{\n   'task_id': 'e1fc63a2-da7a-432f-be78-7c4a95598703',\n"
        "   'Question': 'If Eliud Kipchoge could maintain his record-making marathon \npace '\n"
        "               'indefinitely, how many thousand hours would it take him to \nrun '\n"
        "               'the distance between the Earth and the Moon at its closest \napproach? '\n"
        "               'Please use the minimum perigee value on the Wikipedia page \nfor the Moon '\n"
        "               'when carrying out your calculation. Round your result to the \nnearest '\n"
        "               '1000 hours and do not use any comma separators if \nnecessary.',\n"
        "   'Level': 1,\n   'Final answer': '17',\n}"
    )

    def _code(self, fonts: list[str]) -> MergedRenderBlock:
        return MergedRenderBlock(
            block_id="b1",
            chapter_id="c1",
            block_type="code",
            render_mode="source_artifact_full_width",
            artifact_kind="code",
            title=None,
            source_text=self.SAMPLE,
            target_text=None,
            source_metadata={"pdf_font_names": fonts, "pdf_page_family": "body"},
            source_sentence_ids=[],
            target_segment_ids=[],
            is_expected_source_only=True,
            notice=None,
        )

    def test_a_block_set_wholly_in_the_code_face_stays_code(self) -> None:
        # By its words alone it reads as prose; the code face says otherwise.
        self.assertTrue(should_demote_book_code_block_to_paragraph(self._code([])))
        self.assertFalse(should_demote_book_code_block_to_paragraph(self._code(["JetBrainsMonoNL-Regular"])))


if __name__ == "__main__":
    unittest.main()
