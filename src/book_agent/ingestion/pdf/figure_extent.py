"""The page region a figure really occupies, and a check of a figure region.

A vector figure is painted as many paths: the large shapes that make it
recognisable as a figure (panels, circles, long arrows) and many small ones
(token boxes, arrow heads, bars) plus text labels placed on or beside the
shapes. Its region is everything that belongs to the drawing and nothing
else: every path connected to it, every label inside it or touching it, but
not the page's running header or folio in the margin band, not the caption
and not the body text above or below.

The functions here are pure geometry over ``page.get_drawings()`` rects and
``page.get_text("dict")`` lines so they can be tested without a PDF.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from book_agent.domain.structure.geometry import bleeds_off_page

Bbox = tuple[float, float, float, float]

# Paths and labels closer than this to a figure's drawing belong to it: an
# arrow head sits a few points off its bar, a label a few points off its box.
FIGURE_JOIN_GAP_PT = 12.0
# The region is padded by this much so strokes and glyph edges are not
# clipped, but never into text that is not part of the figure.
FIGURE_REGION_PAD_PT = 3.0
# Running headers, folios and footers live in these bands of the page.
MARGIN_BAND_RATIO = 0.08
# Body text: a block whose widest line spans this much of the page (a
# single line: the larger ratio), or a block of several lines set in the
# page's body font size spanning the smaller ratio. A figure's title, a
# one-line note or a callout in a smaller face stays a label.
PROSE_LINE_WIDTH_RATIO = 0.6
PROSE_SINGLE_LINE_WIDTH_RATIO = 0.7
PROSE_BODY_SIZE_LINE_WIDTH_RATIO = 0.35
_BODY_SIZE_TOLERANCE_PT = 0.3

_CAPTION_LEAD = re.compile(
    r"^\s*(?:figure|fig\.?|table|tab\.?|listing|exhibit)\s*"
    r"(?:\d+(?:[.\-–—]\d+)*|(?-i:[A-Z])(?:[.\-–—]\d+)+)[A-Za-z]?\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PageTextLine:
    bbox: Bbox
    block_index: int
    text: str
    caption: bool
    """The line belongs to a text block that starts like a figure/table caption."""
    prose: bool
    """The line belongs to a text block of body prose (it has a full-width line)."""
    margin: bool
    """The line lies in the page's top or bottom margin band (header/footer)."""


@dataclass
class VectorFigureExtent:
    region: Bbox
    """Drawing plus labels, padded; what a crop of the figure should show."""
    drawing_bbox: Bbox
    """Union of the figure's paths only."""
    path_indices: list[int] = field(default_factory=list)
    image_indices: list[int] = field(default_factory=list)
    label_line_indices: list[int] = field(default_factory=list)


def split_extended_drawings(
    extended: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[Bbox | None]]:
    """Paths of ``page.get_drawings(extended=True)`` and their visible rects.

    A path's ``rect`` covers all its points, but a clip can hide part of it:
    grid cells drawn as long strips and clipped to one row reach far below
    the figure. The visible rect is ``rect`` cut to the clips in force (clips
    at a lower nesting level than the path); ``None`` when fully clipped.
    The paths come in the same order as ``page.get_drawings()``.
    """
    paths: list[dict[str, Any]] = []
    visible: list[Bbox | None] = []
    clips: dict[int, Bbox] = {}
    for item in extended:
        kind = item.get("type")
        level = int(item.get("level") or 0)
        if kind == "clip":
            scissor = item.get("scissor")
            for deeper in [key for key in clips if key >= level]:
                del clips[deeper]
            if scissor is not None:
                clips[level] = (float(scissor[0]), float(scissor[1]), float(scissor[2]), float(scissor[3]))
            continue
        if kind == "group":
            continue
        paths.append(item)
        rect = item.get("rect")
        if rect is None:
            visible.append(None)
            continue
        box: Bbox | None = (float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))
        for clip_level, clip in clips.items():
            if clip_level >= level or box is None:
                continue
            box = _intersection(box, clip)
        visible.append(box)
    return paths, visible


def _intersection(a: Bbox, b: Bbox) -> Bbox | None:
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    if x1 < x0 or y1 < y0:
        return None
    return (x0, y0, x1, y1)


# A font's ascender and descender as a share of its size; beyond these the font's metrics are broken.
_MAX_ASCENDER = 1.5
_MIN_DESCENDER = -1.0


def _glyph_height_bbox(bbox: Bbox, spans: Sequence[dict[str, Any]]) -> Bbox:
    """The line's box, its height cut to its glyphs when a font's metrics are broken.

    PyMuPDF sizes a line by its fonts' ascender and descender. Fonts an OCR tool embeds may carry
    nonsense ones (The Wall Street Gang: ascender 3.06), which made a 7pt line 27pt tall, reaching
    into the figure above it. Such a span is boxed from its baseline instead: 0.9 of its size above,
    0.25 below.
    """
    tops: list[float] = []
    bottoms: list[float] = []
    for span in spans:
        if not str(span.get("text") or "").strip():
            continue
        try:
            size = float(span.get("size") or 0.0)
            ascender = float(span.get("ascender") if span.get("ascender") is not None else 0.8)
            descender = float(span.get("descender") if span.get("descender") is not None else -0.2)
            span_box = [float(value) for value in span.get("bbox") or ()]
            baseline = float((span.get("origin") or (0.0, span_box[3] if span_box else 0.0))[1])
        except (TypeError, ValueError, IndexError):
            return bbox
        if len(span_box) != 4:
            return bbox
        if size > 0 and (ascender > _MAX_ASCENDER or descender < _MIN_DESCENDER):
            tops.append(baseline - 0.9 * size)
            bottoms.append(baseline + 0.25 * size)
        else:
            tops.append(span_box[1])
            bottoms.append(span_box[3])
    if not tops:
        return bbox
    top, bottom = max(bbox[1], min(tops)), min(bbox[3], max(bottoms))
    return (bbox[0], top, bbox[2], bottom) if bottom > top else bbox


