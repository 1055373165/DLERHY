"""Parse health checks (domain/structure/parse_health.py), each on a small input showing the problem it stands for.

The problems are the ones two Manning PDFs had before the fixes on fix/pdf-structure(-2) and
fix/figure-crops: contents lines turned into chapters, chapters under "Part N" folded into the part,
a "References" chapter holding chapters 1–2, front matter merged into "acknowledgements", symbol-font
bullets decoded as "¡", code comments taken for headings, words broken by a line-end hyphen, figure
crops reaching into the running header, hairline image slivers, body text folded into a figure, and
a figure's labels kept a second time as body text.
"""

from __future__ import annotations

import tempfile
import unittest
from collections import Counter
from pathlib import Path

import fitz

from book_agent.domain.structure import parse_health as ph
from book_agent.domain.structure.parse_health import HealthBlock, HealthChapter, OutlineEntry
from book_agent.ingestion.pdf.figure_extent import figure_region_problems


def block(
    block_id: str,
    text: str,
    *,
    block_type: str = "paragraph",
    chapter: str = "c1",
    page: int = 1,
    bbox: tuple[float, float, float, float] | None = None,
    ordinal: int = 0,
    **metadata: object,
) -> HealthBlock:
    span: dict[str, object] = {"source_page_start": page, "reading_order_index": ordinal, **metadata}
    if bbox is not None:
        span["source_bbox_json"] = {"regions": [{"page_number": page, "bbox": list(bbox)}]}
    return HealthBlock(id=block_id, chapter_id=chapter, ordinal=ordinal, block_type=block_type, source_text=text, source_span_json=span)


def chapter(ordinal: int, title: str, start: int, end: int | None = None) -> HealthChapter:
    return HealthChapter(id=f"c{ordinal}", ordinal=ordinal, title=title, page_start=start, page_end=end or start)


PARTED_OUTLINE = [
    OutlineEntry(1, "A Simple Guide", 1),
    OutlineEntry(1, "contents", 7),
    OutlineEntry(1, "preface", 11),
    OutlineEntry(1, "about this book", 15),
    OutlineEntry(2, "Who should read this book?", 15),
    OutlineEntry(1, "Part 1 Foundations", 21),
    OutlineEntry(2, "1 LLMs and the need for RAG ", 23),
    OutlineEntry(2, "2 RAG systems and their design", 37),
    OutlineEntry(1, "Part 2 Creating RAG systems", 51),
    OutlineEntry(2, "3 Indexing pipeline", 53),
    OutlineEntry(1, "index", 249),
    OutlineEntry(1, "Back Cover", 258),
]

GOOD_CHAPTERS = [
    chapter(1, "Front Matter", 1, 6),
    chapter(2, "contents", 7, 10),
    chapter(3, "preface", 11, 14),
    chapter(4, "about this book", 15, 20),
    chapter(5, "Part 1 Foundations", 21, 22),
    chapter(6, "1 LLMs and the need for RAG", 23, 36),
    chapter(7, "2 RAG systems and their design", 37, 50),
    chapter(8, "Part 2 Creating RAG systems", 51, 52),
    chapter(9, "3 Indexing pipeline", 53, 248),
    chapter(10, "index", 249, 258),
]


class OutlineChecksTest(unittest.TestCase):
    def test_a_book_split_like_its_outline_passes(self) -> None:
        result = ph.check_chapters_vs_outline(GOOD_CHAPTERS, PARTED_OUTLINE, "A Simple Guide")
        self.assertEqual((result.count, result.passed), (0, True))
        self.assertEqual(result.denominator, 6)  # parts, numbered chapters and the index; not the cover or title
        front = ph.check_front_matter_sections(GOOD_CHAPTERS, PARTED_OUTLINE, "A Simple Guide")
        self.assertEqual((front.count, front.denominator, front.passed), (0, 3, True))

    def test_contents_lines_as_chapters_and_chapters_folded_into_parts_fail(self) -> None:
        # The RAG book before the fixes: contents lines became chapters, the chapters of a part became one.
        chapters = [
            chapter(1, "Front Matter", 1, 6),
            chapter(2, "contents", 7, 7),
            chapter(3, "2 RAG systems and their design 17", 8, 8),
            chapter(4, "Part 2 Creating RAG systems.......31", 8, 9),
            chapter(5, "preface", 11, 14),
            chapter(6, "about this book", 15, 20),
            chapter(7, "Part 1 Foundations", 21, 24),
            chapter(8, "References", 25, 50),
            chapter(9, "Creating RAG systems", 51, 248),
            chapter(10, "index", 249, 258),
        ]
        result = ph.check_chapters_vs_outline(chapters, PARTED_OUTLINE, "A Simple Guide")
        self.assertFalse(result.passed)
        self.assertEqual(result.breakdown, {"missing_chapter": 3, "extra_chapter": 3})
        self.assertEqual(result.samples[0].detail, "extra_chapter")
        self.assertEqual({sample.page for sample in result.samples if sample.detail == "extra_chapter"}, {8, 25})

    def test_front_matter_folded_into_a_neighbour_fails(self) -> None:
        # The agent book before the fixes: "about this book" ended up inside the preface.
        chapters = [chapter for chapter in GOOD_CHAPTERS if chapter.title != "about this book"]
        result = ph.check_front_matter_sections(chapters, PARTED_OUTLINE, "A Simple Guide")
        self.assertEqual((result.count, result.passed), (1, False))
        self.assertEqual((result.samples[0].text, result.samples[0].page), ("about this book", 15))
        self.assertEqual(result.severity, ph.MAJOR)

    def test_without_an_outline_the_outline_checks_do_not_apply(self) -> None:
        self.assertIsNone(ph.check_chapters_vs_outline(GOOD_CHAPTERS, [], None))
        self.assertIsNone(ph.check_front_matter_sections(GOOD_CHAPTERS, [], None))


