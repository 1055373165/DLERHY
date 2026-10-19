from __future__ import annotations

import copy
import html
import logging
import posixpath
import re
import zipfile
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

from book_agent.domain.document_titles import compose_document_title
from book_agent.domain.structure.models import (
    ParsedBlock,
    ParsedChapter,
    ParsedDocument,
    derive_translatability,
)

logger = logging.getLogger(__name__)

_CONTAINER_PATH = "META-INF/container.xml"
_CONTAINER_NS = {"container": "urn:oasis:names:tc:opendocument:xmlns:container"}
_OPF_NS = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}
_XHTML_NS = {"xhtml": "http://www.w3.org/1999/xhtml", "epub": "http://www.idpf.org/2007/ops"}
_BLOCK_TAGS = {
    "p": "paragraph",
    "blockquote": "quote",
    "pre": "code",
    "li": "list_item",
    # A definition list's term and (when it holds no paragraphs) its description.
    "dt": "paragraph",
    "dd": "paragraph",
    "figcaption": "caption",
    "table": "table",
    "math": "code",
    "svg": "code",
}
_HEADING_TAGS = {f"h{i}" for i in range(1, 7)}
# Raised when the parser keeps more of the book's structure, so an already parsed book can tell
# that a structure refresh would change it (pilot QA ``L1.epub_structure_outdated``).
# 2: list kind/level/number, definition terms, note/tip/warning/sidebar boxes.
EPUB_STRUCTURE_VERSION = 2
# O'Reilly (HTMLBook) boxes: data-type / epub:type of the element that holds the box.
_BOX_KINDS = ("note", "tip", "warning", "caution", "important", "sidebar")
_BLOCK_CHILD_TAGS = {"p", "div", "ul", "ol", "dl", "pre", "table", "blockquote", "figure", "section", "aside", *[f"h{i}" for i in range(1, 7)]}
_KNOWN_HTML_TAGS = {
    "html",
    "head",
    "body",
    "title",
    "meta",
    "link",
    "nav",
    "section",
    "article",
    "aside",
    "div",
    "span",
    "p",
    "blockquote",
    "pre",
    "code",
    "ol",
    "ul",
    "li",
    "figure",
    "figcaption",
    "img",
    "a",
    "em",
    "strong",
    "b",
    "i",
    "u",
    "small",
    "sub",
    "sup",
    "table",
    "thead",
    "tbody",
    "tfoot",
    "tr",
    "td",
    "th",
    "colgroup",
    "col",
    "br",
    "hr",
    "dl",
    "dt",
    "dd",
    "kbd",
    "samp",
    "var",
    "svg",
    "math",
    *list(_HEADING_TAGS),
}
_TOC_LIKE_TITLES = {"contents", "table of contents", "brief contents"}
_TOC_LIKE_PATH_TOKENS = {"contents", "table-of-contents", "toc"}
_TITLEPAGE_PATH_TOKENS = {"titlepage", "title-page", "cover", "half-title"}
_GENERIC_SPINE_NAV_TITLES = {
    "back matter",
    "body matter",
    "contents",
    "cover",
    "front matter",
    "landmarks",
    "navigation",
    "pages",
    "table of contents",
}
_PAGE_LABEL_PATTERN = re.compile(r"^(?:\d+|[ivxlcdm]+)$", re.IGNORECASE)
_EPUB_TYPE_ATTR = "{http://www.idpf.org/2007/ops}type"


def _box_kind(element: ET.Element, local_name: str) -> str | None:
    """The kind of an O'Reilly box (``<div data-type="note">``, ``<aside data-type="sidebar">``)."""
    if local_name not in {"div", "aside", "section"}:
        return None
    declared = (
        element.attrib.get("data-type", "")
        + " "
        + element.attrib.get("{http://www.idpf.org/2007/ops}type", "")
    ).lower().split()
    return next((kind for kind in _BOX_KINDS if kind in declared), None)


def _nested_lists(item: ET.Element) -> list[ET.Element]:
    """The lists inside a list item (directly or in its paragraphs), not those of deeper items."""
    found: list[ET.Element] = []

    def walk(element: ET.Element) -> None:
        for child in list(element):
            if _local_name(child.tag) in {"ul", "ol"}:
                found.append(child)
            else:
                walk(child)

    walk(item)
    return found


def _has_block_child(element: ET.Element) -> bool:
    return any(_local_name(child.tag) in _BLOCK_CHILD_TAGS for child in element.iter() if child is not element)


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


# Word documents set ®, © and ™ in the Symbol font, and exports keep its private-use code points (U+F0xx).
_SYMBOL_FONT_MARKS = {"\uf0d2": "®", "\uf0e2": "®", "\uf0d3": "©", "\uf0e3": "©", "\uf0d4": "™", "\uf0e4": "™"}
_SYMBOL_FONT_MARK_RE = re.compile(r"(\s*)([\uf0d2-\uf0d4\uf0e2-\uf0e4])")


def _symbol_font_mark(match: re.Match[str]) -> str:
    mark = _SYMBOL_FONT_MARKS[match.group(2)]
    # ® and ™ sit against the name they mark ("Apple \uf0e2" -> "Apple®"); © keeps its space.
    return mark if mark in "®™" else match.group(1) + mark


def _normalize_text(text: str | None) -> str:
    text = _SYMBOL_FONT_MARK_RE.sub(_symbol_font_mark, text or "")
    return re.sub(r"\s+", " ", text).strip()


def _normalize_preformatted_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")


