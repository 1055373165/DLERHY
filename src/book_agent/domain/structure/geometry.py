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
