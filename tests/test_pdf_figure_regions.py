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


if __name__ == "__main__":
    unittest.main()