def page_text_lines(
    text_dict: dict[str, Any], page_width: float, page_height: float, *, glyph_heights: bool = False
) -> list[PageTextLine]:
    """Non-empty text lines of a ``page.get_text("dict")`` result, classified.

    ``glyph_heights``: a line set in a font with broken metrics is as tall as its glyphs
    (``_glyph_height_bbox``), for checking a figure's edge against the text around it.
    """
    blocks: list[tuple[int, list[tuple[Bbox, str]], float]] = []
    size_weights: dict[float, int] = {}
    for block_index, block in enumerate(text_dict.get("blocks", []) or []):
        if block.get("type") != 0:
            continue
        block_lines: list[tuple[Bbox, str]] = []
        line_sizes: list[dict[float, int]] = []
        for line in block.get("lines", []) or []:
            spans = line.get("spans", []) or []
            text = "".join(str(span.get("text") or "") for span in spans)
            if not text.strip():
                continue
            try:
                bbox = tuple(float(value) for value in line.get("bbox", (0, 0, 0, 0)))
            except (TypeError, ValueError):
                continue
            if len(bbox) != 4 or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                continue
            if glyph_heights:
                bbox = _glyph_height_bbox(bbox, spans)
            block_lines.append((bbox, text))  # type: ignore[arg-type]
            sizes: dict[float, int] = {}
            for span in spans:
                size = round(float(span.get("size") or 0.0), 1)
                chars = len(str(span.get("text") or "").strip())
                sizes[size] = sizes.get(size, 0) + chars
                size_weights[size] = size_weights.get(size, 0) + chars
            line_sizes.append(sizes)
        # A caption set beside a figure can share a text block with the
        # figure's last label; the caption starts at its own line.
        split = next((index for index in range(1, len(block_lines)) if starts_figure_caption(block_lines[index][1])), None)
        parts = [(0, len(block_lines))] if split is None else [(0, split), (split, len(block_lines))]
        for start, end in parts:
            if start == end:
                continue
            block_sizes: dict[float, int] = {}
            for sizes in line_sizes[start:end]:
                for size, chars in sizes.items():
                    block_sizes[size] = block_sizes.get(size, 0) + chars
            dominant = max(block_sizes, key=block_sizes.__getitem__) if block_sizes else 0.0
            blocks.append((block_index, block_lines[start:end], dominant))
    # The body font size is the size of the page's full-width paragraphs; a
    # page of small-print figure text must not make that text "body".
    wide_sizes: dict[float, int] = {}
    for _index, block_lines, dominant in blocks:
        if len(block_lines) > 1 and max(b[2] - b[0] for b, _t in block_lines) >= page_width * PROSE_LINE_WIDTH_RATIO:
            wide_sizes[dominant] = wide_sizes.get(dominant, 0) + len(block_lines)
    weights = wide_sizes or size_weights
    body_size = max(weights, key=weights.__getitem__) if weights else 0.0

    lines: list[PageTextLine] = []
    for block_index, block_lines, dominant in blocks:
        caption = bool(_CAPTION_LEAD.match(block_lines[0][1]))
        widest = max(bbox[2] - bbox[0] for bbox, _text in block_lines)
        body_sized = abs(dominant - body_size) <= _BODY_SIZE_TOLERANCE_PT
        if len(block_lines) == 1:
            prose = widest >= page_width * PROSE_SINGLE_LINE_WIDTH_RATIO or (
                body_sized and _is_sentence(block_lines[0][1])
            )
        else:
            prose = widest >= page_width * PROSE_LINE_WIDTH_RATIO or (
                body_sized and widest >= page_width * PROSE_BODY_SIZE_LINE_WIDTH_RATIO
            )
        for bbox, text in block_lines:
            lines.append(
                PageTextLine(
                    bbox=bbox,
                    block_index=block_index,
                    text=text,
                    caption=caption,
                    prose=prose,
                    margin=in_margin_band(bbox, page_height),
                )
            )
    return lines


# A figure caption: "Figure 5.4 Although ..." (a capital after the number; "Figure 6.2 summarizes
# these drawbacks." in running text is a reference, not a caption).
_FIGURE_CAPTION_START = re.compile(
    r"^\s*fig(?:ure|\.)\s*(?:\d+(?:[.\-–—]\d+)*|(?-i:[A-Z])(?:[.\-–—]\d+)+)[A-Za-z]?"
    r"(?:[.:\-–—]?\s+(?-i:[A-Z(\"“‘'])|\s*$)",
    re.IGNORECASE,
)


def starts_figure_caption(text: str) -> bool:
    """Whether a line starts a figure caption ("Figure 9.2 Four specialist agents ...")."""
    return bool(_FIGURE_CAPTION_START.match(text))


def _is_sentence(text: str) -> bool:
    words = text.split()
    return len(words) >= 8 and text.rstrip().endswith((".", "?", "!", ":"))


def in_margin_band(bbox: Bbox, page_height: float) -> bool:
    band = page_height * MARGIN_BAND_RATIO
    return bbox[3] <= band or bbox[1] >= page_height - band


