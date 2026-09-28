"""Unit tests for the figure clustering pass.

Synthetic ``BlockLike`` fixtures are used so the tests stay independent
of the PyMuPDF parser. Each test exercises one specific guard or merge
behavior described in the module docstring.
"""
# ruff: noqa: E402

import os
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Avoid importing the heavy app stack during these unit tests.
os.environ.setdefault("BOOK_AGENT_TRANSLATION_BACKEND", "echo")
os.environ.setdefault("BOOK_AGENT_TRANSLATION_MODEL", "echo-worker")

from book_agent.domain.enums import BlockType
from book_agent.domain.structure.figure_clustering import (
    FIGURE_LABEL_REGION_KEY,
    PAGE_BACKGROUND_KEY,
    FigureClusterConfig,
    cluster_figure_regions,
    figure_cluster_config_from_settings,
    iter_replaced_block_indices,
)


@dataclass
class _StubBlock:
    """Minimal BlockLike implementation for tests."""

    role: str
    block_type: BlockType
    text: str
    bbox: tuple[float, float, float, float]
    page_number: int = 1
    font_size_avg: float = 11.0
    anchor: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def bbox_regions(self) -> list[dict[str, Any]]:
        return [{"page_number": self.page_number, "bbox": list(self.bbox)}]


@dataclass
class _MultiRegionBlock(_StubBlock):
    """A block merged from several separate text runs, each with its region."""

    regions: list[tuple[float, float, float, float]] = field(default_factory=list)

    @property
    def bbox_regions(self) -> list[dict[str, Any]]:
        return [{"page_number": self.page_number, "bbox": list(region)} for region in self.regions]


def _image_block(
    bbox: tuple[float, float, float, float],
    *,
    image_type: str = "vector_drawing",
    page: int = 1,
    anchor: str = "img",
) -> _StubBlock:
    return _StubBlock(
        role="image",
        block_type=BlockType.IMAGE,
        text="[Image]",
        bbox=bbox,
        page_number=page,
        font_size_avg=0.0,
        anchor=anchor,
        metadata={"image_type": image_type},
    )


def _text_block(
    text: str,
    bbox: tuple[float, float, float, float],
    *,
    block_type: BlockType = BlockType.PARAGRAPH,
    page: int = 1,
    font_size: float = 11.0,
    anchor: str = "para",
) -> _StubBlock:
    return _StubBlock(
        role=str(block_type.value),
        block_type=block_type,
        text=text,
        bbox=bbox,
        page_number=page,
        font_size_avg=font_size,
        anchor=anchor,
    )


class FigureRegionMembershipTests(unittest.TestCase):
    """What a figure takes in and what it leaves to the page."""

    def test_running_header_inside_the_figure_box_is_not_a_label(self) -> None:
        header = _text_block("7 Curse of the models", (200.0, 25.0, 474.0, 36.0), anchor="hdr")
        header.role = "header"
        blocks = [
            _image_block((73.0, 14.0, 490.0, 300.0), anchor="img"),
            header,
            _text_block("Causal Language Modeling", (133.0, 110.0, 237.0, 122.0), anchor="lbl"),
        ]
        report = cluster_figure_regions(blocks)
        self.assertEqual(report.clusters[0].inline_label_indices, (2,))

    def test_labels_merged_into_one_sentence_like_block_are_still_labels(self) -> None:
        regions = [(309.0, 379.0, 326.0, 389.0), (254.0, 380.0, 268.0, 389.0), (108.0, 423.0, 180.0, 433.0)]
        labels = _MultiRegionBlock(
            role="body",
            block_type=BlockType.PARAGRAPH,
            text="walks 0.02 0.1 applauds 0.4 teaches The teacher ?",
            bbox=regions[0],
            anchor="labels",
            regions=regions,
        )
        report = cluster_figure_regions([_image_block((99.0, 370.0, 463.0, 486.0)), labels])
        self.assertEqual(report.clusters[0].inline_label_indices, (1,))
        self.assertLessEqual(report.clusters[0].bbox[0], 108.0)

    def test_caption_in_the_margin_beside_the_figure_is_linked(self) -> None:
        blocks = [
            _image_block((99.0, 362.0, 321.0, 488.0), anchor="img"),
            _text_block(
                "Figure 9.7 Sequential Workflow: All processes accumulate in context.",
                (335.0, 455.0, 457.0, 485.0),
                block_type=BlockType.CAPTION,
                anchor="cap",
            ),
        ]
        report = cluster_figure_regions(blocks)
        self.assertEqual(report.clusters[0].caption_index, 1)

    def test_text_over_a_page_background_picture_stays_text(self) -> None:
        """A back cover's tinted half is a picture printed to the page edges; its text is the page's text."""
        panel = _image_block((315.0, 0.0, 531.0, 666.0), image_type="embedded_image", anchor="panel")
        panel.metadata[PAGE_BACKGROUND_KEY] = True
        blocks = [
            panel,
            _text_block("About the reader", (380.0, 230.0, 470.0, 245.0), anchor="head"),
            _text_block("For Python developers", (340.0, 250.0, 500.0, 262.0), anchor="line"),
        ]
        report = cluster_figure_regions(blocks)
        self.assertEqual(report.clusters, [])
        self.assertEqual(list(iter_replaced_block_indices(report)), [])


