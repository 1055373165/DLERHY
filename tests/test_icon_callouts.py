"""Icon-led callouts: a small icon and the lines set beside it in a non-body face are one block.

Manning sets example prompts this way (a round "QS" icon, then the prompt in a
sans next to the serif body). The parser reads them as one callout block; a
prose callout is translated line by line and rendered as a box with the small
icon, a code callout stays code.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import fitz

from book_agent.domain.enums import BlockType, SentenceStatus
from book_agent.domain.structure.callouts import (
    CALLOUT_ICON_KEY,
    LooseBlock,
    callout_line_is_code,
    looks_like_code_callout,
    pdf_callout_lines,
)
from book_agent.domain.structure.pdf import PDFParser
from book_agent.export import markup
from book_agent.export.models import MergedRenderBlock
from book_agent.services.bootstrap import SegmentationService

BODY = (
    "Agents should act before asking questions. When the user's intent is clear, execute "
    "immediately without asking for permission. If they ask about the weather, search. If a "
    "calculation is necessary, calculate. This principle distinguishes agents from chatbots. "
) * 3


def _icon_png() -> bytes:
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 32, 30), False)
    pixmap.set_rect(pixmap.irect, (60, 150, 90))
    return pixmap.tobytes("png")


def _body(page: fitz.Page, top: float, bottom: float, text: str = BODY) -> None:
    page.insert_textbox(fitz.Rect(102, top, 477, bottom), text, fontsize=10, fontname="tiro")


def _callout(page: fitz.Page, top: float, lines: list[str], *, fontname: str = "helv") -> None:
    """The icon at the left margin, the first line beside it, the others indented under it."""
    page.insert_image(fitz.Rect(102, top, 118, top + 15), stream=_icon_png())
    for index, line in enumerate(lines):
        x = 120 if index == 0 else 126
        page.insert_text((x, top + 11 + index * 14.5), line, fontsize=9.5, fontname=fontname)


def _build(path: Path, lines: list[str], *, callout_font: str = "helv") -> None:
    document = fitz.open()
    for number in range(1, 4):
        page = document.new_page(width=531, height=666)
        page.insert_text((440, 34), str(number), fontsize=9, fontname="tiro")
        if number == 2:
            _body(page, 60, 200)
            _callout(page, 220, lines, fontname=callout_font)
            _body(page, 240 + 14.5 * len(lines), 420)
        else:
            _body(page, 60, 400)
    document.save(path)


def _parse(lines: list[str], **kwargs) -> list:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "book.pdf"
        _build(path, lines, **kwargs)
        parsed = PDFParser(image_output_dir=Path(tmpdir) / "images").parse(path)
    return [block for chapter in parsed.chapters for block in chapter.blocks]


def _page_blocks(blocks: list, page_number: int) -> list:
    return [block for block in blocks if block.metadata.get("source_page_start") == page_number]


class IconCalloutParseTests(unittest.TestCase):
    def test_icon_and_sans_lines_become_one_quote_callout(self) -> None:
        blocks = _page_blocks(
            _parse(
                [
                    "When the user's intent is clear, execute immediately without confirmation.",
                    "Only when intent is unclear, ask minimal questions to clarify.",
                ]
            ),
            2,
        )
        callouts = [block for block in blocks if block.metadata.get("pdf_block_role") == "callout"]
        self.assertEqual(len(callouts), 1, [(b.block_type, b.text[:40]) for b in blocks])
        callout = callouts[0]
        self.assertEqual(callout.block_type, BlockType.QUOTE.value)
        self.assertEqual(
            callout.text,
            "When the user's intent is clear, execute immediately without confirmation.\n"
            "Only when intent is unclear, ask minimal questions to clarify.",
        )
        self.assertEqual(callout.metadata["callout_kind"], "prose")
        icon = callout.metadata[CALLOUT_ICON_KEY]
        self.assertEqual(icon["page_number"], 2)
        self.assertAlmostEqual(icon["bbox"][0], 102, delta=1)
        # The icon is not an image block of its own, and the lines are not loose paragraphs.
        self.assertFalse([block for block in blocks if block.block_type in {"image", "figure"}])
        self.assertFalse([block for block in blocks if "Only when intent" in block.text and block is not callout])
        # The callout sits between the paragraphs around it, in reading order.
        texts = [block.text for block in blocks if block.metadata.get("pdf_block_role") != "header"]
        index = texts.index(callout.text)
        self.assertTrue(texts[index - 1].startswith("Agents should act"))
        self.assertTrue(texts[index + 1].startswith("Agents should act"))

    def test_json_callout_stays_code(self) -> None:
        blocks = _page_blocks(
            # Straight quotes: the base-14 fonts cannot draw typographic ones.
            _parse(["{", '"query": "The user question",', '"answer": "The final answer"', "}"]),
            2,
        )
        callouts = [block for block in blocks if block.metadata.get("pdf_block_role") == "callout"]
        self.assertEqual(len(callouts), 1)
        self.assertEqual(callouts[0].block_type, BlockType.CODE.value)
        self.assertEqual(callouts[0].metadata["callout_kind"], "code")
        self.assertEqual(callouts[0].text.splitlines()[0], "{")

    def test_icon_beside_body_text_is_not_a_callout(self) -> None:
        # Lines in the body face next to a small picture: an inline glyph, the paragraph stays a paragraph.
        blocks = _page_blocks(_parse(["Agents should act before asking questions."], callout_font="tiro"), 2)
        self.assertFalse([block for block in blocks if block.metadata.get("pdf_block_role") == "callout"])
        self.assertTrue([block for block in blocks if block.block_type == "image"])


class PdfCalloutLinesTests(unittest.TestCase):
    """An old parse joined two callout lines into one block; the repair reads them apart from the page."""

    def test_lines_come_back_from_the_page(self) -> None:
        first, second = "Action: search the web for the price", "Response: The price is 42 won."
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "book.pdf"
            _build(path, [first, second])
            joined = LooseBlock("b1", "paragraph", f"{first} {second}", ("Helvetica",), ((2, (110.0, 220.0, 470.0, 250.0)),))
            with fitz.open(path) as pdf:
                self.assertEqual(pdf_callout_lines(pdf, [joined]), [first, second])
                edited = LooseBlock("b1", "paragraph", "Something else entirely", ("Helvetica",), joined.regions)
                self.assertIsNone(pdf_callout_lines(pdf, [edited]))


class CalloutClassificationTests(unittest.TestCase):
    def test_code_callouts(self) -> None:
        self.assertTrue(looks_like_code_callout(["instructions = “””", "You are a helpful assistant."]))
        self.assertTrue(looks_like_code_callout(["{", "“query”: “...”,", "}"]))
        self.assertFalse(looks_like_code_callout(["[System]", "You are a grader.", "Respond PASS or FAIL."]))
        self.assertFalse(looks_like_code_callout(["Example 1:", "User: “What is the price?”"]))

    def test_data_lines(self) -> None:
        self.assertTrue(callout_line_is_code("},"))
        self.assertTrue(callout_line_is_code("{query}"))
        self.assertTrue(callout_line_is_code("“overall_pass”: “PASS” | “FAIL”,"))
        self.assertFalse(callout_line_is_code("User question: {query}"))
        self.assertFalse(callout_line_is_code("Respond with:"))


class CalloutSegmentationTests(unittest.TestCase):
    def test_one_sentence_per_line_and_data_lines_protected(self) -> None:
        block = SimpleNamespace(
            id="block-1",
            block_type=BlockType.QUOTE,
            source_text="You are a grader. Be strict.\nUser question: {query}\n{\n“score”: 1\n}",
            normalized_text="You are a grader. Be strict. User question: {query} { “score”: 1 }",
            source_span_json={"pdf_block_role": "callout", "callout_kind": "prose"},
            parse_revision_id=None,
            canonical_node_id=None,
            source_anchor="p1-callout1",
            parse_confidence=0.9,
        )
        document = SimpleNamespace(id="doc", segmentation_version=1, src_lang="en")
        chapter = SimpleNamespace(id="chapter")
        sentences = SegmentationService()._build_sentences(document, chapter, block, now=None)
        self.assertEqual(
            [sentence.source_text for sentence in sentences],
            ["You are a grader. Be strict.", "User question: {query}", "{", "“score”: 1", "}"],
        )
        self.assertEqual([sentence.translatable for sentence in sentences], [True, True, False, False, False])
        self.assertEqual(sentences[2].sentence_status, SentenceStatus.PROTECTED)
        self.assertEqual(sentences[1].source_span_json["callout_line"], 2)


def _render_block(**overrides) -> MergedRenderBlock:
    values = dict(
        block_id="callout-1",
        chapter_id="chapter",
        block_type="quote",
        render_mode="zh_primary_with_optional_source",
        artifact_kind=None,
        title=None,
        source_text="Use the search tool when you see:\n• Explicit references\n• Time references",
        target_text="在看到以下表达时使用搜索工具：\n• 明确的指代\n• 时间上的指代",
        source_metadata={"pdf_block_role": "callout", "callout_kind": "prose", CALLOUT_ICON_KEY: {"page_number": 1}},
        source_sentence_ids=[],
        target_segment_ids=[],
        is_expected_source_only=False,
        notice=None,
    )
    values.update(overrides)
    return MergedRenderBlock(**values)


class CalloutRenderTests(unittest.TestCase):
    def test_reading_copy_is_a_box_with_the_icon_and_chinese_lines_only(self) -> None:
        html = markup.render_block_html(
            _render_block(),
            {"callout-1": "assets/pdf-images/callout-1-icon.png"},
            include_source_toggle=False,
        )
        self.assertIn("class='block callout callout-prose'", html)
        self.assertIn("<img class='callout-icon' src='assets/pdf-images/callout-1-icon.png'", html)
        self.assertIn("<p class='callout-line'>在看到以下表达时使用搜索工具：</p>", html)
        self.assertIn("<ul class='callout-list'><li>明确的指代</li><li>时间上的指代</li></ul>", html)
        self.assertNotIn("Explicit references", html)
        self.assertNotIn("artifact-figure", html)

    def test_bilingual_edition_keeps_its_source_toggle(self) -> None:
        html = markup.render_block_html(_render_block(), {})
        self.assertIn("<details open><summary>原文</summary>", html)
        self.assertIn("<li>Explicit references</li>", html)
        # Without an icon file a small marker stands in for it.
        self.assertIn("callout-icon-mark", html)

    def test_code_callout_renders_as_code_in_the_box(self) -> None:
        block = _render_block(
            block_type="code",
            render_mode="source_artifact_full_width",
            artifact_kind="code",
            source_text='instructions = """\nYou are a writer.\n"""',
            target_text=None,
            source_metadata={"pdf_block_role": "callout", "callout_kind": "code"},
        )
        html = markup.render_block_html(block, {}, include_source_toggle=False)
        self.assertIn("callout-code", html)
        self.assertIn("<pre><code>", html)
        self.assertIn("You are a writer.", html)
        epub = markup.render_block_rebuilt_epub_xhtml(block, {"callout-1": "assets/pdf-images/callout-1-icon.png"})
        self.assertIn("callout-code", epub)
        self.assertIn("callout-icon", epub)

    def test_markdown_keeps_the_lines(self) -> None:
        self.assertEqual(
            markup.render_block_markdown(_render_block()),
            "> 在看到以下表达时使用搜索工具：  \n> • 明确的指代  \n> • 时间上的指代",
        )

    def test_target_text_is_assembled_line_by_line(self) -> None:
        sentences = [
            SimpleNamespace(id="s1", ordinal_in_block=1, source_text="Use the search tool:", translatable=True),
            SimpleNamespace(id="s2", ordinal_in_block=2, source_text="• Explicit references", translatable=True),
            SimpleNamespace(id="s3", ordinal_in_block=3, source_text="{query}", translatable=False),
        ]
        targets = {"s1": ["t1"], "s2": ["t2"]}
        target_map = {"t1": SimpleNamespace(text_zh="使用搜索工具："), "t2": SimpleNamespace(text_zh="明确的指代")}
        self.assertEqual(
            markup.callout_target_text(sentences, targets, target_map),
            "使用搜索工具：\n• 明确的指代\n{query}",
        )
        self.assertEqual(markup.callout_target_text(sentences, {}, target_map), "")

    def test_lines_the_model_translated_together_stay_chinese(self) -> None:
        sentences = [
            SimpleNamespace(id="s1", ordinal_in_block=1, source_text="When intent is clear, act.", translatable=True),
            SimpleNamespace(id="s2", ordinal_in_block=2, source_text="Only when unclear, ask.", translatable=True),
        ]
        joined = {"s1": ["t1"], "s2": ["t1"]}
        # One translation per line inside the joined segment: the lines come back.
        split = {"t1": SimpleNamespace(text_zh="意图明确时，直接执行。只有不明确时，才提问。")}
        self.assertEqual(markup.callout_target_text(sentences, joined, split), "意图明确时，直接执行。\n只有不明确时，才提问。")
        # Labelled prompt lines ("Action: ..." / "Response: ...") come back at their labels.
        labelled = {"t1": SimpleNamespace(text_zh="行动：[web_search: price] 响应：当前股价为 XX 韩元。")}
        self.assertEqual(
            markup.callout_target_text(sentences, joined, labelled), "行动：[web_search: price]\n响应：当前股价为 XX 韩元。"
        )
        # Otherwise the joined translation stays on the first line; no English line is shown.
        whole = {"t1": SimpleNamespace(text_zh="意图明确时直接执行，不明确时才提问。")}
        self.assertEqual(markup.callout_target_text(sentences, joined, whole), "意图明确时直接执行，不明确时才提问。")


if __name__ == "__main__":
    unittest.main()
