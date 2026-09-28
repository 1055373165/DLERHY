"""Icon-led callouts: a small icon at the margin and a few lines beside it in a non-body face.

Technical books set example prompts, notes, tips and warnings this way: a
pictogram about one line high (Manning's round "QS" prompt icon, a light bulb,
a warning sign) sits at the start of the first line, and the callout's lines
follow in a face other than the body text's (a sans next to a serif body).
Read block by block, the icon becomes a lone image and each line a loose
paragraph; the reader can no longer tell that the lines belong together.

This module finds them from geometry and fonts alone:

* the icon is an image whose sides are both between ``ICON_MIN_SIDE`` and
  ``ICON_MAX_SIDE`` points (text-line sized, never a figure);
* the first line starts at the icon's right edge, vertically level with it,
  and is set in a face that is not the body face (an icon inline with body
  text is a glyph, not a callout, and must not swallow the paragraph);
* the callout runs over the following text blocks set in that face, no
  larger than body text and indented at least as far as the icon, until any
  other text (the body face resuming, a heading) or the next icon. It carries
  on at the top of the next page when it filled its page.

A callout becomes one block: its logical lines (one per PDF text block,
wrapped lines joined) separated by newlines, the icon's page and bbox kept
under ``CALLOUT_ICON_KEY``. Lines that read as code (a JSON object, a
``name = \"\"\"`` string) make a code callout, which stays untranslated.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from statistics import median
from typing import Any

from book_agent.ingestion.pdf.models import PdfImageBlock, PdfPage, PdfTextBlock
from book_agent.ingestion.text import _looks_like_code, _normalize_text

CALLOUT_ROLE = "callout"
CALLOUT_ICON_KEY = "callout_icon"
CALLOUT_KIND_KEY = "callout_kind"
CALLOUT_KIND_PROSE = "prose"
CALLOUT_KIND_CODE = "code"
CALLOUT_LINE_COUNT_KEY = "callout_line_count"

# An icon is about one text line high: smaller is a bullet glyph or a rule, larger a figure.
ICON_MIN_SIDE = 8.0
ICON_MAX_SIDE = 24.0
# Below this an image renders as a figure card with nothing to show (QA's "tiny icon as figure").
TINY_IMAGE_MAX_SIDE = 24.0
_ICON_MAX_ASPECT = 2.0
# The first line starts at the icon's right edge, give or take a gap.
_FIRST_LINE_MAX_OFFSET = 24.0
# Callout text is no larger than body text; headings in the same face are.
_SIZE_TOLERANCE = 0.6
# ...and not much smaller: a figure's labels are (7pt next to a 10pt body).
_MIN_SIZE_RATIO = 0.85
# An icon of a callout stands alone; another picture this close makes it part of a figure.
_ICON_CLEARANCE = 12.0
# Lines of one callout sit this close together (points between one block's bottom and the next one's top).
_MAX_LINE_GAP = 16.0
# A callout reaching this far down its page may continue at the top of the next one.
_PAGE_BOTTOM_FRACTION = 0.8
# Running heads and folios sit in this band at the top and bottom of the page.
_EDGE_BAND_FRACTION = 0.07

# Fonts that draw bullets and pictographs, not text: they say nothing about the text's face.
_SYMBOL_FONT = re.compile(r"wingding|dingbat|webding|symbol", re.IGNORECASE)
# "name = \"\"\"" / "prompt = '''" / "config = {" opening an assignment, straight or typographic quotes.
_CODE_ASSIGNMENT_OPENER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*\s*=\s*(?:\"\"\"|'''|“””|”””|“““|[{\[(]|f?[\"'“])")
_JSON_OPENER = re.compile(r"^[{\[]\s*$")
_JSON_KEY_LINE = re.compile(r"^[\"“”'][^\"“”']+[\"“”']\s*:")


# Text and image blocks of a page are told apart by where they are: block numbers repeat once blocks are split.
BlockKey = tuple[int, tuple[float, float, float, float]]


def block_key(page_number: int, bbox: Iterable[float]) -> BlockKey:
    x0, y0, x1, y1 = (float(value) for value in bbox)
    return (page_number, (x0, y0, x1, y1))


def text_fonts(font_names: Iterable[str]) -> frozenset[str]:
    """The block's fonts that set text (symbol and pictograph fonts left out)."""
    return frozenset(name for name in font_names if name and not _SYMBOL_FONT.search(name))


@dataclass(frozen=True, slots=True)
class CalloutMember:
    page_number: int
    block: PdfTextBlock

    @property
    def key(self) -> BlockKey:
        return block_key(self.page_number, self.block.bbox)