class FigureClusteringTests(unittest.TestCase):
    # ---- Step 1: anchor merging ----

    def test_three_adjacent_vector_fragments_merge_into_one_figure(self) -> None:
        """Fig 2.2 case: three vector_drawing fragments stacked vertically
        with small gaps between them should merge into one FIGURE."""
        blocks = [
            _image_block((100.0, 100.0, 300.0, 180.0), anchor="img-1"),
            _image_block((100.0, 200.0, 300.0, 280.0), anchor="img-2"),
            _image_block((100.0, 300.0, 300.0, 380.0), anchor="img-3"),
        ]
        report = cluster_figure_regions(blocks)
        self.assertEqual(len(report.clusters), 1)
        cluster = report.clusters[0]
        self.assertEqual(set(cluster.anchor_indices), {0, 1, 2})
        # Union bbox spans the full vertical range.
        self.assertEqual(cluster.bbox, (100.0, 100.0, 300.0, 380.0))

    def test_far_apart_anchors_stay_separate(self) -> None:
        """Two figures on the same page with a large gap remain separate."""
        blocks = [
            _image_block((100.0, 100.0, 300.0, 180.0), anchor="img-1"),
            _image_block((100.0, 500.0, 300.0, 580.0), anchor="img-2"),
        ]
        report = cluster_figure_regions(blocks)
        self.assertEqual(len(report.clusters), 2)

    def test_prose_sentence_between_close_anchors_keeps_figures_separate(self) -> None:
        """Two charts with a one-line body sentence between them (gap < max_anchor_gap_pt)
        used to merge into one figure that swallowed the sentence."""
        sentence = "Inverted Head & Shoulders is just the opposite of Head & Shoulders, as you can see below."
        blocks = [
            _image_block((100.0, 100.0, 400.0, 250.0), image_type="embedded_image", anchor="img-1"),
            _text_block(sentence, (100.0, 254.0, 420.0, 266.0), anchor="para-1"),
            _image_block((100.0, 270.0, 400.0, 450.0), image_type="embedded_image", anchor="img-2"),
        ]
        report = cluster_figure_regions(blocks)
        self.assertEqual(len(report.clusters), 2)
        self.assertNotIn(1, {index for cluster in report.clusters for index in cluster.inline_label_indices})

    def test_prose_sentence_inside_anchor_is_not_absorbed_as_label(self) -> None:
        blocks = [
            _image_block((100.0, 100.0, 400.0, 400.0), anchor="img-1"),
            _text_block(
                "Price makes a new high while the indicator fails to confirm it.",
                (120.0, 240.0, 380.0, 252.0),
                anchor="para-1",
            ),
        ]
        report = cluster_figure_regions(blocks)
        self.assertEqual(report.clusters[0].inline_label_indices, ())

    def test_anchor_below_min_area_dropped(self) -> None:
        """Tiny vector rules (e.g. 18×18 page-divider lines) are filtered."""
        blocks = [
            _image_block((100.0, 100.0, 110.0, 110.0), anchor="rule"),  # 100 area, < 1800
        ]
        report = cluster_figure_regions(blocks)
        self.assertEqual(report.clusters, [])

    def test_small_piece_touching_a_figure_joins_it(self) -> None:
        """A screenshot's title strip, placed as its own small image, is part of the screenshot."""
        blocks = [
            _image_block((152.0, 354.0, 435.0, 576.0), image_type="embedded_image", anchor="shot"),
            _image_block((138.0, 343.0, 183.0, 355.0), image_type="embedded_image", anchor="strip"),
        ]
        report = cluster_figure_regions(blocks)
        self.assertEqual(len(report.clusters), 1)
        self.assertEqual(sorted(report.clusters[0].anchor_indices), [0, 1])
        self.assertEqual(report.clusters[0].bbox, (138.0, 343.0, 435.0, 576.0))

    def test_small_tiles_covering_a_figure_sized_area_form_a_figure(self) -> None:
        """An icon grid pasted as dozens of 10pt image tiles is one figure, not dozens of pictures."""
        tiles = [
            _image_block((100.0 + 10.5 * col, 480.0 + 10.5 * row, 110.0 + 10.5 * col, 490.0 + 10.5 * row),
                         image_type="embedded_image", anchor=f"tile{row}-{col}")
            for row in range(8)
            for col in range(6)
        ]
        report = cluster_figure_regions(tiles)
        self.assertEqual(len(report.clusters), 1)
        self.assertEqual(len(report.clusters[0].anchor_indices), len(tiles))

    # ---- Step 2 + 3: text classification + ownership ----

    def test_short_label_inside_anchor_absorbed(self) -> None:
        """Inline diagram label (e.g. "Normalization:" inside flow box)
        falls inside the anchor bbox → absorb, no separate translation."""
        blocks = [
            _image_block((100.0, 100.0, 400.0, 300.0), anchor="img-1"),
            _text_block("Normalization:", (180.0, 180.0, 300.0, 200.0), anchor="lbl-1"),
        ]
        report = cluster_figure_regions(blocks)
        self.assertEqual(len(report.clusters), 1)
        cluster = report.clusters[0]
        self.assertEqual(cluster.inline_label_indices, (1,))
        self.assertIsNone(cluster.caption_index)

    def test_caption_pattern_kept_separate(self) -> None:
        """Text starting with "Figure 2.4 ..." is a caption — kept as a
        separate translatable block linked to the figure."""
        blocks = [
            _image_block((100.0, 100.0, 400.0, 300.0), anchor="img-1"),
            _text_block(
                "Figure 2.4 The segmentation process breaks normalized text into words or tokens.",
                (100.0, 310.0, 400.0, 330.0),
                block_type=BlockType.CAPTION,
                anchor="cap-1",
            ),
        ]
        report = cluster_figure_regions(blocks)
        self.assertEqual(len(report.clusters), 1)
        cluster = report.clusters[0]
        self.assertEqual(cluster.caption_index, 1)
        self.assertEqual(cluster.inline_label_indices, ())

    def test_long_paragraph_adjacent_stays_as_prose(self) -> None:
        """A 250-char paragraph next to the figure is body prose, not a label.
        Length guard fires before any absorption."""
        long_text = "x" * 250
        # Anchor (100, 100, 400, 300); 30pt pad → zone y in [70, 330].
        # Paragraph bbox (100, 305, 400, 325) → center y=315, inside zone.
        blocks = [
            _image_block((100.0, 100.0, 400.0, 300.0), anchor="img-1"),
            _text_block(long_text, (100.0, 305.0, 400.0, 325.0), anchor="para-1"),
        ]
        report = cluster_figure_regions(blocks)
        self.assertEqual(len(report.clusters), 1)
        cluster = report.clusters[0]
        self.assertEqual(cluster.inline_label_indices, ())
        self.assertIsNone(cluster.caption_index)
        # Decision log records the rejection.
        rejected = [d for d in report.decisions if d.action == "reject_long"]
        self.assertEqual(len(rejected), 1)

    def test_text_between_two_anchors_assigned_to_closest_center(self) -> None:
        """Center-distance ownership (retain-pdf algorithm). A short label
        sits inside both anchors' search zones — should go to the anchor
        whose center is closer."""
        # Two anchors side by side; label inside the right one.
        blocks = [
            _image_block((100.0, 100.0, 200.0, 300.0), anchor="img-left"),
            _image_block((220.0, 100.0, 320.0, 300.0), anchor="img-right"),
            _text_block(
                "y-axis",
                (240.0, 200.0, 280.0, 220.0),  # center at (260, 210), inside right anchor
                anchor="lbl",
            ),
        ]
        # Anchors are far enough apart that they don't merge (gap 20 < 24, hmm).
        # Use a tighter config to force them separate:
        config = FigureClusterConfig(max_anchor_gap_pt=10.0)
        report = cluster_figure_regions(blocks, config=config)
        self.assertEqual(len(report.clusters), 2)
        right_cluster = next(
            c for c in report.clusters if 1 in c.anchor_indices
        )
        left_cluster = next(
            c for c in report.clusters if 0 in c.anchor_indices
        )
        self.assertIn(2, right_cluster.inline_label_indices)
        self.assertNotIn(2, left_cluster.inline_label_indices)

    def test_disabled_returns_empty_report(self) -> None:
        """Kill-switch: enabled=False produces no clusters even with
        textbook merge candidates present."""
        blocks = [
            _image_block((100.0, 100.0, 300.0, 180.0), anchor="img-1"),
            _image_block((100.0, 200.0, 300.0, 280.0), anchor="img-2"),
        ]
        report = cluster_figure_regions(blocks, config=FigureClusterConfig(enabled=False))
        self.assertEqual(report.clusters, [])
        self.assertEqual(report.decisions, [])

    def test_density_guard_suppresses_absorption_when_anchor_contains_prose(self) -> None:
        """Density guard fires when long prose blocks sit *inside* the
        anchor's own bbox — anchor was probably misclassified as image
        when it's really a body paragraph region."""
        long_text = "x" * 250
        # Large anchor (100,100,400,400). Place 2 long blocks INSIDE the
        # anchor bbox so they trip the density guard.
        blocks = [
            _image_block((100.0, 100.0, 400.0, 400.0), anchor="img-1"),
            _text_block(long_text, (110.0, 150.0, 390.0, 200.0), anchor="prose-inside-1"),
            _text_block(long_text, (110.0, 250.0, 390.0, 300.0), anchor="prose-inside-2"),
            _text_block("note", (200.0, 320.0, 240.0, 335.0), anchor="lbl"),
        ]
        report = cluster_figure_regions(blocks)
        self.assertEqual(len(report.clusters), 1)
        cluster = report.clusters[0]
        self.assertEqual(cluster.inline_label_indices, ())
        density_rejects = [d for d in report.decisions if d.action == "reject_high_prose_density"]
        self.assertEqual(len(density_rejects), 1)

    def test_density_guard_does_not_fire_for_prose_above_or_below_anchor(self) -> None:
        """Body paragraphs above / below a real figure must NOT trip the
        density guard. This was the regression that blocked Figure 3.2's
        labels from being absorbed — the guard counted surrounding body
        prose (which is normal book layout) and rejected the cluster."""
        long_text = "x" * 250
        # Anchor (100,200,400,220) tiny banner; long blocks ABOVE/BELOW.
        blocks = [
            _image_block((100.0, 200.0, 400.0, 220.0), anchor="img-1"),
            _text_block(long_text, (100.0, 175.0, 400.0, 195.0), anchor="prose-above"),
            _text_block(long_text, (100.0, 230.0, 400.0, 245.0), anchor="prose-below"),
            _text_block("note", (200.0, 205.0, 240.0, 218.0), anchor="lbl"),
        ]
        report = cluster_figure_regions(blocks)
        self.assertEqual(len(report.clusters), 1)
        cluster = report.clusters[0]
        # Short label gets absorbed because density guard didn't fire.
        self.assertEqual(len(cluster.inline_label_indices), 1)
        density_rejects = [d for d in report.decisions if d.action == "reject_high_prose_density"]
        self.assertEqual(len(density_rejects), 0)

    def test_b_fallback_requires_inside_anchor_by_default(self) -> None:
        """A short non-caption label that lies outside the anchor bbox but
        inside the padded search zone is NOT absorbed by default — the
        production safe path treats it as suspect prose."""
        # Anchor (100, 100, 200, 200); 30pt pad → zone x in [70, 230].
        # Text bbox (205, 145, 225, 155) → center (215, 150). Inside zone
        # but outside anchor (x=215 > 200).
        blocks = [
            _image_block((100.0, 100.0, 200.0, 200.0), anchor="img-1"),
            _text_block("hello", (205.0, 145.0, 225.0, 155.0), anchor="lbl"),
        ]
        report = cluster_figure_regions(blocks)
        cluster = report.clusters[0]
        self.assertEqual(cluster.inline_label_indices, ())
        # Decision log shows the rejection reason.
        rejects = [d for d in report.decisions if d.action == "reject_outside_anchor"]
        self.assertEqual(len(rejects), 1)

    def test_b_fallback_can_be_relaxed_via_config(self) -> None:
        """When ``inline_absorb_requires_inside_anchor=False``, a label
        within the search zone is absorbed even if outside the anchor."""
        blocks = [
            _image_block((100.0, 100.0, 200.0, 200.0), anchor="img-1"),
            _text_block("hello", (205.0, 145.0, 225.0, 155.0), anchor="lbl"),
        ]
        config = FigureClusterConfig(inline_absorb_requires_inside_anchor=False)
        report = cluster_figure_regions(blocks, config=config)
        cluster = report.clusters[0]
        self.assertEqual(cluster.inline_label_indices, (1,))

    def test_per_page_isolation(self) -> None:
        """Anchors on different pages never merge."""
        blocks = [
            _image_block((100.0, 100.0, 300.0, 180.0), anchor="img-p1", page=1),
            _image_block((100.0, 100.0, 300.0, 180.0), anchor="img-p2", page=2),
        ]
        report = cluster_figure_regions(blocks)
        self.assertEqual(len(report.clusters), 2)

    def test_iter_replaced_block_indices_yields_anchors_and_labels(self) -> None:
        """The replacement helper yields anchors + inline labels but NOT
        captions (captions stay in the block list)."""
        blocks = [
            _image_block((100.0, 100.0, 400.0, 300.0), anchor="img-1"),
            _text_block(
                "Figure 1 caption text",
                (100.0, 310.0, 400.0, 330.0),
                block_type=BlockType.CAPTION,
                anchor="cap-1",
            ),
            _text_block("inline", (180.0, 180.0, 250.0, 200.0), anchor="lbl-1"),
        ]
        report = cluster_figure_regions(blocks)
        replaced = list(iter_replaced_block_indices(report))
        self.assertCountEqual(replaced, [0, 2])  # anchor + inline label, not caption