def _normalized_heading_level(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _normalize_chapter_heading_levels(blocks: list[ParsedBlock]) -> list[ParsedBlock]:
    heading_positions = [index for index, block in enumerate(blocks) if block.block_type == "heading"]
    if not heading_positions:
        return blocks

    raw_levels = [
        _normalized_heading_level(blocks[index].metadata.get("heading_level"))
        for index in heading_positions
    ]
    numeric_raw_levels = [level for level in raw_levels if level is not None]
    if not numeric_raw_levels:
        return blocks

    chapter_base_level = min(numeric_raw_levels)
    first_heading_index = heading_positions[0]
    first_heading_raw_level = _normalized_heading_level(blocks[first_heading_index].metadata.get("heading_level"))
    has_peer_level_after_title = any(
        raw_level == first_heading_raw_level
        for raw_level in raw_levels[1:]
        if raw_level is not None and first_heading_raw_level is not None
    )
    normalized_blocks: list[ParsedBlock] = []
    for index, block in enumerate(blocks):
        if block.block_type != "heading":
            normalized_blocks.append(block)
            continue
        metadata = dict(block.metadata or {})
        raw_level = _normalized_heading_level(metadata.get("heading_level"))
        if index == first_heading_index:
            metadata["heading_level"] = 1
        elif raw_level is not None:
            if first_heading_raw_level is not None and raw_level == first_heading_raw_level:
                metadata["heading_level"] = 2
            else:
                offset = 2 if has_peer_level_after_title else 1
                metadata["heading_level"] = max(2, raw_level - chapter_base_level + offset)
        normalized_blocks.append(
            ParsedBlock(
                block_type=block.block_type,
                text=block.text,
                source_path=block.source_path,
                ordinal=block.ordinal,
                anchor=block.anchor,
                metadata=metadata,
                translatability=derive_translatability(block.block_type, metadata),
            )
        )
    return normalized_blocks


def _extract_rich_text(element: ET.Element) -> str:
    """Walk *element* tree preserving inline ``<code>``, bold, and italic as markdown."""
    parts: list[str] = []

    _INLINE_MARKERS: dict[str, tuple[str, str]] = {
        "code": ("`", "`"),
        "b": ("**", "**"),
        "strong": ("**", "**"),
        "i": ("*", "*"),
        "em": ("*", "*"),
    }

    def _walk(el: ET.Element) -> None:
        local = _local_name(el.tag)
        markers = _INLINE_MARKERS.get(local)

        if markers:
            # Collect all nested text inside this inline element.
            inner = "".join(el.itertext())
            if inner.strip():
                # "<em>arXiv, </em>2502.02649": the space belongs outside the markers, or the
                # closing star sits after a space and no longer reads as one.
                stripped = inner.strip()
                lead = inner[: len(inner) - len(inner.lstrip())]
                trail = inner[len(inner.rstrip()) :]
                parts.append(f"{lead}{markers[0]}{stripped}{markers[1]}{trail}")
            # Handle tail text (text after the closing tag).
            if el.tail:
                parts.append(el.tail)
            return

        # Not an inline-marked element — emit own text, then recurse children.
        if el.text:
            parts.append(el.text)
        for child in el:
            _walk(child)
        if el.tail:
            parts.append(el.tail)

    # Start with the root element but skip its tail (belongs to parent).
    if element.text:
        parts.append(element.text)
    for child in element:
        _walk(child)
    return _normalize_text("".join(parts))


def _looks_like_metadata_filename(value: str) -> bool:
    candidate = _normalize_text(value).casefold()
    if not candidate:
        return False
    if "/" in candidate or "\\" in candidate:
        return True
    return candidate.endswith((".html", ".xhtml", ".htm", ".xml", ".opf", ".ncx"))


def _class_tokens(value: str) -> set[str]:
    return {token.casefold() for token in re.split(r"\s+", value or "") if token}


def _element_class_tokens(element: ET.Element) -> set[str]:
    return _class_tokens(element.attrib.get("class", ""))


def _first_descendant(element: ET.Element, local_names: set[str]) -> ET.Element | None:
    for descendant in element.iter():
        if descendant is element:
            continue
        if _local_name(descendant.tag) in local_names:
            return descendant
    return None


def _figure_like_container(local_name: str, class_tokens: set[str], element: ET.Element) -> bool:
    if _first_descendant(element, {"img"}) is None:
        return False
    if local_name == "figure":
        return True
    if local_name != "div":
        return False
    return bool({"figure-container", "image-container", "imageblock", "mediaobject"} & class_tokens)


def _figure_caption_text(element: ET.Element) -> str:
    figcaption = _first_descendant(element, {"figcaption"})
    if figcaption is not None:
        return _normalize_text("".join(figcaption.itertext()))
    for descendant in element.iter():
        if descendant is element:
            continue
        if _local_name(descendant.tag) not in {"h5", "h6", "p", "span", "div"}:
            continue
        if _first_descendant(descendant, {"img"}) is not None:
            continue
        text = _normalize_text("".join(descendant.itertext()))
        if text:
            return text
    return ""


def _mark_image_only_metadata_nontranslatable(metadata: dict[str, object]) -> None:
    metadata["translatable"] = False
    metadata["nontranslatable_reason"] = "image_only_artifact"


def _join_path(base_dir: str, href: str) -> str:
    if not base_dir:
        return href
    return posixpath.normpath(posixpath.join(base_dir, href))


# A trailing parenthetical that is empty, or one word and dots, is a
# production leftover: an O'Reilly early release carried "An Illustrated Guide to AI Agents (for . .)".
_TITLE_DEBRIS = re.compile(r"\s*\((?:\s*\w*\s*(?:\.\s*)+|\s*)\)\s*$")


def _strip_title_debris(title: str) -> str:
    return _TITLE_DEBRIS.sub("", title) or title


def _looks_like_page_nav_label(value: str | None) -> bool:
    normalized = _normalize_text(value or "")
    if not normalized:
        return False
    return bool(_PAGE_LABEL_PATTERN.fullmatch(normalized))


def _looks_like_generic_spine_nav_title(value: str | None) -> bool:
    return _normalize_text(value or "").casefold() in _GENERIC_SPINE_NAV_TITLES


_SAFE_XML_ENTITY_NAMES = {"amp", "apos", "gt", "lt", "quot"}
_NAMED_HTML_ENTITY_PATTERN = re.compile(r"&([A-Za-z][A-Za-z0-9]+);")


def _replace_named_html_entities(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        entity_name = match.group(1)
        if entity_name in _SAFE_XML_ENTITY_NAMES:
            return match.group(0)
        entity_value = html.entities.html5.get(f"{entity_name};") or html.entities.html5.get(entity_name)
        if entity_value is None:
            return match.group(0)
        if isinstance(entity_value, tuple):
            entity_value = entity_value[0]
        return str(entity_value)

    return _NAMED_HTML_ENTITY_PATTERN.sub(repl, text)


def _sanitize_xml_text(text: str) -> str:
    """Apply regex-based cleanup to make malformed XML more parseable.

    Handles:
    - Invalid XML characters (control chars except tab/newline/carriage-return)
    - Unescaped ampersands (& not followed by a valid entity reference)
    - Non-breaking spaces and other common problematic characters
    """
    # Strip XML-illegal control characters (everything below 0x20 except 0x09, 0x0A, 0x0D).
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    # Escape bare ampersands that are not part of a valid entity reference.
    text = re.sub(r"&(?!#?\w+;)", "&amp;", text)
    return text


_BARE_CHAPTER_LABEL = re.compile(
    r"^(?:chapter|part)\s+(?:\d{1,3}|[ivxlc]{1,7}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)$",
    re.IGNORECASE,
)


def _named_chapter_label(title: str | None, blocks: list[ParsedBlock]) -> str | None:
    """"CHAPTER 1" with the name the chapter gives itself next ("CHAPTER 1: SWING TRADING BASICS"): a book
    whose contents list the label and the name as two entries titles the file by the label alone."""
    if not title or not _BARE_CHAPTER_LABEL.match(_normalize_text(title)):
        return title
    headings = [_normalize_text(block.text) for block in blocks if block.block_type == "heading"]
    for index, text in enumerate(headings[:-1]):
        if text.casefold() == _normalize_text(title).casefold():
            name = headings[index + 1]
            if name and len(name) <= 80 and not _BARE_CHAPTER_LABEL.match(name):
                return f"{_normalize_text(title)}: {name}"
    return title


def _title_pages_before_the_contents(chapters: list[ParsedChapter], nav_map: dict[str, str]) -> list[ParsedChapter]:
    """Title the untitled pages ahead of the first chapter the book's contents list (its title page,
    its copyright page). Later untitled files are pieces of a chapter split mid-way, not pages."""
    if not nav_map:
        return chapters
    titled: list[ParsedChapter] = []
    before_contents = True
    for chapter in chapters:
        if posixpath.normpath(chapter.href) in nav_map:
            before_contents = False
        if before_contents and not chapter.title:
            chapter = replace(chapter, title=_untitled_page_title(chapter.blocks))
        titled.append(chapter)
    return titled


def _untitled_page_title(blocks: list[ParsedBlock]) -> str | None:
    """A title for a page the book's contents do not list (its title page, its copyright page): its
    own first short line. Review cannot work on a chapter without one ("missing_chapter_title")."""
    words: list[str] = []
    for block in blocks:
        text = _normalize_text(block.text)
        if not text:
            continue
        if not words and text.casefold().startswith(("copyright", "©")):
            return "Copyright"
        if len(text.split()) > 10 or len(text) > 60 or text.startswith("["):
            break
        # A title page sets its title over short lines ("HOW TO" / "SWING TRADE").
        words.extend(text.split())
        if len(words) >= 3:
            break
    return " ".join(words) or None


def _qualify_xhtml(root: ET.Element) -> ET.Element:
    """A page whose ``<html>`` declares no namespace, read as XHTML.

    Amazon's EPUB export (How to Swing Trade) opens every chapter with a bare ``<html>``: its
    ``<body>`` was in no namespace, the chapters came out empty and the book imported with none.
    """
    if root.tag != "html":
        return root
    namespace = _XHTML_NS["xhtml"]
    for element in root.iter():
        if isinstance(element.tag, str) and not element.tag.startswith("{"):
            element.tag = f"{{{namespace}}}{element.tag}"
    return root


def _parse_xml_document(raw: bytes) -> ET.Element:
    text = raw.decode("utf-8", errors="replace")

    # --- Tier 1: standard parse ---
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        pass

    # --- Tier 2: resolve named HTML entities (e.g. &times;) ---
    sanitized = _replace_named_html_entities(text)
    if sanitized != text:
        try:
            return ET.fromstring(sanitized)
        except ET.ParseError:
            pass
        text = sanitized  # keep entity-resolved version for subsequent tiers

    # --- Tier 3: lxml with recovery mode (tolerates most malformed XML) ---
    try:
        from lxml import etree as lxml_etree  # type: ignore[import-untyped]

        lxml_parser = lxml_etree.XMLParser(recover=True, encoding="utf-8")
        lxml_root = lxml_etree.fromstring(text.encode("utf-8"), parser=lxml_parser)
        # If lxml had to fix many structural tag mismatches the recovered tree
        # may have a significantly different element hierarchy (e.g. elements
        # re-parented or swallowed).  In that case we prefer to let the caller
        # fall back to its own HTML-aware parser which handles broken tags more
        # faithfully.  A small number of mismatches (e.g. a single
        # <link>/<script> mismatch at the end of a file) is typically benign.
        total_errors = len(lxml_parser.error_log)
        tag_mismatch_count = sum(
            1 for e in lxml_parser.error_log if "ERR_TAG_NAME_MISMATCH" in str(e)
        )
        mostly_structural = total_errors > 0 and (tag_mismatch_count / total_errors) > 0.5
        if not mostly_structural:
            # Convert lxml tree back to stdlib ElementTree so the rest of the
            # code (namespace queries, etc.) works identically.
            return ET.fromstring(lxml_etree.tostring(lxml_root, encoding="unicode"))
        logger.debug(
            "lxml recovered with %d/%d structural tag mismatches; skipping to preserve tree fidelity",
            tag_mismatch_count,
            total_errors,
        )
    except ImportError:
        logger.debug("lxml not available; skipping recovery-mode XML parse")
    except Exception:
        logger.debug("lxml recovery-mode parse also failed", exc_info=True)

    # --- Tier 4: regex-based XML cleanup + retry stdlib parse ---
    cleaned = _sanitize_xml_text(text)
    if cleaned != text:
        try:
            return ET.fromstring(cleaned)
        except ET.ParseError:
            pass

    # --- Tier 5: combined entity resolution + cleanup ---
    combined = _sanitize_xml_text(_replace_named_html_entities(cleaned))
    if combined != cleaned:
        try:
            return ET.fromstring(combined)
        except ET.ParseError:
            pass

    # All tiers exhausted — raise the original error for diagnostics.
    return ET.fromstring(raw.decode("utf-8", errors="replace"))


_STYLED_HEADING_MAX_CHARS = 140
_CSS_RULE = re.compile(r"([^{}]+)\{([^}]*)\}")
_CSS_CLASS_SELECTOR = re.compile(r"^\s*(?:p)?\.([A-Za-z_][\w-]*)\s*$")
_CSS_FONT_SIZE = re.compile(r"font-size\s*:\s*([\d.]+)\s*(em|rem|%)", re.IGNORECASE)
_CSS_FONT_WEIGHT = re.compile(r"font-weight\s*:\s*(bold|bolder|\d{3})", re.IGNORECASE)


def _styled_heading_levels(archive: zipfile.ZipFile, manifest: dict[str, dict[str, str]]) -> dict[str, int]:
    """Classes the book's stylesheets set bold and at least 1.15 times the text size, ranked by size
    (the largest is level 1). InDesign exports set headings as ``<p class="heading1">``."""
    sizes: dict[str, float] = {}
    for item in manifest.values():
        if item["media_type"] != "text/css":
            continue
        try:
            css = archive.read(item["href"]).decode("utf-8", errors="replace")
        except KeyError:
            continue
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
        for selectors, body in _CSS_RULE.findall(css):
            size = _CSS_FONT_SIZE.search(body)
            weight = _CSS_FONT_WEIGHT.search(body)
            if size is None or weight is None:
                continue
            value = float(size.group(1)) / (100.0 if size.group(2) == "%" else 1.0)
            bold = weight.group(1).lower() in {"bold", "bolder"} or int(weight.group(1) if weight.group(1).isdigit() else 0) >= 600
            if not bold or value < 1.15:
                continue
            for selector in selectors.split(","):
                match = _CSS_CLASS_SELECTOR.match(selector)
                if match:
                    sizes[match.group(1)] = max(value, sizes.get(match.group(1), 0.0))
    # Rank only the classes paragraphs wear: a drop cap's or an inline override's large bold span is no heading.
    paragraph_classes: set[str] = set()
    for item in manifest.values():
        if item["media_type"] not in {"application/xhtml+xml", "text/html"}:
            continue
        try:
            markup = archive.read(item["href"]).decode("utf-8", errors="replace")
        except KeyError:
            continue
        for match in re.finditer(r"<p\b[^>]*\bclass=[\"']([^\"']*)[\"']", markup):
            paragraph_classes.update(match.group(1).split())
    sizes = {name: value for name, value in sizes.items() if name in paragraph_classes}
    ranked = sorted(set(sizes.values()), reverse=True)
    return {name: min(ranked.index(value) + 1, 4) for name, value in sizes.items()}


def _dense_styled_heading_levels(blocks: list[ParsedBlock]) -> list[ParsedBlock]:
    """A chapter's stylesheet headings numbered 1, 2, ... by size: a size the chapter does not use (the
    contents page's title) leaves no gap between its title and its sections."""
    used = sorted({block.metadata["heading_level"] for block in blocks if block.metadata.get("styled_heading")})
    dense = {level: rank for rank, level in enumerate(used, start=1)}
    return [
        replace(block, metadata={**block.metadata, "heading_level": dense[block.metadata["heading_level"]]})
        if block.metadata.get("styled_heading")
        else block
        for block in blocks
    ]


# An image the book sets this many times is an ornament, not a figure.
_ORNAMENT_IMAGE_MIN_USES = 5


def _drop_ornament_images(chapters: list[ParsedChapter]) -> list[ParsedChapter]:
    """Leave out an uncaptioned image the book repeats (Mastering Trading Psychology opens each of its
    197 quoted forum posts with the same 100x100 quotation mark): every copy came out as an image box,
    and its generated alt text ("A screenshot of a cell phone ...") went to translation each time."""
    uses: dict[str, int] = {}
    for chapter in chapters:
        for block in chapter.blocks:
            path = block.metadata.get("image_path") or block.metadata.get("image_src")
            if path:
                uses[str(path)] = uses.get(str(path), 0) + 1

    def ornament(block: ParsedBlock) -> bool:
        path = block.metadata.get("image_path") or block.metadata.get("image_src")
        return (
            bool(path)
            and uses.get(str(path), 0) >= _ORNAMENT_IMAGE_MIN_USES
            and block.metadata.get("image_caption_generated") in {"alt", "placeholder"}
        )

    if not any(count >= _ORNAMENT_IMAGE_MIN_USES for count in uses.values()):
        return chapters
    return [
        replace(chapter, blocks=[block for block in chapter.blocks if not ornament(block)])
        for chapter in chapters
    ]


def _block_type_for_html(tag: str, attrs: dict[str, str]) -> str | None:
    class_tokens = _class_tokens(attrs.get("class", ""))
    if tag == "div" and "figure-container" in class_tokens:
        return "caption"
    if tag in _HEADING_TAGS:
        return "heading"
    if tag in _BLOCK_TAGS:
        return _BLOCK_TAGS[tag]
    epub_type = attrs.get("epub:type", "") or attrs.get("type", "")
    if "footnote" in epub_type:
        return "footnote"
    if tag == "aside" and "footnote" in epub_type:
        return "footnote"
    return None


class _FallbackHTMLBlockExtractor(HTMLParser):
    def __init__(self, href: str):
        super().__init__(convert_charrefs=True)
        self.href = href
        self.blocks: list[ParsedBlock] = []
        self._body_depth = 0
        self._saw_body = False
        self._active_block: dict[str, str | dict[str, str] | list[str] | None] | None = None

    def extract(self, text: str) -> list[ParsedBlock]:
        self.feed(text)
        self.close()
        self._finalize_active_block()
        return self.blocks

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "body":
            self._saw_body = True
            self._body_depth += 1
            return
        if not self._inside_content():
            return

        attr_map = {key.lower(): (value or "") for key, value in attrs}
        if tag not in _KNOWN_HTML_TAGS:
            if self._active_block is not None and str(self._active_block.get("block_type")) == "table":
                return
            self._append_text(self.get_starttag_text() or f"<{tag}>")
            return

        if tag == "img":
            src = attr_map.get("src", "")
            alt = _normalize_text(attr_map.get("alt", ""))
            if self._active_block is not None:
                metadata = self._active_block["metadata"]
                assert isinstance(metadata, dict)
                source_path = self._active_block["source_path"]
                assert isinstance(source_path, str)
                if src:
                    metadata["image_src"] = src
                    metadata["image_path"] = _join_path(posixpath.dirname(source_path), src)
                if alt:
                    metadata["image_alt"] = alt
            elif src:
                # Standalone <img> outside any block — create a paragraph block for it.
                img_metadata: dict[str, str | int] = {"tag": "p"}
                img_metadata["image_src"] = src
                img_metadata["image_path"] = _join_path(posixpath.dirname(self.href), src)
                if alt:
                    img_metadata["image_alt"] = alt
                self._active_block = {
                    "tag": "p",
                    "block_type": "paragraph",
                    "anchor": attr_map.get("id"),
                    "metadata": img_metadata,
                    "text_parts": [],
                    "source_path": self.href,
                }
                self._flush_active_block()
            return

        if self._active_block is not None and str(self._active_block.get("block_type")) == "table":
            if tag == "tr":
                self._active_block["current_row"] = []
                self._active_block["current_cell"] = None
                return
            if tag in {"th", "td"}:
                self._active_block["current_cell"] = []
                return
            if tag == "br":
                current_cell = self._active_block.get("current_cell")
                if isinstance(current_cell, list):
                    current_cell.append("\n")
                return
            if tag in {"colgroup", "col", "thead", "tbody", "tfoot"}:
                return

        if self._active_block is None:
            block_type = _block_type_for_html(tag, attr_map)
            if block_type:
                metadata: dict[str, str | int] = {"tag": tag}
                if tag in _HEADING_TAGS and len(tag) == 2 and tag[1].isdigit():
                    metadata["heading_level"] = int(tag[1])
                active_block = {
                    "tag": tag,
                    "block_type": block_type,
                    "anchor": attr_map.get("id"),
                    "metadata": metadata,
                    "text_parts": [],
                    "source_path": self.href,
                }
                if block_type == "table":
                    active_block["rows"] = []
                    active_block["current_row"] = None
                    active_block["current_cell"] = None
                self._active_block = active_block
                return

        if tag == "br":
            self._append_text("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() == "br":
            return
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "body":
            self._body_depth = max(0, self._body_depth - 1)
            return
        if not self._inside_content():
            return
        if tag not in _KNOWN_HTML_TAGS:
            if self._active_block is not None and str(self._active_block.get("block_type")) == "table":
                return
            self._append_text(f"</{tag}>")
            return
        if self._active_block is not None and str(self._active_block.get("block_type")) == "table":
            if tag in {"th", "td"}:
                current_cell = self._active_block.get("current_cell")
                current_row = self._active_block.get("current_row")
                if isinstance(current_cell, list) and isinstance(current_row, list):
                    cell_text = _normalize_text("".join(current_cell))
                    if cell_text:
                        current_row.append(cell_text)
                self._active_block["current_cell"] = None
                return
            if tag == "tr":
                current_row = self._active_block.get("current_row")
                rows = self._active_block.get("rows")
                if isinstance(current_row, list) and isinstance(rows, list) and current_row:
                    rows.append(current_row)
                self._active_block["current_row"] = None
                self._active_block["current_cell"] = None
                return
            if tag in {"colgroup", "col", "thead", "tbody", "tfoot"}:
                return
        if self._active_block is not None and self._active_block["tag"] == tag:
            self._finalize_active_block()

    def handle_data(self, data: str) -> None:
        if not self._inside_content():
            return
        if self._active_block is not None and str(self._active_block.get("block_type")) == "table":
            current_cell = self._active_block.get("current_cell")
            if isinstance(current_cell, list):
                current_cell.append(data)
            return
        self._append_text(data)

    def _inside_content(self) -> bool:
        return self._body_depth > 0 or not self._saw_body

    def _append_text(self, text: str) -> None:
        if self._active_block is None:
            return
        text_parts = self._active_block["text_parts"]
        assert isinstance(text_parts, list)
        text_parts.append(text)

    def _finalize_active_block(self) -> None:
        if self._active_block is None:
            return
        text_parts = self._active_block["text_parts"]
        assert isinstance(text_parts, list)
        block_type = str(self._active_block["block_type"])
        if block_type == "table":
            current_cell = self._active_block.get("current_cell")
            current_row = self._active_block.get("current_row")
            rows = self._active_block.get("rows")
            if isinstance(current_cell, list) and isinstance(current_row, list):
                cell_text = _normalize_text("".join(current_cell))
                if cell_text:
                    current_row.append(cell_text)
                self._active_block["current_cell"] = None
            if isinstance(current_row, list) and isinstance(rows, list) and current_row:
                rows.append(current_row)
                self._active_block["current_row"] = None
            raw_rows = rows if isinstance(rows, list) else []
            text = "\n".join(" | ".join(cell for cell in row if cell) for row in raw_rows if row)
        else:
            raw_text = "".join(text_parts)
            text = _normalize_preformatted_text(raw_text) if block_type == "code" else _normalize_text(raw_text)
        metadata = dict(self._active_block["metadata"])
        if not text and metadata.get("image_alt"):
            _mark_image_only_metadata_nontranslatable(metadata)
            metadata["image_caption_generated"] = "alt"
            text = str(metadata["image_alt"])
        elif not text and metadata.get("image_src"):
            _mark_image_only_metadata_nontranslatable(metadata)
            metadata["image_caption_generated"] = "placeholder"
            text = "[Image]"
        if text or metadata.get("image_src"):
            self.blocks.append(
                ParsedBlock(
                    block_type=block_type,
                    text=text,
                    source_path=self.href,
                    ordinal=len(self.blocks) + 1,
                    anchor=(
                        str(self._active_block["anchor"])
                        if self._active_block["anchor"] is not None
                        else None
                    ),
                    metadata=metadata,
                    translatability=derive_translatability(block_type, metadata),
                )
            )
        self._active_block = None


class EPUBParser:
    """Minimal EPUB parser for P0.

    Goals:
    - preserve spine order
    - recover basic metadata
    - extract chapter/block text from XHTML content documents
    - keep anchors for later alignment and export
    """

    def parse(self, file_path: str | Path) -> ParsedDocument:
        with zipfile.ZipFile(file_path) as archive:
            opf_path = self._resolve_opf_path(archive)
            opf_root = _parse_xml_document(archive.read(opf_path))
            opf_dir = posixpath.dirname(opf_path)

            manifest = self._read_manifest(opf_root, opf_dir)
            nav_map = self._read_nav_map(archive, manifest) or self._read_ncx_map(archive, opf_root, manifest)
            self._styled_heading_levels = _styled_heading_levels(archive, manifest)
            metadata = self._read_metadata(opf_root)
            chapters = self._read_spine_chapters(archive, opf_root, manifest, nav_map)
            chapters = _title_pages_before_the_contents(chapters, nav_map)
            chapters = _drop_ornament_images(chapters)
            chapters = self._normalize_spine_chapters(chapters, book_title=metadata.get("title"))

        return ParsedDocument(
            title=metadata.get("title"),
            author=metadata.get("author"),
            language=metadata.get("language"),
            chapters=chapters,
            metadata=metadata,
        )

    def _resolve_opf_path(self, archive: zipfile.ZipFile) -> str:
        container_root = _parse_xml_document(archive.read(_CONTAINER_PATH))
        rootfile = container_root.find(".//container:rootfile", _CONTAINER_NS)
        if rootfile is None:
            raise ValueError("EPUB container.xml is missing rootfile entry")
        full_path = rootfile.attrib.get("full-path")
        if not full_path:
            raise ValueError("EPUB rootfile entry does not include full-path")
        return full_path

    def _read_manifest(self, opf_root: ET.Element, opf_dir: str) -> dict[str, dict[str, str]]:
        manifest: dict[str, dict[str, str]] = {}
        for item in opf_root.findall(".//opf:manifest/opf:item", _OPF_NS):
            item_id = item.attrib["id"]
            manifest[item_id] = {
                "href": _join_path(opf_dir, item.attrib["href"]),
                "media_type": item.attrib.get("media-type", ""),
                "properties": item.attrib.get("properties", ""),
            }
        return manifest

    def _read_metadata(self, opf_root: ET.Element) -> dict[str, str]:
        metadata: dict[str, str] = {}
        title_refinements: dict[str, dict[str, str]] = {}
        for meta in opf_root.findall(".//opf:metadata/opf:meta", _OPF_NS):
            refines = _normalize_text(meta.attrib.get("refines", "")).lstrip("#")
            property_name = _normalize_text(meta.attrib.get("property"))
            value = _normalize_text("".join(meta.itertext()))
            if not refines or not property_name or not value:
                continue
            title_refinements.setdefault(refines, {})[property_name] = value

        title_elements = opf_root.findall(".//opf:metadata/dc:title", _OPF_NS)
        main_title: str | None = None
        subtitle: str | None = None
        fallback_titles: list[str] = []
        for title_element in title_elements:
            title_text = _strip_title_debris(_normalize_text("".join(title_element.itertext())))
            if not title_text:
                continue
            title_type = title_refinements.get(title_element.attrib.get("id", ""), {}).get("title-type", "").casefold()
            if title_type == "main" and main_title is None:
                main_title = title_text
                continue
            if title_type == "subtitle" and subtitle is None:
                subtitle = title_text
                continue
            fallback_titles.append(title_text)
        if main_title is None and fallback_titles:
            main_title = fallback_titles.pop(0)
        if subtitle is None:
            subtitle = next(
                (candidate for candidate in fallback_titles if candidate.casefold() != (main_title or "").casefold()),
                None,
            )

        author = opf_root.findtext(".//dc:creator", default=None, namespaces=_OPF_NS)
        language = opf_root.findtext(".//dc:language", default=None, namespaces=_OPF_NS)
        if main_title:
            metadata["title"] = main_title
        if subtitle and not _looks_like_metadata_filename(subtitle):
            metadata["subtitle"] = subtitle
        combined_title = compose_document_title(main_title, subtitle)
        if combined_title and not _looks_like_metadata_filename(combined_title):
            metadata["document_title_src"] = combined_title
        if author and not _looks_like_metadata_filename(author):
            metadata["author"] = _normalize_text(author)
        if language:
            metadata["language"] = _normalize_text(language)
        return metadata

    def _read_nav_map(
        self,
        archive: zipfile.ZipFile,
        manifest: dict[str, dict[str, str]],
    ) -> dict[str, str]:
        nav_items = [item for item in manifest.values() if "nav" in item["properties"].split()]
        if not nav_items:
            return {}

        nav_path = nav_items[0]["href"]
        try:
            nav_root = _qualify_xhtml(_parse_xml_document(archive.read(nav_path)))
        except ET.ParseError:
            logger.warning("Failed to parse EPUB nav document %s; proceeding without TOC", nav_path)
            return {}
        nav_dir = posixpath.dirname(nav_path)
        nav_map: dict[str, str] = {}
        toc_anchors: list[ET.Element] = []
        for nav in nav_root.findall(".//xhtml:nav", _XHTML_NS):
            epub_type = _normalize_text(nav.attrib.get(_EPUB_TYPE_ATTR) or nav.attrib.get("type") or "")
            epub_type_tokens = {token.casefold() for token in re.split(r"\s+", epub_type) if token}
            if "toc" not in epub_type_tokens:
                continue
            toc_anchors.extend(nav.findall(".//xhtml:a", _XHTML_NS))

        anchors = toc_anchors or nav_root.findall(".//xhtml:nav//xhtml:a", _XHTML_NS)
        for anchor in anchors:
            href = anchor.attrib.get("href", "")
            title = _normalize_text("".join(anchor.itertext()))
            if not href or not title:
                continue
            normalized_href = _join_path(nav_dir, href.split("#", 1)[0])
            # A file's first TOC entry is its chapter; later entries point at its sections (an O'Reilly
            # chapter file lists "1. Introduction", its sections, and last "Summary", which won before).
            nav_map.setdefault(posixpath.normpath(normalized_href), title)
        return nav_map

    def _read_ncx_map(
        self,
        archive: zipfile.ZipFile,
        opf_root: ET.Element,
        manifest: dict[str, dict[str, str]],
    ) -> dict[str, str]:
        """An EPUB 2 book's table of contents (``toc.ncx``, the spine's ``toc``): a file's first entry
        titles its chapter. Mastering Trading Psychology has no nav document, and every chapter came
        out untitled ("missing_chapter_title" blocked its review)."""
        spine = opf_root.find(".//opf:spine", _OPF_NS)
        item = manifest.get(spine.attrib.get("toc", "")) if spine is not None else None
        if item is None:
            item = next((entry for entry in manifest.values() if entry["media_type"] == "application/x-dtbncx+xml"), None)
        if item is None:
            return {}
        try:
            ncx_root = _parse_xml_document(archive.read(item["href"]))
        except (KeyError, ET.ParseError):
            logger.warning("Failed to read EPUB NCX %s; proceeding without TOC", item["href"])
            return {}
        ncx_dir = posixpath.dirname(item["href"])
        ncx_map: dict[str, str] = {}
        for nav_point in ncx_root.iter():
            if _local_name(nav_point.tag) != "navPoint":
                continue
            label = next((child for child in nav_point.iter() if _local_name(child.tag) == "text"), None)
            content = next((child for child in nav_point if _local_name(child.tag) == "content"), None)
            title = _normalize_text("".join(label.itertext())) if label is not None else ""
            src = content.attrib.get("src", "") if content is not None else ""
            if title and src:
                ncx_map.setdefault(posixpath.normpath(_join_path(ncx_dir, src.split("#", 1)[0])), title)
        return ncx_map

    def _read_spine_chapters(
        self,
        archive: zipfile.ZipFile,
        opf_root: ET.Element,
        manifest: dict[str, dict[str, str]],
        nav_map: dict[str, str],
    ) -> list[ParsedChapter]:
        chapters: list[ParsedChapter] = []
        seen_hrefs: set[str] = set()
        for itemref in opf_root.findall(".//opf:spine/opf:itemref", _OPF_NS):
            if itemref.attrib.get("linear", "").casefold() == "no":
                continue
            item_id = itemref.attrib["idref"]
            manifest_item = manifest.get(item_id)
            if manifest_item is None:
                continue
            if manifest_item["media_type"] not in {"application/xhtml+xml", "text/html"}:
                continue
            normalized_href = posixpath.normpath(manifest_item["href"])
            if normalized_href in seen_hrefs:
                continue
            seen_hrefs.add(normalized_href)
            chapters.append(
                self._parse_chapter(archive, manifest_item["href"], nav_map, len(chapters) + 1)
            )
        return chapters

    def _parse_chapter(
        self,
        archive: zipfile.ZipFile,
        href: str,
        nav_map: dict[str, str],
        chapter_index: int,
    ) -> ParsedChapter:
        raw = archive.read(href)
        try:
            chapter_root = _parse_xml_document(raw)
            chapter_root = _qualify_xhtml(chapter_root)
            body = chapter_root.find(".//xhtml:body", _XHTML_NS)
            if body is None:
                return ParsedChapter(chapter_id=f"chapter-{chapter_index}", href=href, title=None, blocks=[])
            blocks = self._extract_blocks(body, href)
        except ET.ParseError:
            blocks = _FallbackHTMLBlockExtractor(href).extract(raw.decode("utf-8", errors="replace"))
        blocks = _normalize_chapter_heading_levels(_dense_styled_heading_levels(blocks))
        nav_title = nav_map.get(posixpath.normpath(href))
        heading_title = next((block.text for block in blocks if block.block_type == "heading"), None)
        chapter_title = _named_chapter_label(self._resolve_chapter_title(nav_title, heading_title), blocks)

        return ParsedChapter(
            chapter_id=f"chapter-{chapter_index}",
            href=href,
            title=chapter_title,
            blocks=blocks,
            metadata={"source_path": href},
        )

    def _resolve_chapter_title(self, nav_title: str | None, heading_title: str | None) -> str | None:
        normalized_nav = _normalize_text(nav_title or "")
        normalized_heading = _normalize_text(heading_title or "")
        if normalized_nav and not (
            _looks_like_page_nav_label(normalized_nav) or _looks_like_generic_spine_nav_title(normalized_nav)
        ):
            return normalized_nav
        return normalized_heading or normalized_nav or None

    def _normalize_spine_chapters(
        self,
        chapters: list[ParsedChapter],
        *,
        book_title: str | None,
    ) -> list[ParsedChapter]:
        normalized: list[ParsedChapter] = []
        for chapter in chapters:
            if self._should_drop_empty_spine_chapter(chapter):
                continue
            if self._should_drop_toc_like_spine_chapter(chapter):
                continue
            if self._should_drop_titlepage_spine_chapter(chapter, book_title=book_title):
                continue
            normalized.append(chapter)
        return normalized

    def _should_drop_empty_spine_chapter(self, chapter: ParsedChapter) -> bool:
        return not any(_normalize_text(block.text) for block in chapter.blocks)

    def _should_drop_toc_like_spine_chapter(self, chapter: ParsedChapter) -> bool:
        normalized_title = _normalize_text(chapter.title or "").casefold()
        href = posixpath.basename(chapter.href).casefold()
        # A contents page the book's own TOC does not list opens with its title ("TABLE OF CONTENTS").
        first_text = _normalize_text(chapter.blocks[0].text).casefold() if chapter.blocks else ""
        if (
            normalized_title not in _TOC_LIKE_TITLES
            and first_text not in _TOC_LIKE_TITLES
            and not any(token in href for token in _TOC_LIKE_PATH_TOKENS)
        ):
            return False
        block_types = {block.block_type for block in chapter.blocks}
        if block_types - {"heading", "paragraph", "list_item"}:
            return False
        return len(chapter.blocks) >= 2

    def _should_drop_titlepage_spine_chapter(
        self,
        chapter: ParsedChapter,
        *,
        book_title: str | None,
    ) -> bool:
        href = posixpath.basename(chapter.href).casefold()
        if not any(token in href for token in _TITLEPAGE_PATH_TOKENS):
            return False
        if not chapter.blocks:
            return True
        normalized_book_title = _normalize_text(book_title or "").casefold()
        meaningful_texts = [_normalize_text(block.text) for block in chapter.blocks if _normalize_text(block.text)]
        if not meaningful_texts:
            return True
        if len(meaningful_texts) > 2 or not normalized_book_title:
            return False
        return all(text.casefold() == normalized_book_title or len(text.split()) <= 4 for text in meaningful_texts)

    def _extract_blocks(self, body: ET.Element, href: str) -> list[ParsedBlock]:
        """The chapter's blocks in reading order, with the structure around them in their metadata.

        Besides the block itself (``_block_type_for_element``), three O'Reilly-style structures are
        kept (``epub_structure_version`` 2):

        * lists: each ``<li>`` is a ``list_item`` with ``list_kind`` ("bullet" for ``<ul>``,
          "number" for ``<ol>``), ``list_level`` and, numbered, ``list_number``; a nested list is
          its own items one level down, not text folded into its parent item;
        * definition lists: a ``<dt>`` is a block of its own (it used to be dropped) marked
          ``definition_term``; the blocks of its ``<dd>`` are ``definition_description``;
        * boxes (``data-type`` note, tip, warning, caution, important, sidebar): every block
          inside carries the box's ``sidebar_id`` and ``sidebar_kind``; the box's heading ("Note",
          "What We Built") is its title (``sidebar_title``, a paragraph, not a section heading).
        """
        blocks: list[ParsedBlock] = []

        def emit(block_type: str, text: str, metadata: dict[str, object], anchor: str | None, context: dict[str, object]) -> None:
            metadata = {**metadata, "epub_structure_version": EPUB_STRUCTURE_VERSION}
            box = context.get("box")
            if isinstance(box, dict):
                metadata["sidebar_id"] = box["id"]
                metadata["sidebar_kind"] = box["kind"]
                if block_type == "heading" and not box["titled"] and not box["has_body"]:
                    # The box's own heading: its title, not a heading of the chapter's outline.
                    block_type = "paragraph"
                    metadata["sidebar_title"] = True
                    metadata["box_heading_tag"] = metadata.pop("tag", None)
                    metadata["tag"] = "p"
                    metadata.pop("heading_level", None)
                    box["titled"] = True
                else:
                    box["has_body"] = True
            definition = context.get("definition")
            if isinstance(definition, dict):
                metadata["definition_list_id"] = definition["id"]
                metadata[definition["role"]] = True
            listing = context.get("list")
            if block_type == "list_item" and isinstance(listing, dict):
                metadata["list_kind"] = listing["kind"]
                metadata["list_level"] = listing["level"]
                if listing["kind"] == "number":
                    metadata["list_number"] = listing["next"]
                listing["next"] = int(listing["next"]) + 1
            blocks.append(
                ParsedBlock(
                    block_type=block_type,
                    text=text,
                    source_path=href,
                    ordinal=len(blocks) + 1,
                    anchor=anchor,
                    metadata=metadata,
                    translatability=derive_translatability(block_type, metadata),
                )
            )

        def visit(element: ET.Element, inherited_anchor: str | None = None, context: dict[str, object] | None = None) -> None:
            context = context or {}
            local = _local_name(element.tag)
            class_tokens = _element_class_tokens(element)
            current_anchor = _normalize_text(element.attrib.get("id")) or inherited_anchor
            if _figure_like_container(local, class_tokens, element):
                figures = self._figure_blocks(element, href, starting_ordinal=len(blocks) + 1)
                box = context.get("box")
                for figure in figures:
                    figure.metadata["epub_structure_version"] = EPUB_STRUCTURE_VERSION
                    if isinstance(box, dict):
                        figure.metadata["sidebar_id"] = box["id"]
                        figure.metadata["sidebar_kind"] = box["kind"]
                        box["has_body"] = True
                blocks.extend(figures)
                return
            kind = _box_kind(element, local)
            if kind and "box" not in context:
                # Its own id (not the section's it inherits): two boxes of one section stay two.
                own_id = _normalize_text(element.attrib.get("id"))
                box_id = f"{href}#{own_id or f'{kind}-{len(blocks) + 1}'}"
                context = {**context, "box": {"id": box_id, "kind": kind, "titled": False, "has_body": False}}
            if local in {"ul", "ol"}:
                parent = context.get("list")
                level = int(parent["level"]) + 1 if isinstance(parent, dict) else 1
                try:
                    first = int(element.attrib.get("start", "1"))
                except ValueError:
                    first = 1
                context = {**context, "list": {"kind": "number" if local == "ol" else "bullet", "level": level, "next": first}}
            if local == "dl":
                own_id = _normalize_text(element.attrib.get("id"))
                context = {**context, "definition_list": f"{href}#{own_id or f'dl-{len(blocks) + 1}'}"}
            if local in {"dt", "dd"} and context.get("definition_list"):
                role = "definition_term" if local == "dt" else "definition_description"
                context = {**context, "definition": {"id": context["definition_list"], "role": role}}
            block_type = self._block_type_for_element(element, local)
            if local == "dd" and _has_block_child(element):
                block_type = None
            if block_type:
                nested_lists = _nested_lists(element) if local == "li" else []
                own = element
                if nested_lists:
                    # The item's own words; its nested list follows as items one level down.
                    own = copy.deepcopy(element)
                    for parent in list(own.iter()):
                        for child in list(parent):
                            if _local_name(child.tag) in {"ul", "ol"}:
                                parent.remove(child)
                text, metadata = self._block_text_and_metadata(own, local, href)
                if text or metadata.get("image_src"):
                    emit(block_type, text, metadata, current_anchor, context)
                    for nested in nested_lists:
                        visit(nested, current_anchor, context)
                    return

            # Standalone <img> outside any recognised block container —
            # emit a synthetic paragraph so the image is preserved in exports.
            if local == "img":
                src = _normalize_text(element.attrib.get("src"))
                if src:
                    alt = _normalize_text(element.attrib.get("alt", ""))
                    img_metadata: dict[str, object] = {"tag": "p"}
                    img_metadata["image_src"] = src
                    img_metadata["image_path"] = _join_path(posixpath.dirname(href), src)
                    if alt:
                        img_metadata["image_alt"] = alt
                    _mark_image_only_metadata_nontranslatable(img_metadata)
                    img_metadata["image_caption_generated"] = "alt" if alt else "placeholder"
                    emit("paragraph", alt or "[Image]", img_metadata, current_anchor, context)
                return

            for child in list(element):
                visit(child, current_anchor, context)

        for child in list(body):
            visit(child)
        return blocks

    def _figure_blocks(
        self,
        element: ET.Element,
        href: str,
        *,
        starting_ordinal: int,
    ) -> list[ParsedBlock]:
        class_tokens = _element_class_tokens(element)
        if not _figure_like_container(_local_name(element.tag), class_tokens, element):
            return []

        image = _first_descendant(element, {"img"})
        image_src = ""
        image_alt = ""
        if image is not None:
            image_src = _normalize_text(image.attrib.get("src"))
            image_alt = _normalize_text(image.attrib.get("alt"))

        image_id = _normalize_text(image.attrib.get("id")) if image is not None else ""
        base_anchor = _normalize_text(element.attrib.get("id")) or image_id or f"figure-{starting_ordinal}"
        figure_anchor = f"{base_anchor}-figure"
        caption_anchor = f"{base_anchor}-caption"

        figure_metadata: dict[str, object] = {"tag": "figure"}
        if image_src:
            figure_metadata["image_src"] = image_src
            figure_metadata["image_path"] = _join_path(posixpath.dirname(href), image_src)
        if image_alt:
            figure_metadata["image_alt"] = image_alt
        figure_metadata["translatable"] = False
        figure_metadata["nontranslatable_reason"] = "epub_figure_artifact"

        caption_text = _figure_caption_text(element)
        caption_metadata: dict[str, object] = {
            "tag": "figcaption",
            "caption_for_source_anchor": f"{href}#{figure_anchor}",
        }
        if image_src:
            caption_metadata["image_src"] = image_src
            caption_metadata["image_path"] = _join_path(posixpath.dirname(href), image_src)
        if image_alt:
            caption_metadata["image_alt"] = image_alt

        figure_metadata["linked_caption_source_anchor"] = f"{href}#{caption_anchor}"

        figure_text = image_alt or "[Image]"
        figure_block = ParsedBlock(
            block_type="figure",
            text=figure_text,
            source_path=href,
            ordinal=starting_ordinal,
            anchor=figure_anchor,
            metadata=figure_metadata,
            translatability=derive_translatability("figure", figure_metadata),
        )

        blocks = [figure_block]
        if caption_text:
            blocks.append(
                ParsedBlock(
                    block_type="caption",
                    text=caption_text,
                    source_path=href,
                    ordinal=starting_ordinal + 1,
                    anchor=caption_anchor,
                    metadata=caption_metadata,
                    translatability=derive_translatability("caption", caption_metadata),
                )
            )
        return blocks

    def _block_type_for_element(self, element: ET.Element, local_name: str) -> str | None:
        class_tokens = _element_class_tokens(element)
        if _figure_like_container(local_name, class_tokens, element):
            return "caption"
        if local_name in _HEADING_TAGS:
            return "heading"
        if local_name == "p" and self._styled_heading_level(element, class_tokens) is not None:
            return "heading"
        if local_name in _BLOCK_TAGS:
            return _BLOCK_TAGS[local_name]
        epub_type = element.attrib.get("{http://www.idpf.org/2007/ops}type", "")
        if "footnote" in epub_type:
            return "footnote"
        if local_name == "aside" and "footnote" in epub_type:
            return "footnote"
        return None

    def _styled_heading_level(self, element: ET.Element, class_tokens) -> int | None:
        """A paragraph the book's stylesheet sets as a heading (``<p class="heading1">``, bold and
        enlarged): its level, or None. A long one is a paragraph with a run-in title, not a heading."""
        levels = getattr(self, "_styled_heading_levels", None) or {}
        level = min((levels[token] for token in class_tokens if token in levels), default=None)
        if level is None:
            return None
        text = _normalize_text("".join(element.itertext()))
        if not text or len(text) > _STYLED_HEADING_MAX_CHARS:
            return None
        return level

    def _block_text_and_metadata(
        self,
        element: ET.Element,
        local_name: str,
        href: str,
    ) -> tuple[str, dict[str, object]]:
        metadata: dict[str, object] = {"tag": local_name}
        if local_name in _HEADING_TAGS and len(local_name) == 2 and local_name[1].isdigit():
            metadata["heading_level"] = int(local_name[1])
        class_tokens = _element_class_tokens(element)
        if local_name == "p" and (styled_level := self._styled_heading_level(element, class_tokens)) is not None:
            metadata["heading_level"] = styled_level
            metadata["styled_heading"] = True
        if _figure_like_container(local_name, class_tokens, element):
            image = _first_descendant(element, {"img"})
            if image is not None:
                src = image.attrib.get("src")
                alt = _normalize_text(image.attrib.get("alt", ""))
                if src:
                    metadata["image_src"] = src
                    metadata["image_path"] = _join_path(posixpath.dirname(href), src)
                if alt:
                    metadata["image_alt"] = alt
            caption_text = _figure_caption_text(element)
            if caption_text:
                return caption_text, metadata
            _mark_image_only_metadata_nontranslatable(metadata)
            if metadata.get("image_alt"):
                metadata["image_caption_generated"] = "alt"
                return metadata["image_alt"], metadata
            metadata["image_caption_generated"] = "placeholder"
            return "[Image]", metadata
        if local_name == "pre":
            return _normalize_preformatted_text("".join(element.itertext())), metadata
        if local_name == "table":
            rows: list[str] = []
            for row in element.iter():
                if _local_name(row.tag) != "tr":
                    continue
                cells = [
                    _normalize_text("".join(cell.itertext()))
                    for cell in list(row)
                    if _local_name(cell.tag) in {"th", "td"}
                ]
                cleaned_cells = [cell for cell in cells if cell]
                if cleaned_cells:
                    rows.append(" | ".join(cleaned_cells))
            if rows:
                return "\n".join(rows), metadata
        text = _extract_rich_text(element)
        # Count inline format markers
        inline_code_count = text.count('`') // 2  # backtick pairs
        bold_count = text.count('**') // 2  # double-star pairs
        italic_singles = text.count('*') - text.count('**') * 2
        italic_count = italic_singles // 2 if italic_singles > 0 else 0
        if inline_code_count or bold_count or italic_count:
            metadata["has_inline_formatting"] = True
            metadata["inline_format_counts"] = {
                "code": inline_code_count,
                "bold": bold_count,
                "italic": italic_count,
            }
        return text, metadata
