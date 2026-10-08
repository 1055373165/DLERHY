"""Text the book sets in its code face stays code; body text with code identifiers stays prose.

The Agent book printed program output and the tail of a listing in the code face right after the
code ("The output shows a dramatic reduction in token usage:" / "Total tokens: 37312"). The parser
split those lines off as a prose paragraph, so the reader saw English sentences, half translated,
outside the listing. A clip of a listing's box also caught the start of an annotation set beside it
("Move back by the ov"). And a paragraph wrapped around a figure, with identifiers in the code face,
was drawn as code and never translated.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import fitz

from book_agent.domain.enums import BlockType
from book_agent.domain.structure.pdf import PDFParser
from book_agent.export import render_repair
from book_agent.export.models import MergedRenderBlock
from book_agent.ingestion.pdf.extract import _own_lines_of_clip


def _render_block(block_type: str, text: str, metadata: dict[str, object], **changes: object) -> MergedRenderBlock:
    values: dict[str, object] = dict(
        block_id="b1",
        chapter_id="c1",
        block_type=block_type,
        render_mode="zh_primary_with_optional_source",
        artifact_kind=None,
        title=None,
        source_text=text,
        target_text="译文",
        source_metadata=metadata,
        source_sentence_ids=[],
        target_segment_ids=[],
        is_expected_source_only=False,
        notice=None,
    )
    values.update(changes)
    return MergedRenderBlock(**values)  # type: ignore[arg-type]


class CodeFaceParseTest(unittest.TestCase):
    def test_program_output_in_the_code_face_stays_with_the_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "listing.pdf"
            document = fitz.open()
            document.new_page().insert_text((72, 200), "Token Savings", fontname="hebo", fontsize=24)
            page = document.new_page()
            page.insert_text((72, 80), "We compare the token counts of the whole page and of the selected chunks.", fontsize=10)
            listing = (
                "top_chunks = [r['chunk'] for r in results]\n"
                "selected_tokens = len(enc.encode(selected_text))\n"
                'print(f"Total tokens: {total_tokens}")\n'
                "The output shows a dramatic reduction in token usage:\n"
                "Total tokens: 37312\n"
                "Selected tokens: 304"
            )
            page.insert_textbox(fitz.Rect(72, 100, 520, 200), listing, fontname="cour", fontsize=8)
            page.insert_text((72, 230), "By extracting only the three most relevant chunks, we reduced the context.", fontsize=10)
            document.save(pdf_path)
            parsed = PDFParser(image_output_dir=Path(tmpdir) / "images").parse(pdf_path)

        blocks = [block for chapter in parsed.chapters for block in chapter.blocks]
        paragraphs = [block.text for block in blocks if block.block_type == BlockType.PARAGRAPH.value]
        code = [block.text for block in blocks if block.block_type == BlockType.CODE.value]
        self.assertFalse(any("dramatic reduction" in text for text in paragraphs), paragraphs)
        self.assertTrue(any("dramatic reduction" in text and "top_chunks" in text for text in code), code)

    def test_a_clip_keeps_only_the_blocks_own_lines(self) -> None:
        raw = "    while start < len(text):\n        end = start + chunk_size\nMove back by the ov\n        return chunks"
        own = ["while start < len(text):", "end = start + chunk_size", "return chunks"]
        self.assertEqual(
            _own_lines_of_clip(raw, own),
            "    while start < len(text):\n        end = start + chunk_size\n        return chunks",
        )


class CodeFaceExportTest(unittest.TestCase):
    def test_a_code_face_tail_an_older_parse_split_off_is_drawn_as_code(self) -> None:
        metadata = {"pdf_mixed_code_prose_split": "trailing_prose_suffix", "pdf_font_names": ["JetBrainsMonoNL-Regular"]}
        block = _render_block("paragraph", "The output shows a reduction:\nTotal tokens: 37312", metadata)
        self.assertTrue(render_repair.should_promote_book_block_to_code(block))
        body_face = {**metadata, "pdf_font_names": ["JetBrainsMonoNL-Regular", "NewBaskervilleStd-Roman"]}
        self.assertFalse(render_repair.is_code_face_prose_split("paragraph", body_face))

    def test_a_code_block_in_the_code_face_is_not_split_into_prose(self) -> None:
        text = (
            "Wharvton or Algrimand?\n"
            "The file is located at: ./gaia_workspace/sales.xlsx\n"
            '"""\n'
            "result = await agent.run(prompt, verbose=True)\n"
            "print(result.output)"
        )
        block = _render_block(
            "code",
            text,
            {"pdf_font_names": ["JetBrainsMonoNL-Regular"]},
            render_mode="source_artifact_full_width",
            artifact_kind="code",
            target_text=None,
        )
        self.assertEqual(render_repair.split_mixed_book_code_prose_render_block(block), [block])

    def test_body_text_with_code_identifiers_is_prose(self) -> None:
        text = (
            "The key to the Loop implementation is the stop condition (stop_condition). This\n"
            "function receives the current result and iteration count and then returns whether to\n"
            "terminate. We also set max_iterations to prevent infinite loops. As in Sequential, only the\n"
            "first agent receives user_input; all subsequent executions receive only the context."
        )

        class _Block:
            block_type = BlockType.PARAGRAPH
            source_text = text

        metadata = {"pdf_block_role": "body", "pdf_font_names": ["JetBrainsMonoNL-Regular", "NewBaskervilleStd-Roman"]}
        self.assertFalse(render_repair.is_code_like_block(_Block(), metadata))

    def test_a_stale_refresh_fragment_already_parsed_as_a_block_is_not_drawn_twice(self) -> None:
        from types import SimpleNamespace

        from book_agent.services.export import _drop_refresh_fragments_parsed_as_blocks

        output = "[1] Similarity: 0.596\nContent about quantum mechanics..."
        bundle = SimpleNamespace(blocks=[SimpleNamespace(source_text="Query: 'quantum computing'"), SimpleNamespace(source_text=output)])
        fragment = _render_block("paragraph", output, {"recovery_flags": ["export_refresh_split_render_fragment"]}, block_id="b1::refresh-split::0")
        own = _render_block("paragraph", output, {}, block_id="b2")
        other = _render_block("paragraph", "Text only the fragment has.", {"recovery_flags": ["export_refresh_split_render_fragment"]})
        self.assertEqual(_drop_refresh_fragments_parsed_as_blocks(bundle, [fragment, own, other]), [own, other])


if __name__ == "__main__":
    unittest.main()
