"""Spatial clustering of PDF blocks into coherent figure regions.

The DocIR PDF parser emits one block per detected raster image, vector
drawing cluster, or text region. Two real-world layouts confuse the
default per-block emission:

1. **Multi-column figures** — e.g. a left "Process" column with a
   diagram and a right "Example" column with short labels. The right
   column gets emitted as standalone text blocks, then translated as
   prose, and the figure crop loses half its content.

2. **Over-segmented vector figures** — a single logical diagram whose
   strokes happen to fall into multiple non-overlapping subregions. The
   parser emits each subregion as its own ``vector_drawing`` block, so
   the same figure renders three times in the export.

This module adds a post-recovery clustering pass that:

* Greedily merges spatially-adjacent image / vector_drawing anchors on
  the same page (case 2).
* For each merged anchor, scans an extended "label search zone" and
  pulls short text blocks into the figure as inline labels (case 1).
* Uses retain-pdf's center-distance ownership rule to disambiguate
  text blocks that fall inside multiple search zones.
* Applies four guards before absorbing any text: length, caption
  pattern, font-size ratio, and prose-neighbor density.
* Keeps caption-pattern text (``Figure 2.4 ...``) as a separately
  translatable CAPTION block linked to the figure — translation
  completeness is part of the production-grade contract.

The module is pure: it takes a sequence of block-like objects (anything
satisfying :class:`BlockLike`) plus per-page dimensions, and returns a
list of :class:`FigureCluster` decisions plus a structured decision log.
The caller materializes new blocks; this module never mutates state.

# Algorithm adapted from github.com/wxyhgk/retain-pdf (MIT)
# Specifically: ``owned_word_entries`` center-distance disambiguation
# from ``backend/scripts/services/rendering/redaction/text_analysis.py``.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol, Sequence

from book_agent.domain.enums import BlockType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types


class BlockLike(Protocol):
    """Minimal interface a clusterable block must satisfy.

    Matches the relevant subset of ``_RecoveredBlock`` fields. Using a
    Protocol keeps this module testable without importing the 10k-line
    ``pdf.py`` parser.
    """

    role: str
    block_type: BlockType
    text: str
    bbox_regions: list[dict[str, Any]]
    font_size_avg: float
    anchor: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class FigureClusterConfig:
    """Tunable thresholds. All distances are in PDF points (1/72 inch)."""

    enabled: bool = True
    """Master kill-switch. When False, ``cluster_figure_regions`` returns
    no clusters and an empty decision log; the caller leaves blocks
    untouched."""

    max_anchor_gap_pt: float = 24.0
    """Two image / vector anchors merge into one anchor region when both
    horizontal AND vertical gap between their bboxes are ≤ this value."""

    label_search_pad_pt: float = 30.0
    """An anchor's "label search zone" is its bbox expanded outward by
    this padding. Text blocks whose center falls inside the search zone
    are candidates for absorption."""

    max_label_chars: int = 200
    """A candidate text block longer than this is treated as prose, never
    absorbed. Captions can be longer — caption detection runs first."""

    min_label_size_ratio: float = 0.85
    """A candidate text block must have ``font_size_avg / page_baseline ≥``
    this ratio to qualify as an inline label. Larger labels are also
    accepted (ratio can be > 1.0)."""

    max_prose_neighbors_in_zone: int = 1
    """Density guard. If the search zone already contains more than this
    many text blocks that look like prose (long, no caption pattern),
    the anchor is treated as embedded in body text and label absorption
    is suppressed."""

    inline_absorb_requires_inside_anchor: bool = True
    """Conservative B-fallback: a text block is absorbed (no separate
    translation) only if its center lies inside the anchor bbox itself,
    not merely inside the padded search zone. Inline diagram labels
    (e.g. "Normalization:" inside a flow-chart box) match this; right-
    column labels typically do not — they're picked up via the caption
    path or as a separate caption block linked to the figure."""

    min_anchor_area_pt2: float = 1800.0
    """Anchors smaller than this are dropped from clustering — they are
    almost always decorative rules or icons rather than real figures."""


CAPTION_LEAD_PATTERN = re.compile(
    r"^\s*("
    r"figure|fig\.?|"  # English
    r"table|tab\.?|"
    r"图|表|示意图|插图"  # Chinese (defensive — input is English here)
    r")\s*[\d一-鿿]",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FigureCluster:
    """One clustered figure region — caller materializes a FIGURE block.

    All bboxes are in PDF point coordinates, ``(x0, y0, x1, y1)``.
    """

    page_number: int
    bbox: tuple[float, float, float, float]
    anchor_indices: tuple[int, ...]
    """Indices in the input block list pointing at image/vector_drawing
    blocks that form the figure's geometric anchor. These should be
    REMOVED from the block list and replaced with one FIGURE block."""

    inline_label_indices: tuple[int, ...]
    """Indices of text blocks absorbed into the figure (no translation,
    no separate render). These should also be REMOVED."""

    caption_index: int | None
    """Index of a caption block to LINK with the figure (kept separate
    so it gets translated). The caller should leave the caption block
    in place but record ``figure_anchor`` in its metadata so the export
    layer can render the caption immediately below the figure."""

    image_type: str
    """``embedded_image``, ``vector_drawing``, or ``mixed`` if both."""


@dataclass
class ClusterDecision:
    """One audit-log entry. Used for diagnostics; not in the hot path."""

    page_number: int
    action: str
    block_anchors: list[str]
    reason: str
    bbox: tuple[float, float, float, float] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class FigureClusterReport:
    clusters: list[FigureCluster]
    decisions: list[ClusterDecision]


# ---------------------------------------------------------------------------
# Geometry helpers


_Bbox = tuple[float, float, float, float]


def _block_page_number(block: BlockLike) -> int | None:
    if not block.bbox_regions:
        return None
    region = block.bbox_regions[0]
    page_number = region.get("page_number") if isinstance(region, dict) else None
    if isinstance(page_number, int):
        return page_number
    return None


def _block_bbox(block: BlockLike) -> _Bbox | None:
    if not block.bbox_regions:
        return None
    region = block.bbox_regions[0]
    if not isinstance(region, dict):
        return None
    raw = region.get("bbox")
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        return (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))
    except (TypeError, ValueError):
        return None


def _bbox_center(bbox: _Bbox) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


def _bbox_area(bbox: _Bbox) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _bbox_union(a: _Bbox, b: _Bbox) -> _Bbox:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _bbox_pad(bbox: _Bbox, pad: float) -> _Bbox:
    return (bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad)


def _bbox_contains_point(bbox: _Bbox, x: float, y: float) -> bool:
    return bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]


def _bbox_gap(a: _Bbox, b: _Bbox) -> tuple[float, float]:
    """Return ``(horizontal_gap, vertical_gap)`` — 0.0 means overlap or touch."""
    horizontal = max(a[0] - b[2], b[0] - a[2], 0.0)
    vertical = max(a[1] - b[3], b[1] - a[3], 0.0)
    return horizontal, vertical


def _squared_distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return dx * dx + dy * dy


# ---------------------------------------------------------------------------
# Per-page partitioning


_ANCHOR_BLOCK_TYPES = frozenset({BlockType.IMAGE, BlockType.FIGURE})

# Text block types eligible for caption-pattern detection or inline-label
# absorption. Structural types (HEADING, FOOTNOTE, QUOTE, CODE, TABLE,
# EQUATION) are deliberately excluded — they carry semantic meaning that
# we should never silently fold into a figure crop. Section headings
# adjacent to figures are common in academic layouts; absorbing them
# would silently drop chapter titles.
_TEXT_BLOCK_TYPES = frozenset(
    {
        BlockType.PARAGRAPH,
        BlockType.CAPTION,
        BlockType.LIST_ITEM,
    }
)


def _is_anchor(block: BlockLike) -> bool:
    if block.block_type in _ANCHOR_BLOCK_TYPES:
        return True
    image_type = block.metadata.get("image_type") if block.metadata else None
    return image_type in {"embedded_image", "vector_drawing"}


def _is_text_candidate(block: BlockLike) -> bool:
    if block.block_type not in _TEXT_BLOCK_TYPES:
        return False
    return bool(block.text and block.text.strip())


def _looks_like_caption_text(text: str) -> bool:
    return bool(CAPTION_LEAD_PATTERN.match(text or ""))


def _page_baseline_font_size(blocks: Sequence[BlockLike]) -> float:
    """Median font size over text blocks on the page; 0.0 if unknown."""
    sizes = sorted(
        block.font_size_avg
        for block in blocks
        if _is_text_candidate(block) and block.font_size_avg > 0.0
    )
    if not sizes:
        return 0.0
    return sizes[len(sizes) // 2]


# ---------------------------------------------------------------------------
# Anchor merging (Step 1)


def _merge_close_anchors(
    anchor_entries: list[tuple[int, _Bbox, str]],
    *,
    config: FigureClusterConfig,
) -> list[tuple[list[int], _Bbox, str]]:
    """Greedy merge of overlapping/adjacent anchor bboxes.

    Iterates anchors in (top, left) order; each anchor either joins an
    existing cluster (if both horizontal and vertical gaps are within
    ``max_anchor_gap_pt``) or starts a new one.
    """
    clusters: list[tuple[list[int], _Bbox, str]] = []
    sorted_entries = sorted(anchor_entries, key=lambda item: (round(item[1][1], 2), round(item[1][0], 2)))
    threshold = config.max_anchor_gap_pt
    for index, bbox, image_type in sorted_entries:
        if _bbox_area(bbox) < config.min_anchor_area_pt2:
            continue
        merged_into: int | None = None
        for cluster_idx, (_, cluster_bbox, cluster_kind) in enumerate(clusters):
            horizontal, vertical = _bbox_gap(cluster_bbox, bbox)
            if horizontal <= threshold and vertical <= threshold:
                merged_into = cluster_idx
                clusters[cluster_idx] = (
                    [*clusters[cluster_idx][0], index],
                    _bbox_union(cluster_bbox, bbox),
                    cluster_kind if cluster_kind == image_type else "mixed",
                )
                break
        if merged_into is None:
            clusters.append(([index], bbox, image_type))
    return clusters


# ---------------------------------------------------------------------------
# Text classification (Step 2 + 3)


def _owner_anchor_for_text(
    text_bbox: _Bbox,
    anchor_bboxes: Sequence[_Bbox],
) -> int | None:
    """Center-distance ownership.

    Adapted from retain-pdf ``owned_word_entries``: among all anchors
    whose bbox contains the text's center, pick the one whose center is
    closest to the text's center. Returns the anchor index, or None if
    no anchor contains the text center.
    """
    if not anchor_bboxes:
        return None
    text_center = _bbox_center(text_bbox)
    candidates: list[tuple[int, float]] = []
    for index, bbox in enumerate(anchor_bboxes):
        if _bbox_contains_point(bbox, text_center[0], text_center[1]):
            anchor_center = _bbox_center(bbox)
            candidates.append((index, _squared_distance(anchor_center, text_center)))
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[1])[0]


def _classify_text_for_anchor(
    block: BlockLike,
    text_bbox: _Bbox,
    anchor_bbox: _Bbox,
    *,
    baseline_size: float,
    config: FigureClusterConfig,
) -> tuple[str, str]:
    """Classify a text block in an anchor's search zone.

    Returns ``(label, reason)`` where label is one of:
      * ``"caption"`` — translate, link to figure (default A path)
      * ``"inline_label"`` — absorb into figure (B fallback)
      * ``"reject_long"`` — leave as prose (too long)
      * ``"reject_size"`` — leave as prose (font too different)
      * ``"reject_outside_anchor"`` — leave as prose (B requires inside)
      * ``"reject_other"``
    """
    text = (block.text or "").strip()
    char_count = len(text)

    # Caption path takes priority — even longer text matches if it leads
    # with "Figure X.Y" / "Table X.Y". Captions are always kept as
    # separate translatable blocks linked to the figure.
    if _looks_like_caption_text(text):
        return "caption", "caption-pattern"

    if char_count > config.max_label_chars:
        return "reject_long", f"len={char_count} > {config.max_label_chars}"

    if baseline_size > 0.0 and block.font_size_avg > 0.0:
        size_ratio = block.font_size_avg / baseline_size
        if size_ratio < config.min_label_size_ratio:
            # Smaller font: still a label candidate (small captions, axis
            # tick labels, etc.) — accept.
            pass

    if config.inline_absorb_requires_inside_anchor:
        text_center = _bbox_center(text_bbox)
        if not _bbox_contains_point(anchor_bbox, text_center[0], text_center[1]):
            return "reject_outside_anchor", "center outside anchor bbox (B requires inside)"

    return "inline_label", "inside anchor + short + non-caption"


def _count_prose_neighbors_in_zone(
    search_zone: _Bbox,
    text_blocks: Sequence[tuple[int, BlockLike, _Bbox]],
    exclude_indices: set[int],
    *,
    config: FigureClusterConfig,
) -> int:
    """Count prose-like text blocks (long, non-caption) inside the zone.

    Used by the density guard: if there are ≥ ``max_prose_neighbors`` + 1
    long prose blocks in the search zone, the anchor is sitting inside
    a prose passage and we should not absorb anything.
    """
    count = 0
    for index, block, bbox in text_blocks:
        if index in exclude_indices:
            continue
        center = _bbox_center(bbox)
        if not _bbox_contains_point(search_zone, center[0], center[1]):
            continue
        text = (block.text or "").strip()
        if _looks_like_caption_text(text):
            continue
        if len(text) > config.max_label_chars:
            count += 1
    return count


# ---------------------------------------------------------------------------
# Top-level orchestrator


def cluster_figure_regions(
    blocks: Sequence[BlockLike],
    *,
    config: FigureClusterConfig | None = None,
) -> FigureClusterReport:
    """Run all three clustering steps and return decision report.

    Parameters
    ----------
    blocks
        The recovered blocks for one document. Operations are scoped per
        page; cross-page clustering is not attempted (figures don't span
        page boundaries in any document we care about).
    config
        Optional config override. Defaults to :class:`FigureClusterConfig`
        with production-tuned thresholds.

    Returns
    -------
    FigureClusterReport
        ``clusters``: zero or more :class:`FigureCluster` describing the
        merges to apply. ``decisions``: structured audit log.
    """
    cfg = config or FigureClusterConfig()
    if not cfg.enabled:
        return FigureClusterReport(clusters=[], decisions=[])

    decisions: list[ClusterDecision] = []
    clusters: list[FigureCluster] = []

    pages: dict[int, list[int]] = {}
    for index, block in enumerate(blocks):
        page = _block_page_number(block)
        if page is None:
            continue
        pages.setdefault(page, []).append(index)

    for page_number in sorted(pages):
        page_block_indices = pages[page_number]
        page_blocks = [blocks[i] for i in page_block_indices]

        anchors: list[tuple[int, _Bbox, str]] = []
        text_entries: list[tuple[int, BlockLike, _Bbox]] = []
        for local_idx, block in zip(page_block_indices, page_blocks):
            bbox = _block_bbox(block)
            if bbox is None:
                continue
            if _is_anchor(block):
                image_type = (block.metadata or {}).get("image_type") or ""
                anchors.append((local_idx, bbox, str(image_type)))
            elif _is_text_candidate(block):
                text_entries.append((local_idx, block, bbox))

        if not anchors:
            continue

        merged_anchor_clusters = _merge_close_anchors(anchors, config=cfg)
        if not merged_anchor_clusters:
            continue

        baseline_size = _page_baseline_font_size(page_blocks)
        anchor_bboxes_only = [cluster_bbox for _, cluster_bbox, _ in merged_anchor_clusters]

        for cluster_indices, cluster_bbox, image_type in merged_anchor_clusters:
            search_zone = _bbox_pad(cluster_bbox, cfg.label_search_pad_pt)

            inline_label_indices: list[int] = []
            caption_index: int | None = None
            absorbed_local: set[int] = set()

            prose_density = _count_prose_neighbors_in_zone(
                search_zone, text_entries, absorbed_local, config=cfg
            )
            density_blocked = prose_density > cfg.max_prose_neighbors_in_zone

            for text_idx, text_block, text_bbox in text_entries:
                text_center = _bbox_center(text_bbox)
                if not _bbox_contains_point(search_zone, text_center[0], text_center[1]):
                    continue

                owner = _owner_anchor_for_text(text_bbox, anchor_bboxes_only)
                this_anchor_idx = anchor_bboxes_only.index(cluster_bbox)
                if owner is not None and owner != this_anchor_idx:
                    decisions.append(
                        ClusterDecision(
                            page_number=page_number,
                            action="reject_text_owned_by_other_anchor",
                            block_anchors=[text_block.anchor],
                            reason=f"center inside anchor[{owner}]; this is anchor[{this_anchor_idx}]",
                        )
                    )
                    continue

                kind, reason = _classify_text_for_anchor(
                    text_block,
                    text_bbox,
                    cluster_bbox,
                    baseline_size=baseline_size,
                    config=cfg,
                )

                if kind == "caption":
                    if caption_index is None:
                        caption_index = text_idx
                        decisions.append(
                            ClusterDecision(
                                page_number=page_number,
                                action="link_caption",
                                block_anchors=[text_block.anchor],
                                reason=reason,
                            )
                        )
                    continue

                if kind != "inline_label":
                    decisions.append(
                        ClusterDecision(
                            page_number=page_number,
                            action=kind,
                            block_anchors=[text_block.anchor],
                            reason=reason,
                        )
                    )
                    continue

                if density_blocked:
                    decisions.append(
                        ClusterDecision(
                            page_number=page_number,
                            action="reject_high_prose_density",
                            block_anchors=[text_block.anchor],
                            reason=f"prose_density={prose_density} > {cfg.max_prose_neighbors_in_zone}",
                        )
                    )
                    continue

                inline_label_indices.append(text_idx)
                absorbed_local.add(text_idx)
                decisions.append(
                    ClusterDecision(
                        page_number=page_number,
                        action="absorb_inline_label",
                        block_anchors=[text_block.anchor],
                        reason=reason,
                    )
                )

            final_bbox = cluster_bbox
            for absorbed_idx in inline_label_indices:
                absorbed_bbox = _block_bbox(blocks[absorbed_idx])
                if absorbed_bbox is not None:
                    final_bbox = _bbox_union(final_bbox, absorbed_bbox)

            anchor_anchors = [blocks[i].anchor for i in cluster_indices]
            clusters.append(
                FigureCluster(
                    page_number=page_number,
                    bbox=final_bbox,
                    anchor_indices=tuple(cluster_indices),
                    inline_label_indices=tuple(inline_label_indices),
                    caption_index=caption_index,
                    image_type=image_type or "vector_drawing",
                )
            )
            decisions.append(
                ClusterDecision(
                    page_number=page_number,
                    action="emit_figure",
                    block_anchors=anchor_anchors,
                    reason=(
                        f"anchors={len(cluster_indices)} "
                        f"absorbed={len(inline_label_indices)} "
                        f"caption={'yes' if caption_index is not None else 'no'} "
                        f"image_type={image_type}"
                    ),
                    bbox=final_bbox,
                )
            )

    if logger.isEnabledFor(logging.DEBUG):
        for decision in decisions:
            logger.debug("figure_cluster decision: %r", decision)

    return FigureClusterReport(clusters=clusters, decisions=decisions)


def figure_cluster_config_from_settings(settings: Any) -> FigureClusterConfig:
    """Build a :class:`FigureClusterConfig` from a ``Settings`` instance.

    Tolerant of older Settings objects that don't carry the new fields:
    falls back to dataclass defaults so callers in older deployments keep
    working until the env is updated.
    """
    defaults = FigureClusterConfig()
    return FigureClusterConfig(
        enabled=getattr(settings, "figure_cluster_enabled", defaults.enabled),
        max_anchor_gap_pt=getattr(
            settings, "figure_cluster_max_anchor_gap_pt", defaults.max_anchor_gap_pt
        ),
        label_search_pad_pt=getattr(
            settings, "figure_cluster_label_search_pad_pt", defaults.label_search_pad_pt
        ),
        max_label_chars=getattr(
            settings, "figure_cluster_max_label_chars", defaults.max_label_chars
        ),
        min_label_size_ratio=getattr(
            settings, "figure_cluster_min_label_size_ratio", defaults.min_label_size_ratio
        ),
        max_prose_neighbors_in_zone=getattr(
            settings,
            "figure_cluster_max_prose_neighbors_in_zone",
            defaults.max_prose_neighbors_in_zone,
        ),
        inline_absorb_requires_inside_anchor=getattr(
            settings,
            "figure_cluster_inline_absorb_requires_inside_anchor",
            defaults.inline_absorb_requires_inside_anchor,
        ),
        min_anchor_area_pt2=getattr(
            settings, "figure_cluster_min_anchor_area_pt2", defaults.min_anchor_area_pt2
        ),
    )


def iter_replaced_block_indices(report: FigureClusterReport) -> Iterable[int]:
    """Yield every block index that should be REMOVED by clustering.

    Anchors and inline labels are removed (anchors → replaced with a
    single FIGURE block; inline labels → fully absorbed). Caption
    blocks are NOT removed — they're kept and linked.
    """
    for cluster in report.clusters:
        yield from cluster.anchor_indices
        yield from cluster.inline_label_indices