class ChapterTitleCheckTest(unittest.TestCase):
    def test_page_numbers_in_titles(self) -> None:
        for title, number in [
            ("2 RAG systems and their design 17", "17"),
            ("Part 2 Creating RAG systems................................31", "31"),
            ("Part 2 Advanced topics 8", "8"),
        ]:
            self.assertEqual(ph.title_page_number(title), number, title)
        for title in ("Chapter 2", "Appendix B", "Part 3", "GPT 4", "Step 1", "1 LLMs and the need for RAG"):
            self.assertIsNone(ph.title_page_number(title), title)

    def test_references_chapter_holding_body_chapters_fails(self) -> None:
        chapters = [chapter(1, "References", 25, 51), chapter(2, "index", 249, 258), chapter(3, "contents", 7, 10)]
        blocks = [
            block("h1", "1 LLMs and the need for RAG", block_type="heading", chapter="c1", page=25),
            block("p1", "Lewis, P. et al. Retrieval-augmented generation. https://arxiv.org/abs/2005.11401", chapter="c1"),
            block("i1", "agents, 12, 45", chapter="c2", page=250),
            block("t1", "1 LLMs and the need for RAG 3", block_type="heading", chapter="c3", page=7),
        ]
        result = ph.check_chapter_titles(chapters, blocks)
        self.assertEqual(result.breakdown, {"body_under_back_matter_title": 1})
        self.assertEqual(result.samples[0].chapter_id, "c1")
        self.assertIn("1 LLMs", result.samples[0].text)

    def test_many_chapters_under_one_title_fail(self) -> None:
        # "An Illustrated Guide to AI Agents": every chapter was titled by its last section, "Summary".
        chapters = [chapter(index, "Summary", index * 20, index * 20 + 19) for index in range(1, 5)] + [chapter(5, "Afterword", 120, 121)]
        result = ph.check_chapter_titles(chapters, [])
        self.assertFalse(result.passed)
        self.assertEqual(result.breakdown, {"repeated_title": 4})
        self.assertTrue(ph.check_chapter_titles(chapters[:2] + chapters[4:], []).passed)

    def test_long_prose_under_a_back_matter_title_fails(self) -> None:
        prose = " ".join(["Retrieval gives a language model facts it never saw during training."] * 4)
        blocks = [block(f"p{index}", prose, chapter="c1") for index in range(20)]
        result = ph.check_chapter_titles([chapter(1, "Index", 240, 258)], blocks)
        self.assertFalse(result.passed)
        self.assertTrue(ph.check_chapter_titles([chapter(1, "index", 240, 258)], blocks[:19]).passed)


