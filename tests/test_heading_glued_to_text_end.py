"""A subheading is never the end of the paragraph before it (the Agent book's "...第 3 章详细讨论这一能力。结构化输出").

Three places joined a heading line onto the text above it, and each has its guard here:

- the parser: a line set in the heading face ("basic flow", small caps above its paragraph) kept as
  the last line of the code block before it, or the first line of a list item;
- the export: a heading joined onto the block before it when that block was itself parsed as a
  heading (body text split off a run-in heading used to keep the heading type);
- the QA: both are found, from the parsed blocks behind the reading copy and the book's fonts.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from book_agent.domain.enums import BlockType
from book_agent.domain.quality import pilot_qa
from book_agent.domain.quality.pilot_qa import PilotQaInputs, QaBlock, QaChapter
from book_agent.domain.structure.pdf import _split_heading_face_lines_pass
from book_agent.export.models import MergedRenderBlock
from book_agent.export.render_repair import should_merge_adjacent_heading_render_blocks
from book_agent.ingestion.pdf.models import _RecoveredBlock

BODY = "The E2B flow is straightforward: create a sandbox, execute code, check results, and stop."


def raw_block(page: int, lines: list[tuple[str, float, bool]], fonts: tuple[str, ...] = ("Dem", "Roman")) -> SimpleNamespace:
    return SimpleNamespace(
        page_number=page,
        ruled_table=False,
        line_texts=[text for text, _size, _bold in lines],
        line_styles=tuple((size, bold) for _text, size, bold in lines),
        font_names=frozenset(fonts),
    )


def context(*blocks: SimpleNamespace) -> SimpleNamespace:
    pages: dict[int, list[SimpleNamespace]] = {}
    for block in blocks:
        pages.setdefault(block.page_number, []).append(block)
    return SimpleNamespace(
        font_emphasis_available=True,
        extraction=SimpleNamespace(pages=[SimpleNamespace(page_number=number, blocks=items) for number, items in pages.items()]),
    )


def recovered(text: str, block_type: BlockType = BlockType.PARAGRAPH, page: int = 244) -> _RecoveredBlock:
    role = {BlockType.CODE: "code_like", BlockType.LIST_ITEM: "list_item"}.get(block_type, "body")
    return _RecoveredBlock(
        role=role,
        block_type=block_type,
        text=text,
        page_start=page,
        page_end=page,
        bbox_regions=[],
        reading_order_index=10,
        parse_confidence=0.9,
        flags=[],
        font_size_avg=10.0,
        source_path=f"pdf://page/{page}",
        anchor="p244-b8",
        metadata={"pdf_page_family": "body"},
    )


HEADING_LINE = raw_block(244, [("basic flow", 9.5, True), (BODY, 10.0, False), ("Execution results are returned in an object.", 10.0, False)])


class ParserHeadingFaceLineTests(unittest.TestCase):
    def test_a_heading_line_glued_to_the_end_of_code_is_split_off(self) -> None:
        blocks = _split_heading_face_lines_pass(None, [recovered("uv add e2b-code-interpreter basic flow", BlockType.CODE)], context(HEADING_LINE))
        self.assertEqual([(block.block_type, block.text) for block in blocks], [
            (BlockType.CODE, "uv add e2b-code-interpreter"),
            (BlockType.HEADING, "basic flow"),
        ])
        self.assertEqual(blocks[1].metadata["pdf_heading_recovery_source"], "embedded_book_styled_heading_recovered")

    def test_heading_lines_at_the_start_middle_and_end_of_a_paragraph_are_split_off(self) -> None:
        text = f"Earlier paragraph ends here.\nbasic flow\n{BODY}"
        blocks = _split_heading_face_lines_pass(None, [recovered(text)], context(HEADING_LINE))
        self.assertEqual([block.block_type for block in blocks], [BlockType.PARAGRAPH, BlockType.HEADING, BlockType.PARAGRAPH])
        ending = _split_heading_face_lines_pass(None, [recovered("A finished sentence.\nbasic flow")], context(HEADING_LINE))
        self.assertEqual([(block.block_type, block.text) for block in ending], [
            (BlockType.PARAGRAPH, "A finished sentence."),
            (BlockType.HEADING, "basic flow"),
        ])
        # "step 1: ..." opening a list item: the heading, then the paragraph under it.
        step = raw_block(69, [("step 1: specify tool definitions", 9.5, True), (BODY, 10.0, False), (BODY, 10.0, False)])
        item = recovered(f"step 1: specify tool definitions\n{BODY}", BlockType.LIST_ITEM, page=69)
        self.assertEqual(
            [block.block_type for block in _split_heading_face_lines_pass(None, [item], context(step))],
            [BlockType.HEADING, BlockType.PARAGRAPH],
        )

    def test_bold_text_that_is_not_a_heading_line_stays(self) -> None:
        # A bold term wrapping onto the next line goes on in lowercase: part of its sentence.
        wrapped = raw_block(244, [("basic flow", 9.5, True), (BODY, 10.0, False), (BODY, 10.0, False)])
        text = "• The item begins with\nbasic flow\nand goes on here."
        self.assertEqual(len(_split_heading_face_lines_pass(None, [recovered(text, BlockType.LIST_ITEM)], context(wrapped))), 1)
        # A code annotation: a bold label over a word or two, never a heading line.
        annotation = raw_block(140, [("Upload to", 9.0, True), ("Pinecone", 9.0, False)])
        code = recovered("index.upsert(vectors)\nUpload to", BlockType.CODE, page=140)
        self.assertEqual(len(_split_heading_face_lines_pass(None, [code], context(annotation))), 1)
        # A running header ("19 / Summary") is not split into a page number and a heading.
        summary = raw_block(38, [("Summary", 12.0, True), (BODY, 10.0, False), (BODY, 10.0, False)])
        self.assertEqual(len(_split_heading_face_lines_pass(None, [recovered("19\nSummary", page=38)], context(summary))), 1)


def render_heading(block_id: str, text: str, order: int) -> MergedRenderBlock:
    return MergedRenderBlock(
        block_id=block_id,
        chapter_id="c2",
        block_type="heading",
        render_mode="zh_primary_with_optional_source",
        artifact_kind=None,
        title=None,
        source_text=text,
        target_text="译文",
        source_metadata={"source_page_start": 42, "source_page_end": 42, "reading_order_index": order, "pdf_page_family": "body"},
        source_sentence_ids=[],
        target_segment_ids=[],
        is_expected_source_only=False,
        notice=None,
    )


class ExportHeadingJoinTests(unittest.TestCase):
    def test_a_heading_is_not_joined_onto_a_finished_sentence(self) -> None:
        prose = render_heading("b31", "Self-hosting can be cheaper. Simple tasks go to open LLMs.", 1)
        heading = render_heading("b32", "stability and control", 2)
        self.assertFalse(should_merge_adjacent_heading_render_blocks(prose, heading))

    def test_a_heading_wrapped_over_two_blocks_is_still_joined(self) -> None:
        first = render_heading("h1", "Designing for model agility in a", 1)
        rest = render_heading("h2", "rapidly changing landscape", 2)
        self.assertTrue(should_merge_adjacent_heading_render_blocks(first, rest))


def qa_block(block_id: str, block_type: str, source: str, target: str, **fields) -> QaBlock:
    return QaBlock(
        block_id=block_id,
        chapter_id="c2",
        block_type=block_type,
        render_mode="zh_primary_with_optional_source",
        artifact_kind=None,
        source_text=source,
        target_text=target,
        html="",
        page=42,
        **fields,
    )


def inputs(blocks: list[QaBlock]) -> PilotQaInputs:
    return PilotQaInputs(
        chapters=[QaChapter(id="c2", ordinal=10, title_src="2 The brain of AI agents: LLMs")],
        blocks=blocks,
        sentences=[],
    )


PROSE = "LLM outputs often have to integrate with other systems. The ability to generate structured output is essential."
HEADINGS = [
    qa_block("h1", "heading", "2.1.3 Essential LLM capabilities for agents", "2.1.3 智能体必备的 LLM 核心能力", font_names=("Dem",)),
    qa_block("h2", "heading", "2.2 LLM API basics", "2.2 LLM API 基础", font_names=("Dem",)),
]


class GluedHeadingCheckTests(unittest.TestCase):
    def test_a_heading_the_export_joined_onto_the_text_before_is_found(self) -> None:
        glued = qa_block(
            "b42",
            "paragraph",
            f"{PROSE} long context support",
            "……必不可少。长上下文支持",
            parsed_text=PROSE,
            font_names=("Roman",),
            folded_headings=(("b42", PROSE), ("b43", "long context support")),
        )
        run = pilot_qa.check_heading_glued_to_text_end(inputs([*HEADINGS, glued]))
        self.assertEqual(len(run.findings), 1)
        evidence = run.findings[0].evidence
        self.assertEqual((evidence["cause"], evidence["headings"], evidence["prose_heading_block_ids"]), ("export_join", ["long context support"], ["b42"]))
        # The body text itself, parsed as a heading, is a prose heading even though the export demotes it.
        prose_runs = pilot_qa.check_headings_and_headers(inputs([*HEADINGS, glued]))[0]
        self.assertEqual([finding.target["id"] for finding in prose_runs.findings], ["b42"])

    def test_a_heading_line_kept_at_the_end_of_a_parsed_block_is_found_by_its_face(self) -> None:
        text = f"{PROSE}\nlong context support"
        glued = qa_block("b42", "paragraph", text, "……长上下文支持", parsed_text=text, font_names=("Dem", "Roman"))
        run = pilot_qa.check_heading_glued_to_text_end(inputs([*HEADINGS, glued]))
        self.assertEqual([(finding.evidence["cause"], finding.evidence["headings"]) for finding in run.findings], [("parsed", ["long context support"])])
        self.assertEqual(pilot_qa.trailing_heading_span(text, ("Dem", "Roman"), frozenset({"Dem"})), (len(PROSE) + 1, len(text)))
        # The same shape in body type alone is a wrapped line, not a heading.
        plain = qa_block("b42", "paragraph", text, "……", parsed_text=text, font_names=("Roman",))
        self.assertEqual(pilot_qa.check_heading_glued_to_text_end(inputs([*HEADINGS, plain])).findings, [])

    def test_a_clean_chapter_has_no_glued_heading(self) -> None:
        heading = qa_block("b41", "heading", "structured output", "结构化输出", font_names=("Dem", "Roman"))
        body = qa_block("b42", "paragraph", PROSE, "……必不可少。", parsed_text=PROSE, font_names=("Roman",))
        self.assertEqual(pilot_qa.check_heading_glued_to_text_end(inputs([*HEADINGS, heading, body])).findings, [])


if __name__ == "__main__":
    unittest.main()