def vector_figure_extents(
    path_bboxes: Sequence[Bbox],
    *,
    seed: Callable[[Bbox], bool],
    figure_sized: Callable[[Bbox], bool] = lambda _bbox: True,
    is_background: Callable[[list[int]], bool] | None = None,
    solid: Sequence[bool] | None = None,
    image_bboxes: Sequence[Bbox] = (),
    text_lines: Sequence[PageTextLine] = (),
    page_width: float,
    page_height: float,
    join_gap: float = FIGURE_JOIN_GAP_PT,
    pad: float = FIGURE_REGION_PAD_PT,
) -> list[VectorFigureExtent]:
    """Group a page's paths into figures and measure each figure's full region.

    Paths within ``join_gap`` of each other form one drawing. Text lines
    inside the drawing, or within ``join_gap`` of it or of another label, are
    its labels unless they are a caption, body prose or margin-band
    furniture. Drawings that share labels, come within ``join_gap`` or are
    panels of one figure merge.

    A drawing is a figure when its large shapes (``seed``) together are
    ``figure_sized``, or when it is a mosaic of small solid shapes or image
    tiles that is ``figure_sized`` as a whole. A lone connector line or a few
    arrow heads (a code listing's annotations) are not.

    ``is_background`` names path groups that are only the tinted panel behind
    text (a sidebar); they are left out before anything joins them.
    ``solid`` marks closed shapes (filled, rectangles) as opposed to open
    strokes such as connector lines.

    Embedded images take part like paths, so a raster picture's axis labels,
    legend swatches and callouts join it into one region. A figure-sized
    picture is a figure by itself; it gets a region here only when something
    joined it (a picture alone is its own image block). An inline icon does
    not turn the text beside it into a figure.

    A drawing with no large shape (hairline arrows, small icons) is a figure
    when a figure caption claims it (``_captioned_drawings``). Text between a
    figure and its caption is the figure's (``_take_text_up_to_caption``).
    """
    path_count = len(path_bboxes)
    # An image set inside a line of text (an emoji, an icon glyph) is part of
    # that text, not a piece of a figure.
    kept_images = [index for index, bbox in enumerate(image_bboxes) if not _is_inline_glyph(bbox, text_lines)]
    all_bboxes = [*path_bboxes, *(image_bboxes[index] for index in kept_images)]
    candidates = [index for index, bbox in enumerate(all_bboxes) if _is_candidate_path(bbox, page_width, page_height)]
    if not candidates:
        return []
    components = _connected_components([all_bboxes[index] for index in candidates], join_gap)
    groups: list[_Group] = []
    for component in components:
        indices = [candidates[local] for local in component]
        paths_only = [index for index in indices if index < path_count]
        if is_background is not None and len(paths_only) == len(indices) and is_background(paths_only):
            continue  # the tinted panel behind a sidebar's text, not a drawing
        containers = [
            all_bboxes[index]
            for index in indices
            if (index >= path_count or solid is None or solid[index])
            and min(all_bboxes[index][2] - all_bboxes[index][0], all_bboxes[index][3] - all_bboxes[index][1])
            >= _MIN_SOLID_PIECE_SIDE_PT
        ]
        groups.append(
            _Group(paths=indices, drawing=_union(all_bboxes[index] for index in indices), containers=containers)
        )

    changed = True
    while changed:
        changed = False
        for group in groups:
            if _absorb_labels(group, text_lines, join_gap):
                changed = True
        merged = _merge_touching_groups(groups, join_gap, text_lines)
        if len(merged) != len(groups):
            groups = merged
            changed = True

    figures: list[_Group] = []
    unclaimed: list[_Group] = []
    for group in groups:
        paths = [index for index in group.paths if index < path_count]
        pictures = [index for index in group.paths if index >= path_count and seed(all_bboxes[index])]
        if pictures:
            if not paths and not group.labels:
                continue
        else:
            seeds = [all_bboxes[index] for index in paths if seed(all_bboxes[index])]
            if not (seeds and figure_sized(_union(seeds))) and not (
                _assembled_from_pieces(group, all_bboxes, path_count, solid) and figure_sized(group.drawing)
            ):
                unclaimed.append(group)
                continue
        figures.append(group)
    figures.extend(
        _captioned_drawings(
            unclaimed,
            figures,
            text_lines,
            figure_sized=lambda bbox: figure_sized(bbox) or _caption_claim_sized(bbox),
            join_gap=join_gap,
            page_height=page_height,
        )
    )
    _take_text_up_to_caption(figures, text_lines)

    extents: list[VectorFigureExtent] = []
    for group in figures:
        extent = group.extent(text_lines)
        region = _pad_clear_of_text(extent, pad, text_lines, set(group.labels), page_width, page_height)
        extents.append(
            VectorFigureExtent(
                region=region,
                drawing_bbox=group.drawing,
                path_indices=sorted(index for index in group.paths if index < path_count),
                image_indices=sorted(kept_images[index - path_count] for index in group.paths if index >= path_count),
                label_line_indices=sorted(group.labels),
            )
        )
    return extents


# A drawing a figure caption claims is a figure when it is at least this long on one side and this
# thick on the other: a flat row of boxes and arrows ("Documents → Chunks → …") falls under the
# general size bar, a lone arrow does not reach this one.
CAPTION_CLAIM_MIN_LONG_PT = 100.0
CAPTION_CLAIM_MIN_SHORT_PT = 20.0
# A side caption (set beside its figure, as Manning does for narrow figures) is this close to it.
SIDE_CAPTION_GAP_PT = 60.0


def _caption_claim_sized(bbox: Bbox) -> bool:
    width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    return max(width, height) >= CAPTION_CLAIM_MIN_LONG_PT and min(width, height) >= CAPTION_CLAIM_MIN_SHORT_PT


