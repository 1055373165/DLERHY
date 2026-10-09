"""Emphasis markers (``*em*``, ``**strong**``) travel through translation and render as tags.

The EPUB parser writes ``<em>`` as ``*x*`` and ``<strong>`` as ``**x**`` so the model keeps the
emphasis. The reading copy of "An Illustrated Guide to AI Agents" showed them as asterisks
("其*智能体程序*（agent program）", "*arXiv,* 2404.16244"). Asterisks that are text (``2*3``,
``*args``, a footnote star, code) must stay as they are.
"""

from __future__ import annotations

import html
import unittest

from book_agent.domain.structure import inline_marks
from book_agent.export import markup
from book_agent.export.models import MergedRenderBlock


def _render(text: str) -> str:
    return inline_marks.render_html(text, html.escape)


def _block(
    *,
    source: str,
    target: str | None,
    metadata: dict | None = None,
    block_type: str = "paragraph",
    render_mode: str = "zh_primary_with_optional_source",
    artifact_kind: str | None = None,
) -> MergedRenderBlock:
    return MergedRenderBlock(
        block_id="b1",
        chapter_id="c1",
        block_type=block_type,
        render_mode=render_mode,
        artifact_kind=artifact_kind,
        title=None,
        source_text=source,
        target_text=target,
        source_metadata=metadata or {},
        source_sentence_ids=["s1"],
        target_segment_ids=["t1"],
        is_expected_source_only=False,
        notice=None,
    )


class PairingTest(unittest.TestCase):
    def test_paired_markers_become_tags_next_to_chinese(self) -> None:
        self.assertEqual(_render("其*智能体程序*（agent program）"), "其<em>智能体程序</em>（agent program）")
        self.assertEqual(_render("*理解*多种模态"), "<em>理解</em>多种模态")
        self.assertEqual(_render("a **bold** word and *em* word"), "a <strong>bold</strong> word and <em>em</em> word")
        self.assertEqual(_render("**a *b* c** done"), "<strong>a <em>b</em> c</strong> done")

    def test_the_books_spacing_quirks_around_a_venue(self) -> None:
        # "<em>arXiv, </em>2502.02649" and "<em>arXiv,</em>2404.16244" as the book sets them.
        self.assertEqual(_render("*arXiv, *2502.02649."), "<em>arXiv,</em> 2502.02649.")
        self.assertEqual(_render("*arXiv,*2404.16244."), "<em>arXiv,</em>2404.16244.")
        self.assertEqual(_render("“The Ethics.” *arXiv,* 2404.16244。"), "“The Ethics.” <em>arXiv,</em> 2404.16244。")

    def test_asterisks_that_are_text_stay(self) -> None:
        for text in ("2*3*4 and 2 * 3 * 4", "call f(*args, **kwargs) now", "*args / **kwargs", "Note* the footnote", "a*b*c"):
            with self.subTest(text=text):
                self.assertEqual(_render(text), html.escape(text))

    def test_code_spans_are_never_looked_into(self) -> None:
        self.assertEqual(
            inline_marks.render_html("use `*args` and `**kw` *here*", html.escape),
            "use <code>*args</code> and <code>**kw</code> <em>here</em>",
        )

    def test_plain_text_drops_pairs_and_keeps_text_stars(self) -> None:
        self.assertEqual(inline_marks.plain_text("其*智能体程序*，`x*y` 和 2*3"), "其智能体程序，x*y 和 2*3")