@dataclass(frozen=True, slots=True)
class IconCallout:
    icon: PdfImageBlock
    members: tuple[CalloutMember, ...]
    lines: tuple[str, ...]
    kind: str

    @property
    def icon_key(self) -> BlockKey:
        return block_key(self.icon.page_number, self.icon.bbox)

    @property
    def page_start(self) -> int:
        return self.icon.page_number

    @property
    def page_end(self) -> int:
        return max(member.page_number for member in self.members)


@dataclass(frozen=True, slots=True)
class _BodyStyle:
    fonts: frozenset[str]
    size: float
    left_by_parity: dict[int, float]


def is_icon_sized(bbox: tuple[float, float, float, float] | list[float]) -> bool:
    width, height = float(bbox[2]) - float(bbox[0]), float(bbox[3]) - float(bbox[1])
    if not (ICON_MIN_SIDE <= width <= ICON_MAX_SIDE and ICON_MIN_SIDE <= height <= ICON_MAX_SIDE):
        return False
    return max(width, height) / max(min(width, height), 0.1) <= _ICON_MAX_ASPECT


def is_tiny_image(bbox: tuple[float, float, float, float] | list[float]) -> bool:
    width, height = float(bbox[2]) - float(bbox[0]), float(bbox[3]) - float(bbox[1])
    return 0 < width <= TINY_IMAGE_MAX_SIDE and 0 < height <= TINY_IMAGE_MAX_SIDE


def _body_style(pages: list[PdfPage]) -> _BodyStyle | None:
    """The body face (the face most characters are set in) and where body text starts on each page side."""
    chars: Counter[str] = Counter()
    for page in pages:
        for block in page.blocks:
            fonts = text_fonts(block.font_names)
            if len(fonts) == 1:
                chars[next(iter(fonts))] += len(block.text)
    if not chars:
        return None
    body_font = chars.most_common(1)[0][0]
    sizes: list[float] = []
    lefts: dict[int, list[float]] = {0: [], 1: []}
    for page in pages:
        page_lefts = [block.bbox[0] for block in page.blocks if body_font in block.font_names]
        if page_lefts:
            lefts[page.page_number % 2].append(round(min(page_lefts)))
        for block in page.blocks:
            if text_fonts(block.font_names) == {body_font}:
                sizes.extend(size for size, _bold in block.line_styles if size > 0)
                if not block.line_styles and block.font_size_avg > 0:
                    sizes.append(block.font_size_avg)
    size = median(sizes) if sizes else 0.0
    left_by_parity = {parity: Counter(values).most_common(1)[0][0] for parity, values in lefts.items() if values}
    return _BodyStyle(fonts=frozenset({body_font}), size=size, left_by_parity=left_by_parity)


def _page_body_left(page: PdfPage, style: _BodyStyle) -> float | None:
    page_lefts = [block.bbox[0] for block in page.blocks if style.fonts & block.font_names]
    if page_lefts:
        return min(page_lefts)
    return style.left_by_parity.get(page.page_number % 2)


def _block_max_size(block: PdfTextBlock) -> float:
    sizes = [size for size, _bold in block.line_styles if size > 0]
    return max(sizes) if sizes else block.font_size_max


def _first_line_box(block: PdfTextBlock) -> tuple[float, float]:
    """Vertical extent of the block's first line (blocks carry no per-line boxes; lines share the height)."""
    lines = max(block.line_count, 1)
    height = (block.bbox[3] - block.bbox[1]) / lines
    return block.bbox[1], block.bbox[1] + height


def _starts_beside(icon: PdfImageBlock, block: PdfTextBlock) -> bool:
    x0 = block.bbox[0]
    if not (icon.bbox[2] - 3.0 <= x0 <= icon.bbox[2] + _FIRST_LINE_MAX_OFFSET):
        return False
    top, bottom = _first_line_box(block)
    center = (top + bottom) / 2
    return icon.bbox[1] - 2.0 <= center <= icon.bbox[3] + 2.0


def _leads_its_line(icon: PdfImageBlock, ordered: list[PdfTextBlock]) -> bool:
    """No text on the icon's line before it: an icon inside a line of text is a glyph (an emoji in a code comment)."""
    center = (icon.bbox[1] + icon.bbox[3]) / 2
    for block in ordered:
        if block.bbox[1] > center or block.bbox[3] < center or _is_blank(block):
            continue
        if block.bbox[0] < icon.bbox[0] - 2.0 and block.bbox[2] > icon.bbox[0] - _FIRST_LINE_MAX_OFFSET:
            return False
    return True


def page_edge_furniture(page: PdfPage, block: PdfTextBlock) -> bool:
    """A short block in the top or bottom band of the page: a running head or folio."""
    band = page.height * _EDGE_BAND_FRACTION
    return block.line_count <= 2 and (block.bbox[3] <= band or block.bbox[1] >= page.height - band)