def _beside_caption(extent: Bbox, caption: Bbox) -> bool:
    """A drawing to the left of a side caption: they overlap vertically and nothing wide separates them."""
    overlaps = extent[1] < caption[3] + 6.0 and extent[3] > caption[1] - 6.0
    return overlaps and extent[2] <= caption[0] + 2.0 and caption[0] - extent[2] <= SIDE_CAPTION_GAP_PT


# A caption with no figure within this distance has not found its drawing yet.
FIGURE_CAPTION_GAP_PT = 36.0
# A figure's title or legend can sit between its drawing and its caption: up to
# this far, with nothing but such lines in between.
FIGURE_TITLE_GAP_PT = 60.0


def _captioned_drawings(
    unclaimed: list[_Group],
    figures: Sequence[_Group],
    text_lines: Sequence[PageTextLine],
    *,
    figure_sized: Callable[[Bbox], bool],
    join_gap: float,
    page_height: float,
) -> list[_Group]:
    """Figures drawn without a large shape, found through their caption.

    A diagram drawn only in hairlines (arrows between labels, a number line
    with tick marks, two outlined boxes) or from small icons has no shape
    large enough to count as a figure by itself, and a few arrows beside a
    code listing must not count as one. A figure caption says a figure is
    there: the drawings between the caption and the body text (or other
    caption) above it, or else below it, are that figure together with their
    labels, when together they are figure-sized. Drawings that cross a line
    of body text are not part of it.
    """
    if not unclaimed:
        return []
    found: list[_Group] = []
    claimed = list(figures)
    remaining = list(unclaimed)
    for block_index, caption in _figure_captions(text_lines):
        if not remaining:
            break
        if any(_gap(group.extent(text_lines), caption) <= FIGURE_CAPTION_GAP_PT for group in claimed):
            continue
        for side in ("above", "below", "beside"):
            if side == "beside":
                # A side caption: the drawings to its left that it overlaps vertically.
                band = (0.0, page_height)
                placed = lambda extent: _beside_caption(extent, caption)  # noqa: E731
            else:
                band = _caption_band(caption, block_index, text_lines, above=side == "above", page_height=page_height)
                if band is None:
                    continue
                placed = lambda extent, band=band: _within_band(extent, band)  # noqa: E731
            members = [
                group
                for group in remaining
                if placed(group.extent(text_lines))
                and not _intersects(group.extent(text_lines), caption)
                and not any(
                    line.prose and index not in group.labels and _intersects(group.drawing, line.bbox)
                    for index, line in enumerate(text_lines)
                )
            ]
            if not members:
                continue
            figure = _Group(
                paths=[index for group in members for index in group.paths],
                drawing=_union(group.drawing for group in members),
                labels=set().union(*(group.labels for group in members)),
                containers=[box for group in members for box in group.containers],
            )
            # The band is the figure's space: text in it over the drawing's width
            # is its labels (a title above the first arrow, a note beside an axis),
            # however far from a stroke.
            span = _union(group.extent(text_lines) for group in members)
            if side == "beside":
                band = (span[1] - join_gap, span[3] + join_gap)
            for index, line in enumerate(text_lines):
                if line.prose or line.caption or line.margin or not _within_band(line.bbox, band):
                    continue
                if line.bbox[0] < span[2] and line.bbox[2] > span[0]:
                    figure.labels.add(index)
            _absorb_labels(figure, text_lines, join_gap)
            if not figure_sized(figure.extent(text_lines)):
                continue
            found.append(figure)
            claimed.append(figure)
            remaining = [group for group in remaining if all(group is not member for member in members)]
            break
    return found


def _figure_captions(text_lines: Sequence[PageTextLine]) -> list[tuple[int, Bbox]]:
    """(block index, box) of each figure caption on the page, top to bottom."""
    captions: dict[int, list[PageTextLine]] = {}
    for line in text_lines:
        if line.caption and not line.margin:
            captions.setdefault(line.block_index, []).append(line)
    found = [
        (block_index, _union(line.bbox for line in lines))
        for block_index, lines in captions.items()
        if starts_figure_caption(lines[0].text)
    ]
    return sorted(found, key=lambda item: item[1][1])


def _take_text_up_to_caption(figures: Sequence[_Group], text_lines: Sequence[PageTextLine]) -> None:
    """Give a figure the text between its drawing and its caption (a title set under the drawing).

    Nothing but the figure sits between a figure and its caption: a line there
    that is not body text, over the figure's width, belongs to the figure
    however far it is from a stroke.
    """
    for _block_index, caption in _figure_captions(text_lines):
        best: tuple[float, _Group, Bbox] | None = None
        for group in figures:
            extent = group.extent(text_lines)
            if extent[0] >= caption[2] or extent[2] <= caption[0]:
                continue
            gap = caption[1] - extent[3] if extent[3] <= caption[1] + 1.0 else extent[1] - caption[3]
            if 0.0 <= gap <= FIGURE_TITLE_GAP_PT and (best is None or gap < best[0]):
                best = (gap, group, extent)
        if best is None:
            continue
        _gap_size, group, extent = best
        top, bottom = (extent[3], caption[1]) if extent[3] <= caption[1] + 1.0 else (caption[3], extent[1])
        between = [
            index
            for index, line in enumerate(text_lines)
            if index not in group.labels
            and line.bbox[1] >= top - 1.0
            and line.bbox[3] <= bottom + 1.0
            and line.bbox[0] < extent[2]
            and line.bbox[2] > extent[0]
        ]
        # Body text or another caption in between: the caption is not this figure's.
        if any(text_lines[index].prose or text_lines[index].caption or text_lines[index].margin for index in between):
            continue
        group.labels.update(
            index for index in between if text_lines[index].bbox[0] >= extent[0] - 1.0 and text_lines[index].bbox[2] <= extent[2] + 1.0
        )