class TextChecksTest(unittest.TestCase):
    def test_stray_glyphs(self) -> None:
        blocks = [
            block("b1", "¡ RAG gives a model facts it was never trained on.", block_type="list_item"),
            block("b2", "The model returned � instead of a quote."),
            block("b3", "An icon  from a private-use font."),
            block("b4", "A clean paragraph. ¡Hola! inside is fine."),
            block("b5", "¡ header", role="header", pdf_block_role="header"),
        ]
        result = ph.check_stray_glyphs(blocks)
        self.assertEqual(
            result.breakdown, {"symbol_font_bullet": 1, "replacement_character": 1, "private_use_character": 1}
        )
        self.assertEqual(result.denominator, 4)  # page furniture is not text
        self.assertFalse(result.passed)
        many_clean = [block(f"c{index}", "Clean text.") for index in range(1000)]
        self.assertTrue(ph.check_stray_glyphs([*many_clean, blocks[0]]).passed)  # 1 in 1,001 is within 0.2%

    def test_pseudo_headings(self) -> None:
        blocks = [
            block("h1", "#Installing bs4 package", block_type="heading"),
            block("h2", "Loading the extracted information", block_type="heading", pdf_font_names=["CourierNewPS-BoldMT"]),
            block("h3", "3.3 Data conversion", block_type="heading", pdf_font_names=["BodegaSans-Medium"]),
            block("h4", "Summary", block_type="heading"),
        ]
        result = ph.check_pseudo_headings(blocks)
        self.assertEqual(result.breakdown, {"hash_prefix": 1, "monospace_font": 1})
        self.assertEqual(result.denominator, 4)
        self.assertFalse(result.passed)

    def test_line_break_hyphens_use_the_pdf_lines_as_evidence(self) -> None:
        inline = Counter({"provider": 3, "fault-tolerant": 2, "fault": 2, "tolerant": 2, "overflow": 1})
        blocks = [
            block("b1", "The pro-vider answers, and a fault-tolerant design matters."),
            block("b2", "It is a decision- making core, used for pre- and post-training."),
            block("b3", "The tour­nament ended."),
            block("b4", "An evalua tion strategy; another evalua tion; the evaluation matters. We can not stop."),
        ]
        result = ph.check_line_break_hyphens(blocks, inline)
        self.assertEqual(result.breakdown, {"broken_word": 1, "hyphen_space": 1, "soft_hyphen": 1, "split_word": 2})
        texts = [sample.text for sample in result.samples]
        self.assertIn("pro-vider", texts)
        self.assertIn("decision- making", texts)
        self.assertFalse(any("fault" in text or "pre-" in text or "can not" in text for text in texts))

    def test_line_break_hyphens_without_the_pdf_use_the_parsed_text(self) -> None:
        blocks = [
            block("b1", "Every provider is a pro-vider of answers."),
            block("b2", "A state-of-the-art, fault-tolerant design."),
        ]
        result = ph.check_line_break_hyphens(blocks)
        self.assertEqual(result.breakdown, {"broken_word": 1})

    def test_a_few_hyphens_in_a_long_book_pass(self) -> None:
        filler = [block(f"f{index}", "word " * 100) for index in range(300)]  # 30,000 words
        broken = [block(f"b{index}", "a pro-vider and a provider") for index in range(6)]
        self.assertTrue(ph.check_line_break_hyphens([*filler, *broken]).passed)  # 6 ≤ max(5, 0.0002 × 30,024)
        many = [block(f"m{index}", "a pro-vider and a provider") for index in range(20)]
        self.assertFalse(ph.check_line_break_hyphens([*filler, *many]).passed)


