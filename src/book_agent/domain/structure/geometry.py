"""Bounding-box helpers shared by PDF structure recovery, artifact grouping and export crops.

Boxes are ``[x0, y0, x1, y1]`` in PDF points.
"""

from __future__ import annotations


def horizontal_overlap_ratio(left: list[float], right: list[float]) -> float:
    """Horizontal overlap as a fraction of the narrower box's width."""
    overlap = min(left[2], right[2]) - max(left[0], right[0])
    if overlap <= 0:
        return 0.0
    left_width = max(left[2] - left[0], 1.0)
    right_width = max(right[2] - right[0], 1.0)
    return overlap / min(left_width, right_width)


def union_bbox(left: list[float] | None, right: list[float] | None) -> list[float] | None:
    if left is None:
        return right
    if right is None:
        return left
    return [
        min(left[0], right[0]),
        min(left[1], right[1]),
        max(left[2], right[2]),
        max(left[3], right[3]),
    ]


PAGE_EDGE_TOLERANCE_PT = 2.0


def bleeds_off_page(bbox, page_width: float, page_height: float, tolerance: float = PAGE_EDGE_TOLERANCE_PT) -> bool:
    """Whether ``bbox`` reaches two opposite edges of the page: page decoration, not a figure.

    Figures sit inside the text block, clear of the margins. A picture or panel
    running from the top edge to the bottom edge (the tinted half of a back
    cover) or from the left edge to the right edge (a chapter-opener band) is
    the page's background; text printed over it is the page's text, not a
    figure's labels.
    """
    x0, y0, x1, y1 = (float(value) for value in bbox)
    spans_height = y0 <= tolerance and y1 >= page_height - tolerance
    spans_width = x0 <= tolerance and x1 >= page_width - tolerance
    return spans_height or spans_width