def _is_blank(block: PdfTextBlock) -> bool:
    return not block.text.strip()


def _logical_line(block: PdfTextBlock) -> str:
    """One callout line: a text block's wrapped lines joined (a line-end hyphen kept for the book-wide rejoin)."""
    parts = [_normalize_text(line) for line in (block.line_texts or block.text.splitlines())]
    joined = ""
    for part in parts:
        if not part:
            continue
        if joined.endswith("-") and joined[-2:-1].isalpha() and part[:1].isalnum():
            joined += part
        elif joined:
            joined += " " + part
        else:
            joined = part
    return joined.strip()


def looks_like_code_callout(lines: Iterable[str]) -> bool:
    """Lines that are a program's text (a JSON object, a string assigned to a name), not a prompt to translate."""
    lines = [line.strip() for line in lines if line.strip()]
    if not lines:
        return False
    first = lines[0]
    if _CODE_ASSIGNMENT_OPENER.match(first):
        return True
    if _JSON_OPENER.match(first):
        keyed = sum(1 for line in lines[1:] if _JSON_KEY_LINE.match(line) or line in {"{", "}", "[", "]", "},", "],"})
        return keyed * 2 >= max(len(lines) - 1, 1)
    return _looks_like_code("\n".join(lines), len(lines))


def detect_icon_callouts(
    pages: list[PdfPage],
    *,
    is_page_furniture: Callable[[PdfPage, PdfTextBlock], bool] | None = None,
) -> list[IconCallout]:
    """Every icon-led callout of the document, in reading order."""
    style = _body_style(pages)
    if style is None or not style.size:
        return []
    furniture = is_page_furniture or page_edge_furniture
    callouts: list[IconCallout] = []
    claimed: set[BlockKey] = set()
    for page_index, page in enumerate(pages):
        icons = [image for image in page.image_blocks if is_icon_sized(image.bbox)]
        if not icons:
            continue
        ordered = sorted(page.blocks, key=lambda block: (round(block.bbox[1], 2), round(block.bbox[0], 2)))
        for icon in sorted(icons, key=lambda image: (image.bbox[1], image.bbox[0])):
            callout = _callout_from_icon(pages, page_index, icon, ordered, icons, style, furniture, claimed)
            if callout is None:
                continue
            claimed.update(member.key for member in callout.members)
            callouts.append(callout)
    return callouts


def _is_member(block: PdfTextBlock, callout_fonts: frozenset[str], style: _BodyStyle) -> bool:
    fonts = text_fonts(block.font_names)
    if not fonts or fonts & style.fonts or not fonts & callout_fonts:
        return False
    size = _block_max_size(block)
    # Reading size: a figure's labels are set far smaller than the text, a heading larger.
    return style.size * _MIN_SIZE_RATIO <= size <= style.size + _SIZE_TOLERANCE