class CaptionPatternTests(unittest.TestCase):
    """The caption regex needs to match the styles real-world books use."""

    def test_matches_common_caption_styles(self) -> None:
        positives = [
            "Figure 2.4 The segmentation process",
            "FIG. 7 Some title",
            "fig 1",
            "Table 3.1: Tokenization metrics",
            "Figure A The appendix layout",
            "图 2.4 分词流程",
            "示意图 1",
        ]
        anchor_bbox = (100.0, 100.0, 400.0, 300.0)
        for text in positives:
            blocks = [
                _image_block(anchor_bbox, anchor="img"),
                _text_block(text, (100.0, 310.0, 400.0, 330.0), anchor=f"cap-{text[:3]}"),
            ]
            report = cluster_figure_regions(blocks)
            self.assertEqual(
                len(report.clusters), 1, f"failed to cluster for: {text!r}"
            )
            self.assertEqual(
                report.clusters[0].caption_index,
                1,
                f"text {text!r} not classified as caption",
            )

    def test_rejects_non_caption_starting_text(self) -> None:
        negatives = [
            "The figure shows tokenization",  # leads with "The"
            "We see in figure 2.4",  # caption pattern not at start
            "table tennis is a sport",  # word "table" but not followed by digit
            "Tables Time series Code Knowledge graph Genomic",  # a row of labels, not "Table s"
            "Figures 3 and 4 show the same pipeline",
        ]
        anchor_bbox = (100.0, 100.0, 400.0, 300.0)
        for text in negatives:
            blocks = [
                _image_block(anchor_bbox, anchor="img"),
                _text_block(text, (100.0, 310.0, 400.0, 330.0), anchor="prose"),
            ]
            report = cluster_figure_regions(blocks)
            self.assertEqual(len(report.clusters), 1)
            self.assertIsNone(
                report.clusters[0].caption_index,
                f"non-caption text {text!r} was misclassified as caption",
            )