def _caption_band(
    caption: Bbox,
    caption_block: int,
    text_lines: Sequence[PageTextLine],
    *,
    above: bool,
    page_height: float,
) -> tuple[float, float] | None:
    """The vertical span from the nearest body text or caption above ``caption`` down to its bottom (or up).

    The span keeps the caption's own lines, so a caption set beside its figure,
    level with the figure's bottom (or top), bounds it too. Only lines
    overlapping the caption horizontally bound the span: body text in a
    column beside a side-by-side figure does not.
    """
    band = page_height * MARGIN_BAND_RATIO
    limit = band if above else page_height - band
    for line in text_lines:
        if line.block_index == caption_block or not (line.prose or line.caption or line.margin):
            continue
        if line.bbox[0] >= caption[2] or line.bbox[2] <= caption[0]:
            continue
        if above and line.bbox[3] <= caption[1] + 1.0:
            limit = max(limit, line.bbox[3])
        elif not above and line.bbox[1] >= caption[3] - 1.0:
            limit = min(limit, line.bbox[1])
    top, bottom = (limit, caption[3]) if above else (caption[1], limit)
    return (top, bottom) if bottom > top else None


def _within_band(bbox: Bbox, band: tuple[float, float]) -> bool:
    return bbox[1] >= band[0] - 1.0 and bbox[3] <= band[1] + 1.0


def figure_label_blocks(
    block_bboxes: Sequence[Bbox],
    text_lines: Sequence[PageTextLine],
    extents: Sequence[VectorFigureExtent],
) -> list[bool]:
    """For each text block (by its bbox): whether it holds only one figure's labels.

    A block is a figure's labels when every text line in it (by the line's
    center) is a label line of the same measured figure. The measurement
    already told labels from body prose (width, body font size, sentences,
    enclosing shapes), so a block of labels is part of the picture however
    long it is or however much its joined text reads like a sentence. A block
    that mixes labels with a line of body text is not.
    """
    owner: dict[int, int] = {}
    for number, extent in enumerate(extents):
        for index in extent.label_line_indices:
            owner.setdefault(index, number)
    result: list[bool] = []
    for bbox in block_bboxes:
        members = [
            index
            for index, line in enumerate(text_lines)
            if _contains(bbox, _center_box(line.bbox), tolerance=0.5)
        ]
        owners = {owner.get(index) for index in members}
        result.append(bool(members) and None not in owners and len(owners) == 1)
    return result


def _center_box(bbox: Bbox) -> Bbox:
    x = (bbox[0] + bbox[2]) / 2
    y = (bbox[1] + bbox[3]) / 2
    return (x, y, x, y)


def _is_inline_glyph(bbox: Bbox, text_lines: Sequence[PageTextLine]) -> bool:
    """An image no taller than a text line, sitting on or against that line."""
    height = bbox[3] - bbox[1]
    for line in text_lines:
        line_height = line.bbox[3] - line.bbox[1]
        if height > line_height * 1.6:
            continue
        vertical = min(bbox[3], line.bbox[3]) - max(bbox[1], line.bbox[1])
        if vertical < height * 0.6:
            continue
        if max(line.bbox[0] - bbox[2], bbox[0] - line.bbox[2]) <= 3.0:
            return True
    return False


# A figure drawn without any large shape is a mosaic of image tiles or of
# many small solid shapes (nodes of a tree, cells of a grid) that together
# cover as much as one figure-sized shape. Rules (underlines, table borders,
# the lines of a code listing's annotations) are thin and arrow heads are
# tiny, so neither a run of underlined links nor annotation arrows qualify.
_MIN_SOLID_PIECES = 6
_MIN_SOLID_PIECE_SIDE_PT = 2.0
_MIN_SOLID_PIECES_AREA_PT2 = 1800.0


def _assembled_from_pieces(
    group: "_Group",
    bboxes: Sequence[Bbox],
    path_count: int,
    solid: Sequence[bool] | None,
) -> bool:
    if any(index >= path_count for index in group.paths):
        return True
    solid_areas = [
        (bboxes[index][2] - bboxes[index][0]) * (bboxes[index][3] - bboxes[index][1])
        for index in group.paths
        if (solid is None or solid[index])
        and min(bboxes[index][2] - bboxes[index][0], bboxes[index][3] - bboxes[index][1]) >= _MIN_SOLID_PIECE_SIDE_PT
    ]
    return len(solid_areas) >= _MIN_SOLID_PIECES and sum(solid_areas) >= _MIN_SOLID_PIECES_AREA_PT2


@dataclass
class _Group:
    paths: list[int]
    drawing: Bbox
    labels: set[int] = field(default_factory=set)
    containers: list[Bbox] = field(default_factory=list)
    """Closed shapes and pictures of the group: what can enclose text."""

    def extent(self, text_lines: Sequence[PageTextLine]) -> Bbox:
        return _union([self.drawing, *(text_lines[index].bbox for index in self.labels)])


def _is_candidate_path(bbox: Bbox, page_width: float, page_height: float) -> bool:
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    if width < 0 or height < 0 or (width < 0.5 and height < 0.5):
        return False
    if width >= page_width * 0.9 and height >= page_height * 0.9:
        return False  # page background
    if width >= page_width * 0.92 and height <= page_height * 0.035:
        return False  # full-width rule
    if width >= page_width * 0.7 and height <= 1.0:
        return False  # a hairline across the text block: a section's rule ("about the author"), not a figure's
    if height >= page_height * 0.92 and width <= page_width * 0.035:
        return False  # full-height rule
    if bleeds_off_page(bbox, page_width, page_height):
        return False  # a panel printed to the page edges: the page's background
    return not in_margin_band(bbox, page_height)