def _box_distance(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> float:
    dx = max(right[0] - left[2], left[0] - right[2], 0.0)
    dy = max(right[1] - left[3], left[1] - right[3], 0.0)
    return max(dx, dy)


def _stands_alone(icon: PdfImageBlock, page: PdfPage) -> bool:
    """No other picture touches the icon: small images packed together are the parts of a figure."""
    return not any(
        other is not icon and _box_distance(tuple(other.bbox), tuple(icon.bbox)) < _ICON_CLEARANCE
        for other in page.image_blocks
    )


def _callout_from_icon(
    pages: list[PdfPage],
    page_index: int,
    icon: PdfImageBlock,
    ordered: list[PdfTextBlock],
    icons: list[PdfImageBlock],
    style: _BodyStyle,
    furniture: Callable[[PdfPage, PdfTextBlock], bool],
    claimed: set[BlockKey],
) -> IconCallout | None:
    page = pages[page_index]
    if not _leads_its_line(icon, ordered) or not _stands_alone(icon, page):
        return None
    first = next(
        (
            block
            for block in ordered
            if not _is_blank(block) and block_key(page.page_number, block.bbox) not in claimed and _starts_beside(icon, block)
        ),
        None,
    )
    if first is None:
        return None
    callout_fonts = text_fonts(first.font_names)
    # Body text next to a small picture is a paragraph with an inline glyph, not a callout.
    if not callout_fonts or not _is_member(first, callout_fonts, style):
        return None
    members = [CalloutMember(page.page_number, first)]
    other_icons = [image for image in icons if image is not icon]
    indent_floor = icon.bbox[0] + (icon.bbox[2] - icon.bbox[0]) / 2

    def starts_other_callout(block: PdfTextBlock) -> bool:
        return any(_starts_beside(image, block) for image in other_icons if image.bbox[1] > icon.bbox[1])

    previous = first
    reached_page_end = True
    for block in ordered[ordered.index(first) + 1 :]:
        if _is_blank(block) or furniture(page, block):
            continue
        if block.bbox[1] < previous.bbox[3] - 2.0:
            # Level with a line already taken (a margin note, the other column): not part of it.
            continue
        if (
            block.bbox[1] - previous.bbox[3] > _MAX_LINE_GAP
            or block.bbox[0] < indent_floor
            or not _is_member(block, callout_fonts, style)
            or starts_other_callout(block)
        ):
            reached_page_end = False
            break
        members.append(CalloutMember(page.page_number, block))
        previous = block
    if reached_page_end and previous.bbox[3] >= page.height * _PAGE_BOTTOM_FRACTION:
        members.extend(_continuation_members(pages, page_index, icon, callout_fonts, style, furniture))
    lines = tuple(line for line in (_logical_line(member.block) for member in members) if line)
    if not lines:
        return None
    kind = CALLOUT_KIND_CODE if looks_like_code_callout(lines) else CALLOUT_KIND_PROSE
    return IconCallout(icon=icon, members=tuple(members), lines=lines, kind=kind)


def _continuation_members(
    pages: list[PdfPage],
    page_index: int,
    icon: PdfImageBlock,
    callout_fonts: frozenset[str],
    style: _BodyStyle,
    furniture: Callable[[PdfPage, PdfTextBlock], bool],
) -> list[CalloutMember]:
    """The callout's lines at the top of the following pages, while each page is filled by it."""
    icon_page = pages[page_index]
    icon_left = _page_body_left(icon_page, style)
    # How far the icon sits from the body's left edge; continued lines are indented at least as far.
    icon_offset = (icon.bbox[0] - icon_left) if icon_left is not None else 0.0
    members: list[CalloutMember] = []
    for page in pages[page_index + 1 :]:
        body_left = _page_body_left(page, style)
        ordered = sorted(page.blocks, key=lambda block: (round(block.bbox[1], 2), round(block.bbox[0], 2)))
        previous: PdfTextBlock | None = None
        ended = False
        for block in ordered:
            if _is_blank(block) or furniture(page, block):
                continue
            indented = body_left is None or block.bbox[0] > body_left + icon_offset + 2.0
            close = previous is None or block.bbox[1] - previous.bbox[3] <= _MAX_LINE_GAP
            first_on_page = previous is None
            if not (indented and close and _is_member(block, callout_fonts, style)):
                ended = True
                break
            if first_on_page and block.bbox[1] > page.height * (1 - _PAGE_BOTTOM_FRACTION):
                # The page does not open with the callout: it ended on the previous page.
                ended = True
                break
            if any(is_icon_sized(image.bbox) and _starts_beside(image, block) for image in page.image_blocks):
                ended = True
                break
            members.append(CalloutMember(page.page_number, block))
            previous = block
        if ended or previous is None or previous.bbox[3] < page.height * _PAGE_BOTTOM_FRACTION:
            break
    return members


def callout_metadata(callout: IconCallout) -> dict[str, Any]:
    """The block metadata a callout carries: its kind, line count and the icon it is drawn with."""
    icon = callout.icon
    icon_json: dict[str, Any] = {
        "page_number": icon.page_number,
        "bbox": [round(float(value), 3) for value in icon.bbox],
    }
    if icon.xref is not None:
        icon_json["image_xref"] = icon.xref
    if icon.materialized_path:
        icon_json["image_path"] = icon.materialized_path
    if icon.image_ext:
        icon_json["image_ext"] = icon.image_ext
    if icon.width_px:
        icon_json["width_px"] = icon.width_px
    if icon.height_px:
        icon_json["height_px"] = icon.height_px
    return {
        "pdf_block_role": CALLOUT_ROLE,
        CALLOUT_KIND_KEY: callout.kind,
        CALLOUT_LINE_COUNT_KEY: len(callout.lines),
        CALLOUT_ICON_KEY: icon_json,
    }


def is_callout_metadata(metadata: dict[str, Any] | None) -> bool:
    return bool(metadata) and (metadata or {}).get("pdf_block_role") == CALLOUT_ROLE


def callout_lines(text: str) -> list[str]:
    """A callout's lines: its text is one line per newline."""
    return [line.strip() for line in (text or "").split("\n") if line.strip()]


_BRACKETS_ONLY = re.compile(r"^[{}\[\]()<>,;:|]+$")
_LONE_PLACEHOLDER = re.compile(r"^\{[A-Za-z_][\w.]*\}$")


def callout_line_is_code(line: str) -> bool:
    """A line of a prompt that is data, not words to translate: a brace, a JSON key, a lone ``{placeholder}``."""
    stripped = line.strip()
    return bool(_BRACKETS_ONLY.match(stripped) or _JSON_KEY_LINE.match(stripped) or _LONE_PLACEHOLDER.match(stripped))