class PictureChecksTest(unittest.TestCase):
    def test_image_fragments(self) -> None:
        blocks = [
            block("i1", "", block_type="image", bbox=(105.8, 549.2, 271.5, 552.8)),  # 3.6pt hairline
            block("i2", "", block_type="image", bbox=(0.0, 781.3, -3988.9, 666.0)),  # inverted, off the page
            block("i3", "", block_type="image", bbox=(600.0, 10.0, 650.0, 60.0)),  # right of the page
            block("i4", "", block_type="image", bbox=(102.8, 479.4, 118.4, 494.6)),  # a 15pt icon is a picture
            block("f1", "[Figure]", block_type="figure", bbox=(60.0, 100.0, 500.0, 320.0)),
            block("p1", "Body text.", bbox=(60.0, 330.0, 500.0, 360.0)),
        ]
        result = ph.check_image_fragments(blocks, {1: (531.0, 666.0)})
        self.assertEqual(result.breakdown, {"sliver": 1, "empty": 2})
        self.assertEqual(result.denominator, 5)
        self.assertFalse(result.passed)

    def test_duplicated_labels(self) -> None:
        blocks = [
            block("f1", "[Figure]", block_type="figure", bbox=(99.0, 285.8, 432.9, 570.3)),
            block("l1", "Retriever User Question", bbox=(134.4, 338.5, 309.7, 356.5)),
            block("l2", "The information relevant to the input question is fetched.", bbox=(254.5, 371.6, 327.3, 396.8)),
            block("p1", "Body text below the figure.", bbox=(102.0, 600.0, 470.0, 640.0)),
            block("c1", "Figure 2.3 The pipelines", block_type="caption", bbox=(102.0, 400.0, 468.0, 420.0)),
            block("h1", "21 What does a RAG system look like?", pdf_block_role="header", bbox=(208.0, 290.0, 473.0, 300.0)),
            block("l3", "A label on another page", page=2, bbox=(134.4, 338.5, 309.7, 356.5)),
        ]
        result = ph.check_duplicated_labels(blocks)
        self.assertEqual(result.count, 2)
        self.assertEqual({sample.block_id for sample in result.samples}, {"l1", "l2"})
        self.assertEqual(result.breakdown, {"pages": 1})
        self.assertTrue(result.passed)  # two label blocks are within the 3 tolerated

    def test_text_over_a_page_background_is_not_a_duplicated_label(self) -> None:
        # The build-an-AI-agent back cover: a tinted panel printed from the top edge to the bottom edge.
        blocks = [
            block("panel", "[Image]", block_type="image", bbox=(315.0, 0.0, 531.0, 666.0)),
            block("t1", "About the author Jungjun Hur is an independent AI engineer.", bbox=(327.0, 332.0, 503.0, 470.0)),
            block("f1", "[Figure]", block_type="figure", bbox=(99.0, 285.0, 300.0, 570.0)),
            block("l1", "Retriever", bbox=(134.0, 338.0, 200.0, 356.0)),
        ]
        result = ph.check_duplicated_labels(blocks, {1: (531.0, 666.0)})
        self.assertEqual({sample.block_id for sample in result.samples}, {"l1"})
        self.assertTrue(ph.is_page_background((315.0, 0.0, 531.0, 666.0), (531.0, 666.0)))
        self.assertTrue(ph.is_page_background((0.0, 0.0, 531.0, 666.0), (531.0, 666.0)))  # a cover
        self.assertFalse(ph.is_page_background((99.0, 285.0, 300.0, 570.0), (531.0, 666.0)))
        self.assertFalse(ph.is_page_background((315.0, 0.0, 531.0, 666.0), None))

    def test_caption_folds(self) -> None:
        caption = "Figure 1.2 ChatGPT (GPT 3.5) response to the question."
        body = (
            "ChatGPT does not have any memory of the 2023 Cricket World Cup. It tells you to check other sources. "
            "This is not ideal. Look at the response in figure 1.3."
        )
        blocks = [
            block("fig", "[Figure]", block_type="figure", page=25, ordinal=1, pdf_block_role="figure",
                  linked_caption_block_id="cap", artifact_group_context_block_ids=["body"]),
            block("cap", caption, block_type="caption", page=25, ordinal=2, pdf_block_role="caption", caption_for_block_id="fig"),
            block("body", body, page=25, ordinal=3, pdf_block_role="body"),
            block("tab", "[Table]", block_type="table", page=26, ordinal=4, pdf_block_role="table_like",
                  linked_caption_block_id="tcap", artifact_group_context_block_ids=["note"]),
            block("tcap", "Table 1.1 Costs", block_type="caption", page=26, ordinal=5, pdf_block_role="caption",
                  caption_for_block_id="tab"),
            block("note", "Table 1.1 lists the cost per call.", page=26, ordinal=6, pdf_block_role="body"),
        ]
        result = ph.check_caption_folds(blocks)
        self.assertEqual((result.count, result.denominator), (1, 2))
        self.assertEqual(result.samples[0].block_id, "body")
        self.assertFalse(result.passed)


def _figure_page_pdf(path: Path) -> None:
    document = fitz.open()
    cover = document.new_page(width=531, height=666)
    cover.insert_text((200, 640), "M A N N I N G", fontsize=9)
    page = document.new_page(width=531, height=666)
    page.insert_text((200, 30), "Retrieval and generation", fontsize=9)  # running header
    page.insert_text((470, 30), "63", fontsize=9)  # folio
    page.draw_rect(fitz.Rect(120, 80, 250, 140), color=(0, 0, 0))
    page.draw_rect(fitz.Rect(300, 80, 430, 140), color=(0, 0, 0))
    page.insert_text((130, 110), "Retriever", fontsize=8)
    page.insert_text((310, 110), "Generator", fontsize=8)
    page.insert_text((102, 160), "Figure 4.4 Two boxes.", fontsize=8)
    page.insert_textbox(fitz.Rect(102, 200, 477, 260), "Body text of the chapter, a whole paragraph long. " * 3, fontsize=10)
    page.insert_text((102, 300), "Figure 4.5 A figure the parser found no region for.", fontsize=8)
    document.save(path)


class FigureRegionCheckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        path = Path(cls._tmp.name) / "figures.pdf"
        _figure_page_pdf(path)
        cls.pdf = fitz.open(path)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.pdf.close()
        cls._tmp.cleanup()

    def _find(self, page: int, bbox, furniture):
        return figure_region_problems(self.pdf[page - 1], bbox, furniture_bboxes=furniture)

    def _blocks(self, figure_bbox) -> list[HealthBlock]:
        return [
            block("cover", "[Figure]", block_type="figure", page=1, bbox=(0.0, 0.0, 531.0, 666.0)),
            block("hdr", "Retrieval and generation", page=2, pdf_block_role="header", bbox=(200.0, 21.0, 330.0, 33.0)),
            block("folio", "63", page=2, pdf_block_role="footer", bbox=(470.0, 21.0, 482.0, 33.0)),
            block("fig", "[Figure]", block_type="figure", page=2, bbox=figure_bbox, linked_caption_block_id="cap"),
            block("cap", "Figure 4.4 Two boxes.", block_type="caption", page=2, bbox=(102.0, 152.0, 200.0, 162.0),
                  caption_for_block_id="fig"),
            block("icon", "", block_type="image", page=2, bbox=(80.0, 200.0, 95.0, 215.0)),
        ]

    def test_a_region_holding_its_figure_and_nothing_else_passes(self) -> None:
        result = ph.check_figure_regions(self._blocks((118.0, 78.0, 432.0, 142.0)), self._find, {1: (531, 666), 2: (531, 666)})
        self.assertEqual((result.count, result.denominator, result.passed), (0, 1, True))  # cover and icon not checked

    def test_a_region_reaching_into_the_running_header_fails(self) -> None:
        result = ph.check_figure_regions(self._blocks((118.0, 14.0, 432.0, 142.0)), self._find, {2: (531, 666)})
        self.assertEqual(result.count, 1)
        self.assertIn("page_furniture", result.breakdown)
        self.assertEqual((result.samples[0].page, result.samples[0].block_id), (2, "fig"))

    def test_a_region_cutting_its_figure_or_holding_its_caption_fails(self) -> None:
        cut = ph.check_figure_regions(self._blocks((118.0, 78.0, 350.0, 142.0)), self._find, {2: (531, 666)})
        self.assertIn("cut_path", cut.breakdown)
        swallowed = ph.check_figure_regions(self._blocks((118.0, 78.0, 432.0, 170.0)), self._find, {2: (531, 666)})
        self.assertIn("caption", swallowed.breakdown)

    def test_a_figure_caption_without_a_figure_is_a_figure_without_a_region(self) -> None:
        blocks = [
            *self._blocks((118.0, 78.0, 432.0, 142.0)),
            block("orphan", "Figure 4.5 A figure the parser found no region for.", block_type="caption", page=2),
            block("tcap", "Table 4.1 Not a figure", block_type="caption", page=2),
        ]
        result = ph.check_figure_regions(blocks, self._find, {2: (531, 666)})
        self.assertEqual(result.breakdown, {"no_region": 1})
        self.assertEqual(result.samples[0].block_id, "orphan")


class ReportTest(unittest.TestCase):
    def test_status_is_the_worst_failed_severity(self) -> None:
        passed = ph.CheckResult("stray_glyphs", ph.BLOCKING, 0, 10, 0, True)
        major = ph.CheckResult("pseudo_headings", ph.MAJOR, 4, 10, 0, False)
        blocking = ph.CheckResult("figure_regions", ph.BLOCKING, 1, 10, 0, False)
        self.assertEqual(ph.summarize([passed])["status"], "passed")
        self.assertEqual(ph.summarize([passed, major])["status"], "warnings")
        self.assertEqual(ph.summarize([passed, major, blocking]), {"status": "blocking", "failed": {"blocking": 1, "major": 1, "minor": 0}})

    def test_an_epub_gets_only_the_checks_that_apply(self) -> None:
        checks = ph.run_checks(is_pdf=False, chapters=[chapter(1, "Chapter One", 0)], blocks=[block("b1", "Text.")])
        names = {check.name for check in checks}
        self.assertEqual(names, {"chapter_titles", "stray_glyphs", "pseudo_headings", "caption_folds"})
        self.assertFalse(names & ph.PDF_ONLY_CHECKS)

    def test_every_check_has_a_severity_and_a_threshold(self) -> None:
        self.assertEqual(set(ph.THRESHOLDS), set(ph.SEVERITIES))
        for name, threshold in ph.THRESHOLDS.items():
            if threshold.max_count or threshold.max_ratio:
                self.assertTrue(threshold.note, f"{name}: a tolerance needs a reason")


if __name__ == "__main__":
    unittest.main()
