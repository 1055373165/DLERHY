"""The page region of a PDF figure: everything of the figure, nothing else.

Regressions from a Manning book whose exported figure crops were wrong:

* A vector figure's region was its large shapes padded by 40pt: it reached
  up into the running header and page number, and stopped above the rows of
  small token boxes the padding did not reach.
* A figure drawn only with small shapes (bars, rounded boxes) and a panel
  below the figure-size threshold got no region at all; the export cropped
  around its caption instead and clipped the top row and the right label.
* A figure's label near the top of the page ("Step 1" on every pipeline
  diagram) repeats like a running header and was taken for one.
* The export snapped a region to a layout image block and trimmed text at
  its edges, and replaced a picture-plus-labels region with the bare picture.

``figure_region_problems`` is the check that would have caught these: a
figure region must not intersect the page furniture or the caption, and
every path and text line crossing or touching its edge must lie inside it.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import fitz

from book_agent.domain.enums import BlockType
from book_agent.domain.structure.pdf import PDFParser
from book_agent.export import pdf_crop
from book_agent.ingestion.pdf.figure_extent import (
    figure_label_blocks,
    figure_region_problems,
    page_text_lines,
    split_extended_drawings,
    vector_figure_extents,
)

ORANGE = (0.95, 0.42, 0.3)
GREY = (0.9, 0.9, 0.9)
BODY = (
    "Body text of the chapter runs across the full width of the text column, "
    "line after line, the way a book paragraph does, so that the parser has "
    "prose to tell apart from the short labels inside a figure drawing."
)


def _solid_png(color: tuple[int, int, int]) -> bytes:
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 30), False)
    pixmap.set_rect(pixmap.irect, color)
    return pixmap.tobytes("png")


def _page_furniture(page: fitz.Page, number: int) -> None:
    page.insert_text((200, 34), "Curse of the models and the idea", fontsize=9)
    page.insert_text((470, 34), str(number), fontsize=9)


def _body(page: fitz.Page, top: float, bottom: float) -> None:
    page.insert_textbox(fitz.Rect(102, top, 477, bottom), BODY, fontsize=10)


def _token_figure_page(page: fitz.Page) -> None:
    """Figure 1.4's layout: big shapes on top, rows of small boxes below."""
    page.draw_rect(fitz.Rect(258, 54, 312, 88), color=(0, 0, 0), dashes="[3] 0")
    page.insert_text((264, 66), "The teacher", fontsize=7)
    page.insert_text((334, 76), "Sample", fontsize=9)
    page.draw_rect(fitz.Rect(116, 90, 447, 96), color=None, fill=(0, 0, 0))  # bracket
    page.draw_circle(fitz.Point(185, 202), 25, color=None, fill=ORANGE)
    page.draw_circle(fitz.Point(383, 202), 25, color=None, fill=ORANGE)
    for row_top in (132, 147, 254):
        for left in (102, 131, 170, 207, 237):
            page.draw_rect(fitz.Rect(left, row_top, left + 27, row_top + 12), color=None, fill=ORANGE)
    page.insert_text((133, 120), "Causal Language Modeling", fontsize=8)
    page.insert_text((140, 240), "Objective: Predict next token", fontsize=7)
    page.draw_line(fitz.Point(281, 144), fitz.Point(281, 302), color=GREY)  # divider between halves
    page.draw_line(fitz.Point(102, 284.7), fitz.Point(144, 284.7), color=ORANGE)  # context arrow
    page.insert_text((151, 297), "Unidirectional context", fontsize=7)
    page.insert_text((102, 320), "Figure 1.4 Two token prediction techniques.", fontsize=8)


def _bar_figure_page(page: fitz.Page) -> None:
    """Figure 1.5's layout: no shape is figure-sized; a label beside the bars."""
    page.draw_rect(fitz.Rect(175, 374, 248, 440), color=None, fill=GREY)
    for index, width in enumerate((12, 22, 40, 32, 9)):
        top = 374 + index * 22
        page.draw_rect(fitz.Rect(252, top, 272, top + 15), color=ORANGE)  # probability
        page.draw_rect(fitz.Rect(275, top, 362, top + 15), color=ORANGE)  # word
        page.draw_rect(fitz.Rect(372, top, 372 + width, top + 20), color=None, fill=ORANGE)
    page.insert_text((257, 386), "0.02", fontsize=7)
    page.insert_text((306, 405), "applauds", fontsize=7)
    page.draw_line(fitz.Point(424, 427), fitz.Point(460, 427))
    page.insert_text((428, 441), "Selected", fontsize=9)
    page.insert_text((428, 451), "word", fontsize=9)
    page.insert_text((102, 502), "Figure 1.5 Probability distribution of words.", fontsize=8)


def _build_book(path: Path) -> None:
    document = fitz.open()
    for number in range(1, 5):
        page = document.new_page(width=531, height=666)
        _page_furniture(page, number)
        if number == 2:
            _token_figure_page(page)
            _bar_figure_page(page)
            _body(page, 530, 610)
        else:
            _body(page, 60, 300)
    document.save(path)


def _figure_blocks(parsed) -> list:
    return [
        block
        for chapter in parsed.chapters
        for block in chapter.blocks
        if block.block_type in {BlockType.IMAGE.value, BlockType.FIGURE.value}
    ]


def _region(block) -> tuple[int, list[float]]:
    region = block.metadata["source_bbox_json"]["regions"][0]
    return region["page_number"], [float(value) for value in region["bbox"]]