class StrayMarkerTest(unittest.TestCase):
    def test_unpaired_markers_against_chinese_are_strays(self) -> None:
        self.assertEqual(inline_marks.stray_markers("其中的*智能体程序（agent"), ["*"])
        self.assertEqual(inline_marks.stray_markers("注意力**机制"), ["**"])
        self.assertEqual(inline_marks.stray_markers("其*智能体程序*（agent）"), [])
        self.assertEqual(inline_marks.stray_markers("f(*args, **kwargs) 和 2*3"), [])

    def test_an_opening_star_after_a_curly_quote_whose_close_was_lost(self) -> None:
        # The Illustrated Guide's citations: "Needs," *arXiv, *2504" lost its closing star.
        source = "Qian, Cheng et al. 2025. “ToolRL: Reward Is All Tool Learning Needs,” *arXiv, *2504.13958."
        target = "Qian, Cheng 等，2025。“ToolRL: Reward Is All Tool Learning Needs，”*arXiv，2504.13958。"
        self.assertEqual(inline_marks.stray_markers(target), ["*"])
        self.assertEqual(inline_marks.stray_markers(source), [])
        self.assertEqual(
            inline_marks.drop_stray_markers(target, [source]),
            "Qian, Cheng 等，2025。“ToolRL: Reward Is All Tool Learning Needs，”arXiv，2504.13958。",
        )
        self.assertEqual(inline_marks.stray_markers("他说“*你好*”"), [])

    def test_a_translation_is_judged_against_its_source(self) -> None:
        self.assertEqual(inline_marks.unbalanced_against("其*智能体程序（agent）", ["its *agent program*"]), ["*"])
        self.assertEqual(inline_marks.unbalanced_against("其*智能体程序*（agent）", ["its *agent program*"]), [])

    def test_drop_keeps_the_sources_pairs_and_drops_the_rest(self) -> None:
        # The EPUB's own emphasis stays: the reading copy renders it.
        self.assertEqual(inline_marks.drop_stray_markers("其*智能体程序*（agent）", ["its *agent program*"]), "其*智能体程序*（agent）")
        self.assertEqual(inline_marks.drop_stray_markers("**核心**要点", ["**Key** points"]), "**核心**要点")
        # Emphasis a PDF source has no marker of, and a lone star: dropped; code untouched.
        self.assertEqual(
            inline_marks.drop_stray_markers("**核心要点**：其*智能体程序（agent `**x**`", ["Key points: the agent program `**x**`"]),
            "核心要点：其智能体程序（agent `**x**`",
        )


class ReadingCopyTest(unittest.TestCase):
    def test_translation_emphasis_renders_in_the_reading_copy(self) -> None:
        block = _block(source="In practice, its *agent program* is a model.", target="在实践中，其*智能体程序*（agent program）是一个模型。")
        rendered = markup.render_block_html(block, include_source_toggle=False)
        self.assertIn("其<em>智能体程序</em>（agent program）", rendered)
        self.assertNotIn("*", rendered)

    def test_an_epub_source_shown_beside_the_translation_renders_too(self) -> None:
        block = _block(
            source="its *agent program* and `run()`",
            target="其*智能体程序*与 `run()`",
            metadata={"has_inline_formatting": True},
        )
        rendered = markup.render_block_html(block)
        self.assertIn("<div class='source'>its <em>agent program</em> and <code class='inline-token'>run()</code></div>", rendered)

    def test_a_pdf_source_without_markup_keeps_its_text(self) -> None:
        block = _block(source="def f(*args, **kwargs): 2*3", target="函数")
        rendered = markup.render_block_html(block)
        self.assertIn("def f(*args, **kwargs): 2*3", rendered)

    def test_an_untranslated_epub_block_renders_its_markup(self) -> None:
        block = _block(source="1 Russell, *Artificial Intelligence*.", target=None, metadata={"has_inline_formatting": True})
        rendered = markup.render_block_html(block, include_source_toggle=False)
        self.assertIn("<em>Artificial Intelligence</em>", rendered)

    def test_code_blocks_keep_their_stars(self) -> None:
        block = _block(
            source="print(2*3)  # **not** emphasis",
            target=None,
            block_type="code",
            render_mode="source_artifact_full_width",
            artifact_kind="code",
            metadata={"has_inline_formatting": True},
        )
        rendered = markup.render_block_html(block)
        self.assertIn("**not**", rendered)
        self.assertNotIn("<strong>", rendered)


if __name__ == "__main__":
    unittest.main()
