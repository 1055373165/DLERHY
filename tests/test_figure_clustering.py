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

    def test_anchor_below_min_area_dropped(self) -> None:
        """Tiny vector rules (e.g. 18×18 page-divider lines) are filtered."""
        blocks = [
            _image_block((100.0, 100.0, 110.0, 110.0), anchor="rule"),  # 100 area, < 1800
        ]
        report = cluster_figure_regions(blocks)
        self.assertEqual(report.clusters, [])

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

    def test_density_guard_suppresses_absorption_in_prose_block(self) -> None:
        """If the search zone has multiple long prose paragraphs around the
        anchor, suppress absorption — anchor is embedded in body text."""
        long_text = "x" * 250
        # Anchor (100, 200, 400, 220) tiny banner; 30pt pad → zone y in [170, 250].
        # Place 2 long prose blocks with centers inside the zone:
        #   prose-above: (100, 175, 400, 195) → center y=185 ∈ [170, 250]
        #   prose-below: (100, 230, 400, 245) → center y=237 ∈ [170, 250]
        blocks = [
            _image_block((100.0, 200.0, 400.0, 220.0), anchor="img-1"),
            _text_block(long_text, (100.0, 175.0, 400.0, 195.0), anchor="prose-above"),
            _text_block(long_text, (100.0, 230.0, 400.0, 245.0), anchor="prose-below"),
            # A short label that would normally be absorbed (center inside anchor):
            _text_block("note", (200.0, 205.0, 240.0, 218.0), anchor="lbl"),
        ]
        report = cluster_figure_regions(blocks)
        self.assertEqual(len(report.clusters), 1)
        cluster = report.clusters[0]
        # The short "note" label is rejected because surrounding prose density is high.
        self.assertEqual(cluster.inline_label_indices, ())
        density_rejects = [d for d in report.decisions if d.action == "reject_high_prose_density"]
        self.assertEqual(len(density_rejects), 1)

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
        from book_agent.domain.structure.pdf import (
            PdfStructureRecoveryService,
            _RecoveredBlock,
        )

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


if __name__ == "__main__":
    unittest.main()