@dataclass
class _LabelledBlock(_MultiRegionBlock):
    """A block whose regions the extractor marked (or not) as measured figure labels, one flag per region."""

    flags: list[bool] = field(default_factory=list)

    @property
    def bbox_regions(self) -> list[dict[str, Any]]:
        regions = super().bbox_regions
        flags = self.flags or [True] * len(regions)
        return [{**region, **({FIGURE_LABEL_REGION_KEY: True} if flag else {})} for region, flag in zip(regions, flags)]


def _labelled(block: _StubBlock, *flags: bool) -> _LabelledBlock:
    regions = list(getattr(block, "regions", None) or [block.bbox])
    return _LabelledBlock(
        role=block.role,
        block_type=block.block_type,
        text=block.text,
        bbox=regions[0],
        page_number=block.page_number,
        font_size_avg=block.font_size_avg,
        anchor=block.anchor,
        regions=regions,
        flags=list(flags),
    )


class MeasuredFigureLabelTests(unittest.TestCase):
    """Text the extractor measured as a figure's labels is part of the figure.

    Real books: RAG p41 ("The prompt with the user question is augmented with the retrieved
    information..."), p225 (a requirements document of 500+ characters of bullets), Agent p37
    ("Chapters 2-4 Chapters 5-9 Building the basic agent loop ..." over 200 characters). The
    length, sentence and prose-density guards kept them as translatable paragraphs.
    """

    ANCHOR = (99.0, 295.0, 427.0, 517.0)

    def test_long_sentence_like_label_block_is_absorbed(self) -> None:
        text = (
            "Use case: Customer support system A multi-lingual, multi-geography customer support system that "
            "provides instant and accurate responses to customer queries, thus reducing dependency on human agents. "
            "Business objectives User needs."
        )
        self.assertGreater(len(text), 200)
        labels = _labelled(_text_block(text, (107.0, 299.0, 395.0, 344.0), font_size=7.0, anchor="lbl"))
        report = cluster_figure_regions([_image_block(self.ANCHOR), labels])
        self.assertEqual(report.clusters[0].inline_label_indices, (1,))

    def test_measured_labels_do_not_count_as_prose_around_the_figure(self) -> None:
        long_labels = "• Ask questions in natural language • Ask questions using voice input " * 4
        blocks = [
            _image_block(self.ANCHOR),
            _labelled(_text_block(long_labels, (109.0, 350.0, 235.0, 376.0), font_size=7.0, anchor="a")),
            _labelled(_text_block(long_labels, (280.0, 406.0, 397.0, 432.0), font_size=7.0, anchor="b")),
            _text_block("Constraints", (283.0, 440.0, 330.0, 450.0), font_size=7.0, anchor="c"),
        ]
        report = cluster_figure_regions(blocks)
        self.assertEqual(sorted(report.clusters[0].inline_label_indices), [1, 2, 3])

    def test_label_block_typed_as_code_is_absorbed(self) -> None:
        numbers = _labelled(
            _text_block("[-0.0147, 0.0179, 0.0119 ... -0.0047]", (125.0, 485.0, 414.0, 503.0),
                        block_type=BlockType.CODE, font_size=7.0, anchor="num")
        )
        report = cluster_figure_regions([_image_block((97.0, 463.0, 420.0, 585.0)), numbers])
        self.assertEqual(report.clusters[0].inline_label_indices, (1,))

    def test_block_joining_a_label_and_body_text_is_not_absorbed(self) -> None:
        body = (
            "There are disadvantages to this pattern as well. The orchestrator only sees the summary, so it "
            "cannot check how the child agent reached it, and every call pays for a second model context."
        )
        mixed = _labelled(
            _MultiRegionBlock(
                role="list_item",
                block_type=BlockType.LIST_ITEM,
                text="• 3 web searches • Result analysis • Summary writing " + body,
                bbox=(345.0, 176.0, 401.0, 202.0),
                font_size_avg=8.5,
                anchor="mixed",
                regions=[(345.0, 176.0, 401.0, 202.0), (93.0, 219.0, 279.0, 297.0)],
            ),
            True,
            False,
        )
        report = cluster_figure_regions([_image_block((294.0, 50.0, 464.0, 269.0)), mixed])
        self.assertEqual(report.clusters[0].inline_label_indices, ())

    def test_short_block_with_a_piece_outside_the_figure_is_not_absorbed(self) -> None:
        """"or go to .../freebook" merged with "(continued on last page)" from the text column beside it."""
        merged = _MultiRegionBlock(
            role="body",
            block_type=BlockType.PARAGRAPH,
            text="or go to https://www.manning.com/freebook (continued on last page)",
            bbox=(337.0, 534.0, 489.0, 556.0),
            font_size_avg=9.4,
            anchor="merged",
            regions=[(337.0, 534.0, 489.0, 556.0), (204.0, 554.0, 301.0, 565.0)],
        )
        report = cluster_figure_regions([_image_block((334.0, 484.0, 492.0, 635.0)), merged])
        self.assertEqual(report.clusters[0].inline_label_indices, ())
        self.assertEqual(report.clusters[0].bbox, (334.0, 484.0, 492.0, 635.0))  # not stretched into the column