def _furniture(parsed, page_number: int) -> list[tuple[float, ...]]:
    return [
        tuple(region["bbox"])
        for chapter in parsed.chapters
        for block in chapter.blocks
        if block.metadata.get("pdf_block_role") in {"header", "footer"}
        for region in block.metadata["source_bbox_json"]["regions"]
        if region["page_number"] == page_number
    ]


class ParsedFigureRegionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.pdf_path = Path(cls._tmp.name) / "book.pdf"
        _build_book(cls.pdf_path)
        cls.parsed = PDFParser(image_output_dir=Path(cls._tmp.name) / "images").parse(cls.pdf_path)
        cls.document = fitz.open(cls.pdf_path)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.document.close()
        cls._tmp.cleanup()

    def _figures_on_page_two(self) -> list[list[float]]:
        regions = [_region(block) for block in _figure_blocks(self.parsed)]
        return sorted((bbox for page, bbox in regions if page == 2), key=lambda bbox: bbox[1])

    def test_each_figure_gets_one_region(self) -> None:
        self.assertEqual(len(self._figures_on_page_two()), 2)

    def test_regions_pass_the_figure_region_check(self) -> None:
        page = self.document[1]
        furniture = _furniture(self.parsed, 2)
        self.assertTrue(furniture, "running header and folio are recognised as page furniture")
        for bbox in self._figures_on_page_two():
            with self.subTest(bbox=bbox):
                self.assertEqual(figure_region_problems(page, bbox, furniture_bboxes=furniture), [])

    def test_token_figure_region_stops_below_the_header_and_reaches_the_last_row(self) -> None:
        token_region = self._figures_on_page_two()[0]
        self.assertGreater(token_region[1], 40.0)  # header band ends at ~36
        self.assertGreaterEqual(token_region[3], 299.0)  # "Unidirectional context" label
        self.assertLess(token_region[3], 310.0)  # caption starts at ~312

    def test_bar_figure_region_holds_its_side_label(self) -> None:
        bar_region = self._figures_on_page_two()[1]
        self.assertLessEqual(bar_region[1], 374.0)
        self.assertGreaterEqual(bar_region[2], 460.0)  # "Selected word" and its arrow
        self.assertLess(bar_region[3], 494.0)  # caption

    def test_labels_inside_the_figure_are_not_body_text(self) -> None:
        texts = [block.text for chapter in self.parsed.chapters for block in chapter.blocks]
        self.assertFalse(any("Selected" in text for text in texts))
        self.assertFalse(any("Causal Language Modeling" in text for text in texts))

    def test_export_renders_the_parser_region_as_is(self) -> None:
        page = self.document[1]
        for block in _figure_blocks(self.parsed):
            page_number, bbox = _region(block)
            if page_number != 2:
                continue
            render_block = SimpleNamespace(
                block_type=block.block_type, render_mode=None, source_metadata=block.metadata
            )
            kind, _page, spec_bbox = pdf_crop.pdf_asset_crop_spec(render_block)  # type: ignore[arg-type]
            self.assertEqual(kind, pdf_crop.FIGURE_REGION_CONTENT_EXTENT)
            self.assertEqual(pdf_crop.resolve_pdf_crop_bbox(page, kind, spec_bbox), bbox)


class FigureRegionCheckTests(unittest.TestCase):
    """The check flags each way a crop can be wrong."""

    def setUp(self) -> None:
        self.document = fitz.open()
        self.page = self.document.new_page(width=531, height=666)
        _page_furniture(self.page, 7)
        _token_figure_page(self.page)
        self.furniture = [line.bbox for line in page_text_lines(self.page.get_text("dict"), 531, 666) if line.margin]

    def tearDown(self) -> None:
        self.document.close()

    def _kinds(self, region: list[float]) -> set[str]:
        return {problem.kind for problem in figure_region_problems(self.page, region, furniture_bboxes=self.furniture)}

    def test_clean_region_has_no_problem(self) -> None:
        self.assertEqual(self._kinds([99.0, 51.0, 468.0, 303.0]), set())

    def test_region_reaching_into_the_running_header(self) -> None:
        self.assertIn("page_furniture", self._kinds([73.0, 14.0, 490.0, 303.0]))

    def test_region_cutting_through_a_row_of_boxes(self) -> None:
        self.assertIn("cut_path", self._kinds([73.0, 40.0, 490.0, 135.5]))

    def test_region_cutting_off_a_label(self) -> None:
        self.assertIn("cut_text", self._kinds([99.0, 51.0, 200.0, 303.0]))

    def test_region_holding_the_caption(self) -> None:
        self.assertIn("caption", self._kinds([99.0, 51.0, 468.0, 330.0]))


