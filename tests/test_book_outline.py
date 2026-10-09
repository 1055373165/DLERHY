"""The reading copy's outline against the book's own: run-in titles under the numbered sections are
bold text, not headings, and the QA compares the reading copy's headings with the PDF's bookmarks."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from book_agent.domain.quality import pilot_qa
from book_agent.domain.quality.pilot_qa import QaChapter
from book_agent.domain.structure import book_outline
from book_agent.domain.structure.book_outline import BookOutlineEntry
from book_agent.domain.structure.parse_health import OutlineEntry
from book_agent.export import markup, render_repair
from book_agent.export.models import MergedRenderBlock
from book_agent.services.export import _demote_headings_the_book_does_not_list
from tests.test_pilot_qa_rules import C1, C2, build, rendered

OUTLINE = [
    {"level": 2, "title": "1 What is an AI agent?", "page_number": 22},
    {"level": 3, "title": "1.2\tUnderstanding LLM agents", "page_number": 24},
    {"level": 4, "title": "1.2.1\tWhat is an LLM?", "page_number": 24},
    {"level": 4, "title": "1.2.2\tWhat is an LLM agent?", "page_number": 25},
    {"level": 4, "title": "8.5.3\tImplementation", "page_number": 264},
]


def _block(block_id: str, text: str, level: int | None, *, block_type: str = "heading", page: int = 25) -> MergedRenderBlock:
    metadata: dict = {"source_page_start": page}
    if level is not None:
        metadata["heading_level"] = level
    return MergedRenderBlock(
        block_id=block_id,
        chapter_id="c1",
        block_type=block_type,
        render_mode="zh_primary_with_optional_source",
        artifact_kind=None,
        title=None,
        source_text=text,
        target_text=f"ZH::{text}",
        source_metadata=metadata,
        source_sentence_ids=[],
        target_segment_ids=[],
        is_expected_source_only=False,
        notice=None,
    )


class RunInHeadingRuleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.outline = book_outline.outline_index(
            book_outline.outline_entries({"pdf_page_evidence": {"pdf_outline_entries": OUTLINE}})
        )

    def _run_in(self, text: str, level: int | None, *, numbered: bool = True, page: int | None = 25) -> bool:
        return book_outline.is_run_in_heading(text, level, numbered_chapter=numbered, outline=self.outline, page=page)

    def test_an_unlisted_title_under_the_numbered_sections_is_a_run_in(self) -> None:
        self.assertTrue(self._run_in("core definition: autonomous control flow", 3))
        self.assertTrue(self._run_in("chain", 3))
        # Numbered, listed, or a chapter's own section label ("Summary" at the x.y level): headings.
        self.assertFalse(self._run_in("1.2.2\nWhat is an LLM agent?", 3))
        self.assertFalse(self._run_in("What is an LLM?", 3))
        self.assertFalse(self._run_in("Summary", 2))
        # A book that does not number its sections: its headings are its structure.
        self.assertFalse(self._run_in("chain", 3, numbered=False))

    def test_a_listed_title_counts_only_near_its_page(self) -> None:
        # "8.5.3 Implementation" is on page 264; "implementation" in chapter 6 is a run-in.
        self.assertTrue(self._run_in("implementation", 4, page=179))
        self.assertFalse(self._run_in("implementation", 4, page=265))

    def test_prose_and_lead_in_sentences(self) -> None:
        self.assertTrue(book_outline.reads_as_prose("In practice, LLM-optimized search services such as Tavily implement similar func-"))
        self.assertFalse(book_outline.reads_as_prose("elements that realize autonomy: llm + tool + loop"))
        self.assertTrue(book_outline.is_lead_in_sentence("This hybrid approach offers several advantages:"))
        self.assertFalse(book_outline.is_lead_in_sentence("Workflow: Developer-defined flow"))


class ExportDemotionTest(unittest.TestCase):
    def _demote(self, blocks: list[MergedRenderBlock]) -> list[MergedRenderBlock]:
        bundle = SimpleNamespace(document=SimpleNamespace(metadata_json={"pdf_page_evidence": {"pdf_outline_entries": OUTLINE}}))
        return _demote_headings_the_book_does_not_list(bundle, blocks)

    def test_only_the_books_sections_stay_headings(self) -> None:
        blocks = self._demote(
            [
                _block("h", "1.2.2\nWhat is an LLM agent?", 3),
                _block("run-in", "core definition: autonomous control flow", 3),
                _block("lead-in", "This hybrid approach offers several advantages:", None),
                _block("prose", "In practice, LLM-optimized search services such as Tavily implement similar func-", 3),
                _block("summary", "Summary", 2),
            ]
        )
        by_id = {block.block_id: block for block in blocks}
        self.assertEqual(by_id["h"].block_type, "heading")
        self.assertEqual(by_id["summary"].block_type, "heading")
        self.assertEqual(by_id["run-in"].block_type, "paragraph")
        self.assertTrue(by_id["run-in"].source_metadata["run_in_heading"])
        for block_id in ("lead-in", "prose"):
            self.assertEqual(by_id[block_id].block_type, "paragraph")
            self.assertNotIn("run_in_heading", by_id[block_id].source_metadata)
        # The run-in title reads in bold, in its own paragraph.
        self.assertEqual(
            markup.render_block_markdown(by_id["run-in"]), "**ZH::core definition: autonomous control flow**"
        )
        self.assertIn("run-in-heading", markup.render_block_html(by_id["run-in"]))
        self.assertFalse(render_repair.should_merge_adjacent_book_paragraph_fragments(by_id["prose"], by_id["run-in"]))

    def test_a_chapter_without_numbered_subsections_keeps_its_headings(self) -> None:
        blocks = self._demote([_block("h", "1.2\nUnderstanding LLM agents", 2), _block("x", "chain", 3)])
        self.assertEqual([block.block_type for block in blocks], ["heading", "heading"])


class SectionsVsOutlineCheckTest(unittest.TestCase):
    OUTLINE = [
        OutlineEntry(1, "1 Why agents", 20),
        OutlineEntry(2, "1.1 The loop", 21),
        OutlineEntry(3, "1.1.1 One step", 21),
        OutlineEntry(2, "1.2 Tools", 22),
        OutlineEntry(1, "2 The loop", 31),
    ]

    def _findings(self, *extra) -> list[dict]:
        blocks = [
            rendered("b-h1", C1, "heading", "1 Why agents", "1 为什么需要智能体", page=20),
            rendered("b-s1", C1, "heading", "1.1 The loop", "1.1 循环", page=21),
            rendered("b-s2", C1, "heading", "1.1.1 One step", "1.1.1 一步", page=21),
            *extra,
            rendered("b-h2", C2, "heading", "2 The loop", "2 循环", page=31),
        ]
        inputs = build(
            blocks,
            outline=self.OUTLINE,
            chapters=[
                QaChapter(C1, 10, "1 Why agents", "1 为什么需要智能体", 20, 30),
                QaChapter(C2, 11, "2 The loop", "2 循环", 31, 40),
            ],
        )
        return [f for f in pilot_qa.check_sections_vs_outline(inputs).findings]

    def test_a_listed_section_without_a_heading_is_missing(self) -> None:
        [finding] = self._findings()
        self.assertEqual(finding.evidence["detail"], "missing")
        self.assertEqual(finding.severity, pilot_qa.MAJOR)
        self.assertIn("1.2 Tools", finding.message)

    def test_a_run_in_title_in_the_outline_is_reported_and_a_bold_paragraph_is_not(self) -> None:
        tools = rendered("b-s3", C1, "heading", "1.2 Tools", "1.2 工具", page=22)
        run_in = rendered("b-r", C1, "heading", "chain", "链", page=22)
        run_in = pilot_qa.QaBlock(**{**{f: getattr(run_in, f) for f in run_in.__slots__}, "heading_level": 3})
        extra = rendered("b-x", C1, "heading", "1.3 Bonus", "1.3 附加", page=23)
        found = {f.evidence["detail"]: f for f in self._findings(tools, run_in, extra)}
        self.assertEqual(set(found), {"unlisted", "extra"})
        self.assertEqual(found["unlisted"].severity, pilot_qa.MAJOR)
        self.assertIn("加粗小标题", found["unlisted"].message)
        self.assertEqual(found["extra"].severity, pilot_qa.MINOR)
        # Set as a bold paragraph, the run-in title is not in the outline: nothing to report.
        bold = rendered("b-r", C1, "paragraph", "chain", "链", page=22, metadata={"run_in_heading": True})
        self.assertEqual(self._findings(tools, bold), [])

    def test_a_lower_case_run_in_title_is_not_half_a_sentence(self) -> None:
        bullet = rendered("b-li", C1, "list_item", "• LLM call error rate", "• LLM 调用错误率", page=22)
        traces = rendered("b-r", C1, "paragraph", "traces", "追踪", page=22, metadata={"run_in_heading": True})
        after = rendered("b-p", C1, "paragraph", "tiktoken is a tokenizer library.", "tiktoken 是一个分词库。", page=22)
        intro = rendered("b-r2", C1, "paragraph", "introducing tiktoken", "介绍 tiktoken", page=22, metadata={"run_in_heading": True})
        inputs = build([rendered("b-h1", C1, "heading", "1 Why agents", "1 为什么需要智能体", page=20), bullet, traces, intro, after])
        self.assertNotIn("L1.paragraph_split_midsentence", {f["check_id"] for layer in pilot_qa.run_layers(inputs) for f in layer["findings"]})


if __name__ == "__main__":
    unittest.main()