def _absorb_labels(group: _Group, text_lines: Sequence[PageTextLine], join_gap: float) -> bool:
    """Add the text lines that belong to the drawing; True when any was added."""
    added = False
    drawing = group.drawing
    for index, line in enumerate(text_lines):
        if index in group.labels or line.caption:
            continue
        inside = _contains(drawing, line.bbox, tolerance=1.0)
        if inside:
            if line.margin and not _contains(drawing, line.bbox, tolerance=0.0):
                continue
            # Body text inside the drawing's box belongs to it only when a
            # shape of the drawing encloses it (a text panel of a diagram),
            # not when it merely flows beside a picture.
            if line.prose and not any(_contains(box, line.bbox, tolerance=1.0) for box in group.containers):
                continue
        else:
            if line.prose or line.margin:
                continue
            # A label sits next to the drawing, or next to another label
            # (a description under a column heading, a legend's second row).
            near_drawing = _gap(drawing, line.bbox) <= join_gap
            beside_label = any(_gap(text_lines[label].bbox, line.bbox) <= join_gap for label in group.labels)
            if not (near_drawing or beside_label):
                continue
        group.labels.add(index)
        added = True
    return added


def _merge_touching_groups(
    groups: list[_Group],
    join_gap: float,
    text_lines: Sequence[PageTextLine] = (),
) -> list[_Group]:
    """Merge drawings that touch, share a label, or are panels of one figure.

    Panels (sub-diagrams side by side or stacked) sit up to
    ``FIGURE_PANEL_GAP_PT`` apart with nothing but blank space between them;
    two figures that close would have a caption or body text in between.
    """
    merged = list(groups)
    index = 0
    while index < len(merged):
        other = index + 1
        while other < len(merged):
            left, right = merged[index], merged[other]
            if (
                _gap(left.drawing, right.drawing) <= join_gap
                or left.labels & right.labels
                or _are_panels_of_one_figure(left, right, text_lines)
            ):
                merged[index] = _Group(
                    paths=[*left.paths, *right.paths],
                    drawing=_union([left.drawing, right.drawing]),
                    labels=left.labels | right.labels,
                    containers=[*left.containers, *right.containers],
                )
                merged.pop(other)
                other = index + 1
                continue
            other += 1
        index += 1
    return merged


# Panels of one figure are at most this far apart (half an inch of blank
# space; separate figures are kept apart by their captions).
FIGURE_PANEL_GAP_PT = 36.0


def _are_panels_of_one_figure(left: _Group, right: _Group, text_lines: Sequence[PageTextLine]) -> bool:
    # A panel has shapes or a picture; a bare rule (under a heading, above a
    # footnote) is not a panel and must not pull a picture into a figure.
    if not left.containers or not right.containers:
        return False
    left_box, right_box = left.extent(text_lines), right.extent(text_lines)
    if _gap(left_box, right_box) > FIGURE_PANEL_GAP_PT:
        return False
    between = (
        min(left_box[2], right_box[2]),
        min(left_box[3], right_box[3]),
        max(left_box[0], right_box[0]),
        max(left_box[1], right_box[1]),
    )
    # ``between`` spans the gap along each axis where the boxes are apart and
    # their overlap along an axis where they overlap.
    gap_box = (
        min(between[0], between[2]),
        min(between[1], between[3]),
        max(between[0], between[2]),
        max(between[1], between[3]),
    )
    members = left.labels | right.labels
    return not any(
        index not in members and _intersects(gap_box, line.bbox) for index, line in enumerate(text_lines)
    )


def _pad_clear_of_text(
    bbox: Bbox,
    pad: float,
    text_lines: Sequence[PageTextLine],
    members: set[int],
    page_width: float,
    page_height: float,
) -> Bbox:
    """``bbox`` grown by up to ``pad`` on each side without touching outside text."""
    left_pad = top_pad = right_pad = bottom_pad = pad
    for index, line in enumerate(text_lines):
        if index in members:
            continue
        x0, y0, x1, y1 = line.bbox
        overlaps_x = x0 < bbox[2] and x1 > bbox[0]
        overlaps_y = y0 < bbox[3] and y1 > bbox[1]
        if overlaps_x and y1 <= bbox[1]:
            top_pad = min(top_pad, max(0.0, (bbox[1] - y1) / 2))
        if overlaps_x and y0 >= bbox[3]:
            bottom_pad = min(bottom_pad, max(0.0, (y0 - bbox[3]) / 2))
        if overlaps_y and x1 <= bbox[0]:
            left_pad = min(left_pad, max(0.0, (bbox[0] - x1) / 2))
        if overlaps_y and x0 >= bbox[2]:
            right_pad = min(right_pad, max(0.0, (x0 - bbox[2]) / 2))
    return (
        max(0.0, bbox[0] - left_pad),
        max(0.0, bbox[1] - top_pad),
        min(page_width, bbox[2] + right_pad),
        min(page_height, bbox[3] + bottom_pad),
    )