class FigureExtentUnitTests(unittest.TestCase):
    def test_clipped_part_of_a_path_is_not_part_of_the_figure(self) -> None:
        document = fitz.open()
        page = document.new_page(width=531, height=666)
        page.draw_rect(fitz.Rect(10, 600, 12, 602), color=None, fill=(0, 0, 0))
        # A 100pt-tall strip clipped to its top 20pt (PDF space: y up).
        stream = b"\nq 100 546 200 20 re W n 0 0 1 rg 100 466 200 100 re f Q\n"
        xref = page.get_contents()[0]
        document.update_stream(xref, document.xref_stream(xref) + stream)
        paths, visible = split_extended_drawings(page.get_drawings(extended=True))
        self.assertEqual(len(paths), 2)
        self.assertAlmostEqual(paths[1]["rect"].y1, 200.0, delta=0.5)
        self.assertIsNotNone(visible[1])
        self.assertAlmostEqual(visible[1][3], 120.0, delta=0.5)
        document.close()

    def test_code_annotation_arrows_are_not_a_figure(self) -> None:
        # A listing's annotation: one long L-shaped connector, arrow heads.
        paths = [(358.0, 305.0, 453.0, 337.0), (421.0, 264.0, 453.0, 296.0), (350.0, 210.0, 350.0, 249.0)]
        paths += [(417.0, 293.0, 423.0, 298.0), (354.0, 334.0, 360.0, 339.0)]
        extents = vector_figure_extents(
            paths,
            seed=lambda bbox: (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) >= 1800,
            figure_sized=lambda bbox: (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) >= 12000,
            solid=[False, False, False, True, True],
            page_width=531,
            page_height=666,
        )
        self.assertEqual(extents, [])

    def test_inline_icon_does_not_make_the_text_beside_it_a_figure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "icon.pdf"
            document = fitz.open()
            page = document.new_page(width=531, height=666)
            page.insert_image(fitz.Rect(93, 100, 106, 112), stream=_solid_png((30, 30, 200)))
            page.insert_text((110, 110), "Use the conversation search tool when needed.", fontsize=10)
            page.insert_text((110, 124), "Example 1:", fontsize=10)
            document.save(pdf_path)
            parsed = PDFParser(image_output_dir=Path(tmpdir) / "images").parse(pdf_path)
        figures = [block for block in _figure_blocks(parsed) if block.block_type == BlockType.FIGURE.value]
        self.assertEqual(figures, [])
        texts = " ".join(block.text for chapter in parsed.chapters for block in chapter.blocks)
        self.assertIn("Example 1:", texts)

    def test_picture_with_axis_labels_is_cropped_not_copied(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "chart.pdf"
            document = fitz.open()
            page = document.new_page(width=531, height=666)
            _body(page, 60, 200)
            page.insert_image(
                fitz.Rect(57, 248, 461, 355), stream=_solid_png((200, 200, 240)), keep_proportion=False
            )
            page.insert_text((63, 362), "November 2022", fontsize=7)
            page.insert_text((384, 362), "November 2024", fontsize=7)
            page.draw_rect(fitz.Rect(141, 375, 166, 377), color=None, fill=(0, 0, 1))
            page.insert_text((169, 378), "Generative AI", fontsize=7)
            page.insert_text((57, 398), "Figure 1.1 Search interest over time.", fontsize=8)
            document.save(pdf_path)
            parsed = PDFParser(image_output_dir=Path(tmpdir) / "images").parse(pdf_path)
            figures = _figure_blocks(parsed)
            self.assertEqual(len(figures), 1)
            figure = figures[0]
            _page, bbox = _region(figure)
            self.assertGreaterEqual(bbox[3], 378.0)  # legend
            self.assertLess(bbox[3], 390.0)  # caption
            self.assertNotIn("image_path", figure.metadata)
            with fitz.open(pdf_path) as reopened:
                page = reopened[0]
                self.assertEqual(figure_region_problems(page, bbox, furniture_bboxes=[]), [])
                asset = pdf_crop.probe_pdf_original_asset(reopened, page, fitz.Rect(*bbox), content_extent=True)
                self.assertNotIn("image_bytes", asset)


class NonWebImageFormatTests(unittest.TestCase):
    """How Large Language Models Work embeds its figures as JPEG 2000 ("jpx"): the reading copy kept
    them as .jpx files no browser shows, and the export failed writing a crop under that name
    ("Image format jpx not in ('png', ...)")."""

    def test_a_jpeg_2000_original_is_written_as_png(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "jpx.pdf"
            document = fitz.open()
            page = document.new_page(width=531, height=666)
            page.insert_image(fitz.Rect(100, 100, 400, 300), stream=_solid_png((200, 200, 240)), keep_proportion=False)
            document.save(pdf_path)

            class Jpx:
                """The document, reporting its image as JPEG 2000 the way such a PDF does."""

                def __init__(self, inner) -> None:
                    self.inner = inner

                def __getattr__(self, name):
                    return getattr(self.inner, name)

                def extract_image(self, xref):
                    return {**self.inner.extract_image(xref), "ext": "jpx"}

            with fitz.open(pdf_path) as reopened:
                asset = pdf_crop.probe_pdf_original_asset(Jpx(reopened), reopened[0], fitz.Rect(100, 100, 400, 300))
        self.assertEqual(asset["extension"], ".png")
        self.assertTrue(asset["image_bytes"].startswith(b"\x89PNG"))

    def test_a_recorded_non_web_suffix_is_written_as_png(self) -> None:
        recorded = SimpleNamespace(storage_path="document-images/d/b.jpx", metadata_json={"image_ext": "jpx"})
        self.assertEqual(pdf_crop.document_image_asset_suffix(recorded, default_ext=".png"), ".png")
        kept = SimpleNamespace(storage_path="document-images/d/b.jpeg", metadata_json={})
        self.assertEqual(pdf_crop.document_image_asset_suffix(kept, default_ext=".png"), ".jpeg")


class RepeatedFigureLabelTests(unittest.TestCase):
    def test_label_repeated_at_the_top_of_figures_is_not_a_running_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "steps.pdf"
            document = fitz.open()
            for number in range(1, 4):
                page = document.new_page(width=531, height=666)
                _page_furniture(page, number)
                page.draw_rect(fitz.Rect(100, 52, 440, 280), color=(0, 0, 0))
                page.draw_rect(fitz.Rect(130, 70, 250, 120), color=None, fill=ORANGE)
                page.insert_text((135, 64), "Step 1", fontsize=7)
                page.insert_text((102, 300), f"Figure 2.{number} A pipeline step by step.", fontsize=8)
                _body(page, 330, 450)
            document.save(pdf_path)
            parsed = PDFParser(image_output_dir=Path(tmpdir) / "images").parse(pdf_path)
        headers = [
            block.text
            for chapter in parsed.chapters
            for block in chapter.blocks
            if block.metadata.get("pdf_block_role") == "header"
        ]
        self.assertTrue(headers)
        self.assertFalse(any("Step 1" in text for text in headers))


def _parse(pdf_path: Path, tmpdir: str):
    return PDFParser(image_output_dir=Path(tmpdir) / "images").parse(pdf_path)


def _text_blocks(parsed) -> list:
    return [
        block
        for chapter in parsed.chapters
        for block in chapter.blocks
        if block.block_type not in {BlockType.IMAGE.value, BlockType.FIGURE.value}
    ]


class PageBackgroundTests(unittest.TestCase):
    """A panel printed to two opposite page edges is the page's background, not a figure.

    The build-an-AI-agent back cover: the right half is a tinted picture from the top edge to
    the bottom edge with the cover copy printed on it. Taken as a figure, its lines of copy were
    labels to fold into the picture.
    """

    def test_panel_to_the_page_edges_is_not_a_figure_piece(self) -> None:
        extents = vector_figure_extents(
            [(315.0, 0.0, 531.0, 666.0)],
            seed=lambda _bbox: True,
            figure_sized=lambda _bbox: True,
            page_width=531,
            page_height=666,
        )
        self.assertEqual(extents, [])

    def test_text_printed_on_a_background_picture_stays_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "cover.pdf"
            document = fitz.open()
            page = document.new_page(width=531, height=666)
            page.insert_image(fitz.Rect(315, 0, 531, 666), stream=_solid_png((60, 80, 90)), keep_proportion=False)
            page.insert_text((360, 120), "About the reader", fontsize=12)
            page.insert_text((335, 140), "For Python developers", fontsize=11)
            _body(page, 60, 300)
            document.save(pdf_path)
            parsed = _parse(pdf_path, tmpdir)
        texts = " ".join(block.text for block in _text_blocks(parsed))
        self.assertIn("About the reader", texts)
        self.assertIn("For Python developers", texts)
        panels = [block for block in _figure_blocks(parsed)]
        self.assertTrue(panels)
        self.assertTrue(all(block.metadata.get("pdf_page_background") for block in panels))


def _labelled_figure_page(page: fitz.Page) -> None:
    """Figure 1.1's layout: body text in a column beside a two-panel diagram whose left panel
    holds a long, sentence-like run of small labels."""
    _body(page, 60, 150)
    page.insert_textbox(fitz.Rect(102, 300, 300, 470), BODY + " " + BODY, fontsize=10)
    page.draw_rect(fitz.Rect(310, 300, 390, 420), color=(0, 0, 0))
    page.draw_rect(fitz.Rect(397, 300, 477, 420), color=(0, 0, 0))
    page.insert_text((315, 310), "Few-shot learning", fontsize=7)
    page.insert_text((402, 310), "Zero-shot learning", fontsize=7)
    page.insert_textbox(
        fitz.Rect(313, 318, 388, 415),
        "Translate English to Korean. Apple becomes sagwa and orange becomes orenji in the examples. "
        "Banana is the new word. The answer is banana in Korean, found from the two examples above.",
        fontsize=6,
    )
    page.insert_text((402, 330), "Translate English to Korean.", fontsize=6)
    page.insert_text((310, 440), "Figure 1.1 Example of a language model's generalization", fontsize=8)


class ChapterOpenerPanelTests(unittest.TestCase):
    def test_a_this_chapter_covers_list_on_a_tint_is_text_not_a_figure(self) -> None:
        """How Large Language Models Work: the "This chapter covers" box (a light borderless tint, a
        small sans list with wrapped items, no line wide enough to be prose) became a picture of
        English text in seven of nine chapters."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "opener.pdf"
            document = fitz.open()
            page = document.new_page(width=531, height=666)
            page.insert_text((164, 150), "1 Big picture: What are LLMs?", fontsize=20)
            page.draw_rect(fitz.Rect(68, 273, 318, 413), color=None, fill=(0.97, 0.958, 0.913))
            page.insert_text((88, 290), "This chapter covers", fontsize=11)
            lines = ["What Generative Pretrained Transformers", "and large language models are", "How LLMs work in plain language",
                     "How humans and machines represent", "languages differently", "Why tools like ChatGPT perform so well"]
            for index, line in enumerate(lines):
                if not line[0].islower():
                    page.draw_rect(fitz.Rect(88, 300 + index * 16, 91, 303 + index * 16), color=None, fill=(0.2, 0.3, 0.5))
                page.insert_text((100, 306 + index * 16), line, fontsize=9)
            _body(page, 440, 600)
            document.save(pdf_path)
            parsed = PDFParser(image_output_dir=Path(tmpdir) / "images").parse(pdf_path)
        self.assertEqual(_figure_blocks(parsed), [])
        texts = " ".join(block.text for chapter in parsed.chapters for block in chapter.blocks)
        self.assertIn("This chapter covers", texts)
        self.assertIn("How LLMs work in plain language", texts)

    def test_a_drawing_with_scattered_labels_on_a_tint_stays_a_figure(self) -> None:
        from types import SimpleNamespace

        from book_agent.ingestion.pdf.extract import _is_text_panel_background

        tint = {"fill": (0.97, 0.96, 0.91), "color": None, "items": [("re",)]}
        label = lambda x, y: SimpleNamespace(bbox=(x, y, x + 40, y + 8), prose=False)
        scattered = [label(80, 280), label(200, 300), label(140, 340), label(250, 380)]
        column = [label(100, 300 + 16 * index) for index in range(3)]
        self.assertFalse(_is_text_panel_background(tint, (68.0, 273.0, 318.0, 413.0), 531.0, scattered))
        self.assertTrue(_is_text_panel_background(tint, (68.0, 273.0, 318.0, 413.0), 531.0, column))


class MeasuredLabelTests(unittest.TestCase):
    """Labels the extractor measured with a drawing are the figure's, however long or sentence-like.

    Real books: 263 (RAG) and 49 (Agent) such label blocks stayed translatable paragraphs, because
    clustering re-judged them by length (> 200 characters), sentence shape and prose density.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        pdf_path = Path(cls._tmp.name) / "labels.pdf"
        document = fitz.open()
        _labelled_figure_page(document.new_page(width=531, height=666))
        document.save(pdf_path)
        cls.parsed = _parse(pdf_path, cls._tmp.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_labels_are_not_body_text(self) -> None:
        texts = " ".join(block.text for block in _text_blocks(self.parsed))
        for label in ("Few-shot learning", "Zero-shot learning", "Banana is the new word", "Translate English"):
            self.assertNotIn(label, texts)

    def test_body_text_beside_the_figure_stays_body_text(self) -> None:
        texts = " ".join(" ".join(block.text.split()) for block in _text_blocks(self.parsed))
        self.assertGreaterEqual(texts.count("Body text of the chapter runs across the full width"), 3)
        self.assertIn("Figure 1.1 Example", texts)
        figures = [_region(block)[1] for block in _figure_blocks(self.parsed)]
        self.assertEqual(len(figures), 1)
        self.assertGreater(figures[0][0], 300.0)  # the text column ends at 300

    def test_a_block_is_labels_only_when_all_its_lines_are(self) -> None:
        document = fitz.open()
        page = document.new_page(width=531, height=666)
        _labelled_figure_page(page)
        lines = page_text_lines(page.get_text("dict"), 531, 666)
        paths, visible = split_extended_drawings(page.get_drawings(extended=True))
        extents = vector_figure_extents(
            [box for box in visible if box is not None],
            seed=lambda bbox: (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) >= 1800,
            figure_sized=lambda bbox: (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) >= 12000,
            text_lines=lines,
            page_width=531,
            page_height=666,
        )
        self.assertEqual(len(extents), 1)
        label_box = (313.0, 318.0, 388.0, 415.0)
        column_box = (102.0, 300.0, 300.0, 470.0)
        both = (102.0, 300.0, 388.0, 470.0)
        self.assertEqual(figure_label_blocks([label_box, column_box, both], lines, extents), [True, False, False])
        document.close()

    def test_labels_and_body_text_are_not_joined_into_one_paragraph(self) -> None:
        from book_agent.domain.structure.pdf import PdfStructureRecoveryService
        from book_agent.ingestion.pdf.models import PdfPage, _RecoveredBlock

        def recovered(text, role, block_type, bbox, label):
            region = {"page_number": 1, "bbox": list(bbox), **({"figure_label": True} if label else {})}
            return _RecoveredBlock(
                role=role, block_type=block_type, text=text, page_start=1, page_end=1, bbox_regions=[region],
                reading_order_index=0, parse_confidence=0.9, flags=[], font_size_avg=8.5,
                source_path="pdf://page/1", anchor="a",
            )

        service = PdfStructureRecoveryService()
        pages = [PdfPage(page_number=1, width=531.0, height=666.0, blocks=[])]
        body = recovered("there are disadvantages to this pattern as well.", "body", BlockType.PARAGRAPH, (93, 203, 279, 297), False)
        plain = recovered("• 3 web searches • Result analysis", "list_item", BlockType.LIST_ITEM, (345, 176, 401, 202), False)
        labels = recovered("• 3 web searches • Result analysis", "list_item", BlockType.LIST_ITEM, (345, 176, 401, 202), True)
        self.assertEqual(service._merge_target_index([plain], body, pages), 0)
        self.assertIsNone(service._merge_target_index([labels], body, pages))


def _text_dict_line(text: str, bbox: tuple[float, float, float, float], size: float) -> dict:
    return {"bbox": bbox, "spans": [{"text": text, "size": size}]}


class MidBlockCaptionTests(unittest.TestCase):
    """A caption set beside a figure can share a text block with the figure's last label.

    Agent p254: "No tools, optimized prompt for writing" (a label) and "Figure 9.2 Four specialist
    agents ..." (the side caption) came out as one block; the caption was not seen as one, the
    figure region took it in, and the block stayed a paragraph inside the figure.
    """

    LINES = [
        ("No tools, optimized", (93.0, 547.0, 173.0, 557.0), 8.3),
        ("prompt for writing", (93.0, 557.0, 160.0, 567.0), 8.3),
        ("Figure 9.2 Four specialist", (333.0, 566.0, 450.0, 575.0), 8.0),
        ("agents for technical blog writing.", (333.0, 576.0, 440.0, 585.0), 8.0),
    ]

    def test_caption_lines_start_at_the_caption(self) -> None:
        text_dict = {"blocks": [{"type": 0, "lines": [_text_dict_line(*line) for line in self.LINES]}]}
        lines = page_text_lines(text_dict, 531, 666)
        self.assertEqual([line.caption for line in lines], [False, False, True, True])

    def test_a_reference_in_running_text_does_not_split_a_block(self) -> None:
        text_dict = {
            "blocks": [
                {
                    "type": 0,
                    "lines": [
                        _text_dict_line("the generation suffers from hallucination and", (102, 100, 477, 112), 10.0),
                        _text_dict_line("Figure 6.2 summarizes these drawbacks.", (102, 113, 300, 125), 10.0),
                    ],
                }
            ]
        }
        self.assertEqual([line.caption for line in page_text_lines(text_dict, 531, 666)], [False, False])

    def test_extracted_block_is_split_at_the_caption(self) -> None:
        from book_agent.ingestion.pdf.extract import _split_mid_block_captions
        from book_agent.ingestion.pdf.models import PdfTextBlock

        block = PdfTextBlock(
            page_number=1,
            block_number=3,
            text=" ".join(text for text, _bbox, _size in self.LINES),
            bbox=(93.0, 547.0, 450.0, 585.0),
            line_texts=[text for text, _bbox, _size in self.LINES],
            span_count=4,
            line_count=4,
            font_size_min=8.0,
            font_size_max=8.3,
            font_size_avg=8.15,
            line_styles=tuple((size, False) for _text, _bbox, size in self.LINES),
        )
        parts = _split_mid_block_captions([block], {id(block): [bbox for _text, bbox, _size in self.LINES]})
        self.assertEqual([part.text for part in parts], ["No tools, optimized\nprompt for writing", "Figure 9.2 Four specialist\nagents for technical blog writing."])
        self.assertEqual(parts[0].bbox, (93.0, 547.0, 173.0, 567.0))
        self.assertEqual(parts[1].bbox, (333.0, 566.0, 450.0, 585.0))
        self.assertEqual(parts[1].font_size_avg, 8.0)

    def test_parsed_caption_is_its_own_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "side.pdf"
            document = fitz.open()
            page = document.new_page(width=531, height=666)
            _body(page, 60, 200)
            page.insert_text(
                (102, 400),
                "No tools, optimized\nprompt for writing\nFigure 9.2 Four specialist agents\nfor technical blog writing.",
                fontsize=8,
            )
            document.save(pdf_path)
            parsed = _parse(pdf_path, tmpdir)
        texts = [" ".join(block.text.split()) for block in _text_blocks(parsed)]
        caption = next(text for text in texts if "Four specialist" in text)
        self.assertTrue(caption.startswith("Figure 9.2"), texts)
        self.assertNotIn("No tools", caption)


def _arrow(page: fitz.Page, start: tuple[float, float], end: tuple[float, float]) -> None:
    """A hairline connector with a small filled head, the way the books draw their arrows."""
    page.draw_line(fitz.Point(*start), fitz.Point(*end), color=(0, 0, 0), width=0.5)
    x, y = end
    page.draw_polyline([fitz.Point(x - 5, y - 1.5), fitz.Point(x, y), fitz.Point(x - 5, y + 1.5)], color=None, fill=(0, 0, 0))


def _hairline_figure(page: fitz.Page, *, caption: str | None, side_caption: bool = False) -> None:
    """Figure 5.5's layout: labels joined by hairline arrows, a few tiny boxes; no large shape."""
    _body(page, 60, 150)
    page.insert_text((102, 196), "sofa is comfortable", fontsize=7)
    page.insert_text((110, 256), "couch is large", fontsize=7)
    _arrow(page, (170, 193), (196, 193))
    _arrow(page, (170, 253), (196, 253))
    for row in (186, 246):
        for left in (200, 218, 236, 254):
            page.draw_rect(fitz.Rect(left, row, left + 16, row + 12), color=(0, 0, 0), width=0.5)
    page.draw_line(fitz.Point(272, 192), fitz.Point(290, 192), color=(0, 0, 0), width=0.5)
    page.draw_line(fitz.Point(272, 252), fitz.Point(290, 252), color=(0, 0, 0), width=0.5)
    page.draw_line(fitz.Point(290, 192), fitz.Point(290, 252), color=(0, 0, 0), width=0.5)
    page.insert_text((296, 225), "Relevant", fontsize=7)
    if caption and side_caption:
        page.insert_text((350, 256), caption, fontsize=8)
    elif caption:
        page.insert_text((102, 280), caption, fontsize=8)
    _body(page, 300, 400)


class CaptionedDrawingTests(unittest.TestCase):
    """A diagram drawn only in hairlines (or small icons) is a figure when a caption says so.

    Real books: Agent Figures 5.4, 5.5 and 7.2 and RAG Figures 3.4, 3.8, 4.4 and 6.2 had no shape
    large enough to count as a figure; they got no region, or a region made up from the text
    blocks above the caption that took in the section heading and body text.
    """

    def _parse_page(self, **figure) -> tuple[list, list]:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "hairline.pdf"
            document = fitz.open()
            _hairline_figure(document.new_page(width=531, height=666), **figure)
            document.save(pdf_path)
            parsed = _parse(pdf_path, tmpdir)
            with fitz.open(pdf_path) as reopened:
                regions = [_region(block)[1] for block in _figure_blocks(parsed)]
                problems = [figure_region_problems(reopened[0], bbox, furniture_bboxes=[]) for bbox in regions]
        return regions, [" ".join(block.text.split()) for block in _text_blocks(parsed)], problems

    def test_captioned_hairline_diagram_gets_its_whole_region(self) -> None:
        regions, texts, problems = self._parse_page(caption="Figure 5.5 Vector search determines relevance.")
        self.assertEqual(len(regions), 1)
        x0, y0, x1, y1 = regions[0]
        self.assertLessEqual(x0, 102.0)  # "sofa is comfortable"
        self.assertGreaterEqual(x1, 325.0)  # "Relevant"
        self.assertGreater(y0, 150.0)  # below the body text above
        self.assertLess(y1, 272.0)  # above the caption
        self.assertEqual(problems, [[]])
        joined = " ".join(texts)
        self.assertNotIn("sofa is comfortable", joined)
        self.assertNotIn("Relevant", joined)
        self.assertIn("Figure 5.5 Vector search", joined)
        self.assertGreaterEqual(joined.count("Body text of the chapter"), 2)

    def test_side_caption_level_with_the_drawing_finds_it_too(self) -> None:
        regions, texts, _problems = self._parse_page(caption="Figure 5.5 Vector search.", side_caption=True)
        self.assertEqual(len(regions), 1)
        self.assertLess(regions[0][2], 350.0)  # stops before the caption beside it
        self.assertNotIn("couch is large", " ".join(texts))

    def test_the_same_strokes_without_a_caption_are_not_a_figure(self) -> None:
        regions, texts, _problems = self._parse_page(caption=None)
        self.assertEqual(regions, [])
        self.assertIn("sofa is comfortable", " ".join(texts))

    def test_title_between_the_figure_and_its_caption_is_part_of_the_figure(self) -> None:
        """RAG p39: "Small to Big Chunking" is set under the drawing, 13pt from it, above the caption."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "title.pdf"
            document = fitz.open()
            page = document.new_page(width=531, height=666)
            _body(page, 60, 150)
            page.draw_rect(fitz.Rect(111, 180, 475, 300), color=(0, 0, 0))
            page.draw_circle(fitz.Point(200, 240), 30, color=None, fill=ORANGE)
            page.draw_circle(fitz.Point(380, 240), 30, color=None, fill=ORANGE)
            page.insert_text((216, 312), "Dividing into compact units", fontsize=7)
            page.insert_text((249, 336), "Small to Big Chunking", fontsize=9)
            page.insert_text((102, 356), "Figure 3.2 Data-chunking process", fontsize=8)
            _body(page, 380, 480)
            document.save(pdf_path)
            parsed = _parse(pdf_path, tmpdir)
        regions = [_region(block)[1] for block in _figure_blocks(parsed)]
        self.assertEqual(len(regions), 1)
        self.assertGreaterEqual(regions[0][3], 338.0)
        self.assertLess(regions[0][3], 346.0)
        self.assertNotIn("Small to Big Chunking", " ".join(block.text for block in _text_blocks(parsed)))


class TextOnlyFigureProseTests(unittest.TestCase):
    """A figure made up from text blocks above its caption never takes in body text.

    Agent p120 at 63639db: with no drawing found for Figure 5.5, the blocks above its caption were
    taken as the figure, including the paragraph ending "... this semantic connection can't be
    recognized automatically." and the section heading.
    """

    def test_body_paragraphs_above_the_caption_stay_text(self) -> None:
        paragraph = (
            "Keyword search won't find documents containing couch. Without a synonym dictionary, "
            "this connection can't be recognized automatically."
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "orphan.pdf"
            document = fitz.open()
            page = document.new_page(width=531, height=666)
            page.insert_textbox(fitz.Rect(102, 60, 477, 100), paragraph, fontsize=10)
            _body(page, 100, 140)  # one long sentence
            page.insert_text((150, 150), "Capital", fontsize=7)
            page.insert_text((300, 150), "Debt", fontsize=7)
            page.insert_text((150, 200), "Stock", fontsize=7)
            page.insert_text((300, 200), "Rare", fontsize=7)
            page.insert_text((102, 230), "Figure 3.3 Word relations in a vector space", fontsize=8)
            _body(page, 260, 360)
            document.save(pdf_path)
            parsed = _parse(pdf_path, tmpdir)
        texts = " ".join(" ".join(block.text.split()) for block in _text_blocks(parsed))
        self.assertIn("Without a synonym dictionary", texts)
        self.assertIn("Body text of the chapter", texts)
        for block in _figure_blocks(parsed):
            self.assertGreater(_region(block)[1][1], 100.0)


if __name__ == "__main__":
    unittest.main()


class ArtOfAIProductFigureTests(unittest.TestCase):
    """The Art of AI Product Development (Manning): a wrapped cross-reference read as a caption, a
    section rule joined to the author photo, a flat row of boxes under the size bar, and margin
    diagrams cut short."""

    def _parse_built(self, build) -> tuple[list, list, Path, str]:
        tmpdir = tempfile.mkdtemp()
        pdf_path = Path(tmpdir) / "book.pdf"
        document = fitz.open()
        build(document.new_page(width=531, height=666))
        document.save(pdf_path)
        parsed = _parse(pdf_path, tmpdir)
        return [_region(block)[1] for block in _figure_blocks(parsed)], _text_blocks(parsed), pdf_path, tmpdir

    def test_a_wrapped_lowercase_cross_reference_is_not_a_caption(self) -> None:
        """p35: "…shown earlier in figure 1.2. This will enable you…" wrapped so a line starts "figure 1.2."."""
        from book_agent.ingestion.text import _FIGURE_CAPTION_PATTERN

        self.assertIsNone(_FIGURE_CAPTION_PATTERN.match("figure 1.2. This will enable you to think about AI in a systematic way"))
        self.assertIsNone(_FIGURE_CAPTION_PATTERN.match("figure 2.2: We start with sourcing opportunities"))
        for caption in ("Figure 1.2 The components of an AI system", "FIGURE 3-1 Agents", "Fig. 4 Overview"):
            self.assertIsNotNone(_FIGURE_CAPTION_PATTERN.match(caption), caption)

    def test_a_flat_row_of_boxes_its_caption_claims_is_a_figure(self) -> None:
        def build(page):
            _body(page, 60, 200)
            for left in (57, 171, 285, 398):
                page.draw_rect(fitz.Rect(left, 255, left + 63, 284), color=(0, 0, 0), fill=(0.85, 0.9, 1.0), width=0.5)
            for start in (120, 234, 348):
                _arrow(page, (start, 270), (start + 50, 270))
            page.insert_text((57, 306), "Figure 7.5 Constructing the embedding database", fontsize=8)
            _body(page, 330, 420)

        regions, texts, _path, _tmp = self._parse_built(build)
        self.assertEqual(len(regions), 1)
        x0, y0, x1, y1 = regions[0]
        self.assertLessEqual(x0, 57.5)
        self.assertGreaterEqual(x1, 460.5)
        self.assertGreater(y0, 200.0)
        self.assertLess(y1, 297.0)

    def test_a_section_rule_across_the_text_block_is_not_part_of_a_figure(self) -> None:
        """p21: the 0.5pt rule under "about the author" (77% of the page) joined the photo, and through it
        the heading and the bio."""
        from book_agent.ingestion.pdf.figure_extent import _is_candidate_path

        self.assertFalse(_is_candidate_path((66.0, 246.9, 474.0, 246.9), 531.0, 666.0))
        # A figure's own lines and boxes still count: a short arrow, a wide flat box.
        self.assertTrue(_is_candidate_path((120.0, 270.0, 170.0, 270.0), 531.0, 666.0))
        self.assertTrue(_is_candidate_path((57.0, 255.0, 461.0, 284.0), 531.0, 666.0))

    def test_a_region_grows_to_the_boxes_it_cuts_and_stops_at_body_text(self) -> None:
        from book_agent.ingestion.pdf.figure_extent import complete_figure_region

        document = fitz.open()
        page = document.new_page(width=531, height=666)
        _body(page, 60, 160)
        for top in (181, 220, 259):
            page.draw_rect(fitz.Rect(402, top, 459, top + 10), color=(0, 0, 0), fill=(0.2, 0.3, 0.6), width=0.5)
        for top in (191, 230):
            _arrow(page, (430, top), (430, top + 29))
        page.insert_textbox(fitz.Rect(93, 171, 381, 400), BODY, fontsize=10)
        grown = complete_figure_region(page, (402.0, 220.0, 459.0, 269.0))
        self.assertLessEqual(grown[1], 181.5)  # the first box, cut off before
        self.assertGreaterEqual(grown[0], 390.0)  # never over the text beside it
        self.assertEqual(figure_region_problems(page, grown, furniture_bboxes=[]), [])


class TextPanelBackgroundTests(unittest.TestCase):
    """A sidebar's tint is not a figure's: The Art of AI Product Development p42 (a case study box with a
    table in it), RAG from First Principles p127 (a note box) and The Art of Code p117 (a math refresher)
    were each cropped whole as one picture, their text never translated."""

    def test_a_light_borderless_panel_with_body_text_on_it_is_a_panel(self) -> None:
        from book_agent.ingestion.pdf.extract import _is_text_panel_background

        tint = {"fill": (0.97, 0.96, 0.91), "color": None, "items": [("re", None)]}
        prose = SimpleNamespace(prose=True, bbox=(114.0, 230.0, 460.0, 242.0))
        self.assertTrue(_is_text_panel_background(tint, (102.0, 214.0, 474.0, 458.0), 531.0, [prose]))
        # Not a panel: no body text on it, a dark or outlined shape, or a narrow box in a figure.
        self.assertFalse(_is_text_panel_background(tint, (102.0, 214.0, 474.0, 458.0), 531.0, []))
        dark = {**tint, "fill": (0.28, 0.42, 0.52)}
        self.assertFalse(_is_text_panel_background(dark, (114.0, 281.0, 461.0, 297.0), 531.0, [prose]))
        outlined = {**tint, "color": (0, 0, 0)}
        self.assertFalse(_is_text_panel_background(outlined, (102.0, 214.0, 474.0, 458.0), 531.0, [prose]))
        self.assertFalse(_is_text_panel_background(tint, (300.0, 214.0, 400.0, 458.0), 531.0, [prose]))