class SettingsBridgeTests(unittest.TestCase):
    """The settings → config bridge must respect the live Settings type."""

    def test_bridge_from_real_settings(self) -> None:
        from book_agent.core.config import Settings

        settings = Settings()
        config = figure_cluster_config_from_settings(settings)
        self.assertEqual(config.enabled, settings.figure_cluster_enabled)
        self.assertEqual(
            config.max_anchor_gap_pt, settings.figure_cluster_max_anchor_gap_pt
        )
        self.assertEqual(
            config.inline_absorb_requires_inside_anchor,
            settings.figure_cluster_inline_absorb_requires_inside_anchor,
        )

    def test_bridge_falls_back_to_defaults_on_legacy_settings(self) -> None:
        """Older Settings objects without the new fields fall back to defaults."""

        class LegacySettings:
            pass

        config = figure_cluster_config_from_settings(LegacySettings())
        defaults = FigureClusterConfig()
        self.assertEqual(config, defaults)


class ParserIntegrationTests(unittest.TestCase):
    """Drive ``_apply_figure_clustering`` directly so we exercise the
    real ``_RecoveredBlock`` round-trip including metadata preservation."""

    def setUp(self) -> None:
        from book_agent.domain.structure.pdf import PdfStructureRecoveryService
        from book_agent.ingestion.pdf.models import _RecoveredBlock

        self.RecoveredBlock = _RecoveredBlock
        self.service = PdfStructureRecoveryService()

    def _make_image_block(
        self,
        bbox: tuple[float, float, float, float],
        *,
        anchor: str,
        page: int = 1,
        order: int = 0,
        image_type: str = "vector_drawing",
    ):
        return self.RecoveredBlock(
            role="image",
            block_type=BlockType.IMAGE,
            text="[Image]",
            page_start=page,
            page_end=page,
            bbox_regions=[{"page_number": page, "bbox": list(bbox)}],
            reading_order_index=order,
            parse_confidence=0.9,
            flags=[],
            font_size_avg=0.0,
            source_path="pdf://page/1",
            anchor=anchor,
            metadata={"image_type": image_type},
        )

    def _make_text_block(
        self,
        text: str,
        bbox: tuple[float, float, float, float],
        *,
        anchor: str,
        page: int = 1,
        order: int = 0,
        block_type: BlockType = BlockType.PARAGRAPH,
    ):
        return self.RecoveredBlock(
            role=str(block_type.value),
            block_type=block_type,
            text=text,
            page_start=page,
            page_end=page,
            bbox_regions=[{"page_number": page, "bbox": list(bbox)}],
            reading_order_index=order,
            parse_confidence=0.9,
            flags=[],
            font_size_avg=11.0,
            source_path="pdf://page/1",
            anchor=anchor,
            metadata={},
        )

    def test_fig_2_2_three_fragments_merge(self) -> None:
        """Three adjacent vector_drawing blocks merge into one FIGURE.

        Models the Fig 2.2 case from "How LLMs Work" — input/normalization/
        segmentation/output diagram emitted as 3 separate vector blocks
        because the strokes happened to fall in 3 sub-regions.
        """
        blocks = [
            self._make_image_block((100.0, 100.0, 300.0, 180.0), anchor="p1-img1", order=0),
            self._make_image_block((100.0, 200.0, 300.0, 280.0), anchor="p1-img2", order=1),
            self._make_image_block((100.0, 300.0, 300.0, 380.0), anchor="p1-img3", order=2),
        ]
        result = self.service._apply_figure_clustering(blocks)
        figure_blocks = [b for b in result if b.block_type == BlockType.FIGURE]
        image_blocks = [b for b in result if b.block_type == BlockType.IMAGE]
        self.assertEqual(len(figure_blocks), 1)
        self.assertEqual(len(image_blocks), 0)
        figure = figure_blocks[0]
        self.assertEqual(figure.bbox_regions[0]["bbox"], [100.0, 100.0, 300.0, 380.0])
        cluster_meta = figure.metadata["figure_cluster"]
        self.assertEqual(
            cluster_meta["anchor_block_anchors"],
            ["p1-img1", "p1-img2", "p1-img3"],
        )

    def test_fig_2_4_right_column_label_absorbed(self) -> None:
        """A short right-column label inside the anchor's bbox is absorbed
        into the FIGURE — does not survive as a translatable prose block.

        Models Fig 2.4 from the book — left column is the diagram, right
        column has short labels like ``"hello world"`` that the parser
        previously emitted as separate paragraphs.
        """
        # Anchor spans both columns; right column label center sits inside.
        blocks = [
            self._make_image_block(
                (100.0, 100.0, 500.0, 300.0), anchor="p1-img1", order=0
            ),
            # Adjacent prose ABOVE the figure, untouched.
            self._make_text_block(
                "Body paragraph not adjacent to figure search zone." * 4,
                (100.0, 50.0, 500.0, 65.0),
                anchor="p1-prose1",
                order=1,
            ),
            # Right-column label inside anchor bbox.
            self._make_text_block(
                "hello world",
                (380.0, 180.0, 480.0, 200.0),
                anchor="p1-lbl1",
                order=2,
            ),
        ]
        result = self.service._apply_figure_clustering(blocks)
        # Labels removed from output.
        anchors_remaining = {b.anchor for b in result}
        self.assertNotIn("p1-lbl1", anchors_remaining)
        self.assertIn("p1-prose1", anchors_remaining)
        # FIGURE block emitted with absorbed label recorded.
        figures = [b for b in result if b.block_type == BlockType.FIGURE]
        self.assertEqual(len(figures), 1)
        cluster_meta = figures[0].metadata["figure_cluster"]
        self.assertEqual(cluster_meta["absorbed_label_anchors"], ["p1-lbl1"])

    def test_caption_below_image_remains_translatable_with_link(self) -> None:
        """Caption text starting with "Figure X.Y" stays in the block list
        so it gets translated, but is tagged with ``figure_anchor`` for
        the export layer to render it next to the figure."""
        # Two anchors close enough to cluster (forces non-trivial cluster):
        blocks = [
            self._make_image_block((100.0, 100.0, 300.0, 200.0), anchor="p1-img1", order=0),
            self._make_image_block((100.0, 210.0, 300.0, 290.0), anchor="p1-img2", order=1),
            self._make_text_block(
                "Figure 2.4 The segmentation process breaks normalized text into tokens.",
                (100.0, 300.0, 500.0, 320.0),
                anchor="p1-cap1",
                order=2,
                block_type=BlockType.CAPTION,
            ),
        ]
        result = self.service._apply_figure_clustering(blocks)
        # Caption is preserved.
        captions = [b for b in result if b.block_type == BlockType.CAPTION]
        self.assertEqual(len(captions), 1)
        # FIGURE block emitted; caption tagged with figure_anchor.
        figures = [b for b in result if b.block_type == BlockType.FIGURE]
        self.assertEqual(len(figures), 1)
        self.assertEqual(captions[0].metadata.get("figure_anchor"), figures[0].anchor)

    def test_single_image_no_labels_passes_through_unchanged(self) -> None:
        """Trivial cluster (1 anchor, no labels) is skipped — original
        IMAGE block preserved as-is. Critical for not regressing existing
        single-figure-page tests."""
        blocks = [
            self._make_image_block((100.0, 100.0, 400.0, 300.0), anchor="p1-img1", order=0),
            self._make_text_block(
                "Figure 1. System overview diagram.",
                (96.0, 532.0, 396.0, 556.0),
                anchor="p1-cap1",
                order=1,
                block_type=BlockType.CAPTION,
            ),
        ]
        result = self.service._apply_figure_clustering(blocks)
        # Original block list returned untouched.
        self.assertEqual([b.anchor for b in result], ["p1-img1", "p1-cap1"])
        self.assertEqual(result[0].block_type, BlockType.IMAGE)