def _connected_components(bboxes: Sequence[Bbox], gap: float) -> list[list[int]]:
    """Indices of ``bboxes`` grouped by transitive proximity (≤ ``gap``)."""
    parent = list(range(len(bboxes)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    cell = 64.0
    buckets: dict[tuple[int, int], list[int]] = {}
    for index, bbox in enumerate(bboxes):
        x0, y0 = int((bbox[0] - gap) // cell), int((bbox[1] - gap) // cell)
        x1, y1 = int((bbox[2] + gap) // cell), int((bbox[3] + gap) // cell)
        seen: set[int] = set()
        for cx in range(x0, x1 + 1):
            for cy in range(y0, y1 + 1):
                bucket = buckets.setdefault((cx, cy), [])
                for other in bucket:
                    if other in seen:
                        continue
                    seen.add(other)
                    if _gap(bbox, bboxes[other]) <= gap:
                        root_a, root_b = find(index), find(other)
                        if root_a != root_b:
                            parent[root_a] = root_b
                bucket.append(index)
    components: dict[int, list[int]] = {}
    for index in range(len(bboxes)):
        components.setdefault(find(index), []).append(index)
    return list(components.values())


def _intersects(a: Bbox, b: Sequence[float]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _gap(a: Bbox, b: Bbox) -> float:
    """Largest axis gap between two boxes; 0 when they overlap or touch."""
    return max(a[0] - b[2], b[0] - a[2], a[1] - b[3], b[1] - a[3], 0.0)


def _contains(outer: Bbox, inner: Bbox, *, tolerance: float) -> bool:
    return (
        inner[0] >= outer[0] - tolerance
        and inner[1] >= outer[1] - tolerance
        and inner[2] <= outer[2] + tolerance
        and inner[3] <= outer[3] + tolerance
    )


def _union(bboxes: Iterable[Bbox]) -> Bbox:
    items = list(bboxes)
    return (
        min(bbox[0] for bbox in items),
        min(bbox[1] for bbox in items),
        max(bbox[2] for bbox in items),
        max(bbox[3] for bbox in items),
    )


# ---------------------------------------------------------------------------
# Completing a region


def complete_figure_region(
    page: Any,
    region: Sequence[float],
    *,
    max_rounds: int = 8,
    touch_gap: float = 2.0,
) -> Bbox:
    """``region`` grown to the drawing it cuts through: every path that crosses or touches its edge,
    and the labels (short lines, not sentences) that then cross it, round after round.

    A path or label whose taking would bring in body prose, a caption or the page's header or footer
    is left out, so a figure never grows over the text around it. Returns ``region`` unchanged when
    nothing is cut.
    """
    page_width = float(page.rect.width)
    page_height = float(page.rect.height)
    lines = page_text_lines(page.get_text("dict"), page_width, page_height, glyph_heights=True)
    try:
        _paths, visible = split_extended_drawings(page.get_drawings(extended=True))
    except Exception:
        visible = []
    paths = [bbox for bbox in visible if bbox is not None and _is_candidate_path(bbox, page_width, page_height)]
    box: Bbox = tuple(float(value) for value in region)  # type: ignore[assignment]

    def forbidden(line: PageTextLine) -> bool:
        text = " ".join(line.text.split())
        lead_in = text.endswith(":") and len(text.split()) >= 5
        return line.caption or line.margin or line.prose or _is_sentence(line.text) or lead_in

    # A region that is one glyph (a formula the parser boxed by a single character) reaches the
    # formula's other lines a little further off.
    line_gap = touch_gap if (box[2] - box[0]) * (box[3] - box[1]) >= 200.0 else FIGURE_JOIN_GAP_PT

    def takes_forbidden(candidate: Bbox, current: Bbox) -> bool:
        grown = _union([current, candidate])
        return any(
            forbidden(line) and _intersects(grown, line.bbox) and not _intersects(current, line.bbox)
            for line in lines
        )

    for _ in range(max_rounds):
        zone: Bbox = (box[0] - touch_gap, box[1] - touch_gap, box[2] + touch_gap, box[3] + touch_gap)
        grown = box
        for bbox in paths:
            if _intersects(zone, bbox) and not _contains(grown, bbox, tolerance=1.0) and not takes_forbidden(bbox, grown):
                grown = _union([grown, bbox])
        line_zone: Bbox = (box[0] - line_gap, box[1] - line_gap, box[2] + line_gap, box[3] + line_gap)
        for line in lines:
            if forbidden(line) or not _intersects(line_zone, line.bbox) or _contains(grown, line.bbox, tolerance=1.0):
                continue
            if not takes_forbidden(line.bbox, grown):
                grown = _union([grown, line.bbox])
        if grown == box:
            break
        box = grown
    return _clear_of_outside_prose(box, lines, paths, forbidden)


# Past the check's touch gap (``figure_region_problems``), so the moved edge no longer touches the line.
_EDGE_CLEARANCE_PT = 2.5


def _clear_of_outside_prose(
    box: Bbox,
    lines: Sequence[PageTextLine],
    paths: Sequence[Bbox],
    forbidden: Callable[[PageTextLine], bool],
) -> Bbox:
    """``box`` with its top or bottom edge moved off a line of the text around it.

    A scanned picture's box can reach a few points into the line of body text above or below it
    (The Wall Street Gang's charts): the crop would carry a slice of that line. A line that sits
    mostly outside the box and runs past its side is the page's text, as is any line that only
    grazes the edge; the edge moves to clear it, unless that would cut a drawing or take more than
    a line's height off the figure.
    """
    x0, y0, x1, y1 = box
    for line in lines:
        zone = (x0, y0 - _EDGE_CLEARANCE_PT, x1, y1 + _EDGE_CLEARANCE_PT)
        if not _intersects(zone, line.bbox):
            continue
        lx0, ly0, lx1, ly1 = line.bbox
        height = ly1 - ly0
        if height <= 0:
            continue
        overlap = min(ly1, y1) - max(ly0, y0)
        # A line that only grazes the edge was not taken into the figure (``complete_figure_region``
        # takes the labels that cross it); deeper, only the page's text running past its side.
        grazes = overlap < 0.25 * height
        page_text = forbidden(line) and (lx0 < x0 - 4.0 or lx1 > x1 + 4.0) and overlap < 0.75 * height
        if not (grazes or page_text):
            continue
        if ly0 < y0 + _EDGE_CLEARANCE_PT and ly1 <= y0 + height:
            band = (x0, y0, x1, ly1)
            if not any(_intersects(band, path) for path in paths):
                y0 = ly1 + _EDGE_CLEARANCE_PT
        elif y1 - height <= ly0 and y1 - _EDGE_CLEARANCE_PT < ly1:
            band = (x0, ly0, x1, y1)
            if not any(_intersects(band, path) for path in paths):
                y1 = ly0 - _EDGE_CLEARANCE_PT
    return (x0, y0, x1, y1) if y1 - y0 > 10.0 else box


def is_printed_text_patch(page: Any, region: Sequence[float]) -> bool:
    """``region`` is a piece of the page's printed text set as a picture, not a figure.

    A scan turned into text and pictures can leave words of the page as images (The Wall Street
    Gang: three lines of the foreword, a word in a sentence, the contents page's roman numerals)
    while those words are in the text layer as well. Lines run through the picture and past its
    side (a figure's own text stays inside it): two that between them cover 40% of its height, or
    one that covers 60%. Or the picture is small (under 2,500pt², an inline patch, not a figure)
    and holds half of a text line.
    """
    x0, y0, x1, y1 = (float(value) for value in region)
    height = y1 - y0
    if height <= 0 or x1 <= x0:
        return False
    lines = page_text_lines(page.get_text("dict"), float(page.rect.width), float(page.rect.height), glyph_heights=True)
    small = (x1 - x0) * height < 2500.0
    crossing = 0
    covered = 0.0
    for line in lines:
        lx0, ly0, lx1, ly1 = line.bbox
        line_height = ly1 - ly0
        overlap = min(ly1, y1) - max(ly0, y0)
        if line_height <= 0 or overlap <= 0 or lx1 <= x0 or lx0 >= x1 or not line.text.strip():
            continue
        if small:
            inside = (min(lx1, x1) - max(lx0, x0)) * overlap
            if inside >= 0.5 * (lx1 - lx0) * line_height:
                return True
        if overlap < 0.75 * line_height or (lx0 >= x0 - 10.0 and lx1 <= x1 + 10.0):
            continue
        crossing += 1
        covered += overlap
    return (crossing >= 2 and covered >= 0.4 * height) or (crossing >= 1 and covered >= 0.6 * height)


# ---------------------------------------------------------------------------
# Region check


@dataclass(frozen=True)
class FigureRegionProblem:
    kind: str
    """``page_furniture`` (header/folio/footer inside), ``caption`` (the
    caption inside), ``cut_path`` / ``cut_text`` (a path or text line crossing
    or touching the region's edge: part of the figure left outside)."""
    bbox: Bbox
    text: str = ""


def figure_region_problems(
    page: Any,
    region: Sequence[float],
    *,
    furniture_bboxes: Sequence[Bbox] | None = None,
    touch_gap: float = 2.0,
    tolerance: float = 1.0,
) -> list[FigureRegionProblem]:
    """What is wrong with ``region`` as the crop of a figure on ``page``.

    A clean figure region covers every path and text line of the figure and
    nothing of the page around it, so:

    * no running header, folio or footer may intersect it. They are
      ``furniture_bboxes`` when the caller knows them (recovered header/footer
      blocks); otherwise the text lines in the page's margin bands;
    * no caption line may intersect it;
    * every path and text line that intersects it, or comes within
      ``touch_gap`` of it, must lie inside it (within ``tolerance``).
    """
    x0, y0, x1, y1 = (float(value) for value in region)
    box: Bbox = (x0, y0, x1, y1)
    zone: Bbox = (x0 - touch_gap, y0 - touch_gap, x1 + touch_gap, y1 + touch_gap)
    page_width = float(page.rect.width)
    page_height = float(page.rect.height)
    lines = page_text_lines(page.get_text("dict"), page_width, page_height, glyph_heights=True)
    problems: list[FigureRegionProblem] = []

    furniture = list(furniture_bboxes) if furniture_bboxes is not None else [
        line.bbox for line in lines if line.margin
    ]
    for bbox in furniture:
        if _intersects(box, bbox):
            problems.append(FigureRegionProblem("page_furniture", bbox, _text_at(lines, bbox)))
    furniture_set = {tuple(bbox) for bbox in furniture}

    for line in lines:
        if line.bbox in furniture_set or not _intersects(zone, line.bbox):
            continue
        if line.caption:
            if _intersects(box, line.bbox):
                problems.append(FigureRegionProblem("caption", line.bbox, line.text))
            continue
        if not _contains(box, line.bbox, tolerance=tolerance):
            problems.append(FigureRegionProblem("cut_text", line.bbox, line.text))

    try:
        _paths, visible = split_extended_drawings(page.get_drawings(extended=True))
    except Exception:
        visible = []
    for bbox in visible:
        if bbox is None:
            continue
        if not _is_candidate_path(bbox, page_width, page_height) or not _intersects(zone, bbox):
            continue
        if not _contains(box, bbox, tolerance=tolerance):
            problems.append(FigureRegionProblem("cut_path", bbox))
    return problems


def _text_at(lines: Sequence[PageTextLine], bbox: Sequence[float]) -> str:
    return " ".join(line.text.strip() for line in lines if _intersects(tuple(bbox), line.bbox))  # type: ignore[arg-type]