class FigureResidueTests(unittest.TestCase):
    """Code, tables and equations made of a figure's own text (RAG figure 1.3, Agent figures 1.6 and 9.10)."""

    FIGURE = (99.0, 51.0, 465.0, 305.0)
    CAPTION = "Figure 1.3 The teacher teaches the student how to answer."

    def cluster(self, *blocks: _StubBlock, caption: bool = True, image_type: str = "vector_drawing"):
        page = [_image_block(self.FIGURE, image_type=image_type)]
        if caption:
            page.append(_text_block(self.CAPTION, (99.0, 312.0, 465.0, 330.0), block_type=BlockType.CAPTION, anchor="cap"))
        return cluster_figure_regions([*page, *blocks])

    def absorbed(self, report) -> list[int]:
        return sorted(index for cluster in report.clusters for index in cluster.inline_label_indices)

    def test_a_table_of_repeated_labels_inside_the_figure_is_absorbed(self) -> None:
        table = _text_block("| The | teacher |\n| The | teacher |", (102.0, 132.0, 165.0, 158.0), block_type=BlockType.TABLE, anchor="t")
        self.assertEqual(self.absorbed(self.cluster(table)), [2])

    def test_a_legend_filling_a_captioned_drawing_is_absorbed(self) -> None:
        # Figure 9.10: the diagram's boxes read as a two-column "table" nearly as big as the drawing.
        legend = _text_block(
            "| User: 'What is 15 squared?' | |\n| Router agent | |\n| tools= [transfer] | |",
            (101.0, 53.0, 463.0, 303.0),
            block_type=BlockType.TABLE,
            anchor="t",
        )
        self.assertEqual(self.absorbed(self.cluster(legend)), [2])

    def test_labels_read_as_code_or_an_equation_are_absorbed(self) -> None:
        code = _text_block("{Query + Information} Retrieve", (234.0, 108.0, 301.0, 117.0), block_type=BlockType.CODE, anchor="c")
        equation = _text_block("MRR = 13/24 = 0.54", (150.0, 200.0, 260.0, 214.0), block_type=BlockType.EQUATION, anchor="e")
        self.assertEqual(self.absorbed(self.cluster(code, equation)), [2, 3])

    def test_real_listings_and_tables_are_never_absorbed(self) -> None:
        listing = _text_block("from openai import OpenAI\nclient = OpenAI()", (110.0, 60.0, 300.0, 90.0), block_type=BlockType.CODE, anchor="k")
        listing.metadata["has_monospace_font"] = True
        grid = _text_block(
            "| Use case | Needs data? | RAG? |\n| Chat bot | Yes: product data | Yes |\n| Translation | No: all in the prompt | No |",
            (110.0, 150.0, 400.0, 290.0),
            block_type=BlockType.TABLE,
            anchor="g",
        )
        outside = _text_block("| The | teacher |\n| The | teacher |", (102.0, 340.0, 165.0, 370.0), block_type=BlockType.TABLE, anchor="o")
        self.assertEqual(self.absorbed(self.cluster(listing, grid, outside)), [])

    def test_a_titled_table_inside_a_drawing_stays_a_table(self) -> None:
        # A ruled table's rules are a drawing too; its "Table 1.1" title says what it is.
        title = _text_block("Table 1.1 Models and prices", (99.0, 40.0, 300.0, 50.0), block_type=BlockType.CAPTION, anchor="title")
        table = _text_block("| Model | Price |\n| GPT-5 | 10 |", (102.0, 60.0, 300.0, 120.0), block_type=BlockType.TABLE, anchor="t")
        report = cluster_figure_regions([_image_block(self.FIGURE), title, table])
        self.assertNotIn(2, self.absorbed(report))

    def test_a_table_that_is_the_whole_uncaptioned_drawing_stays_a_table(self) -> None:
        table = _text_block("| Model | Price |\n| GPT-5 | 10 |", (100.0, 52.0, 464.0, 304.0), block_type=BlockType.TABLE, anchor="t")
        self.assertEqual(self.absorbed(self.cluster(table, caption=False)), [])


if __name__ == "__main__":
    unittest.main()
