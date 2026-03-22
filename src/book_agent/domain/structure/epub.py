from __future__ import annotations

import html
import posixpath
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

from book_agent.domain.structure.models import ParsedBlock, ParsedChapter, ParsedDocument
from book_agent.domain.document_titles import compose_document_title

_CONTAINER_PATH = "META-INF/container.xml"
_CONTAINER_NS = {"container": "urn:oasis:names:tc:opendocument:xmlns:container"}
_OPF_NS = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}
_XHTML_NS = {"xhtml": "http://www.w3.org/1999/xhtml", "epub": "http://www.idpf.org/2007/ops"}
_BLOCK_TAGS = {
    "p": "paragraph",
    "blockquote": "quote",
    "pre": "code",
    "li": "list_item",
    "figcaption": "caption",
    "table": "table",
    "math": "code",
    "svg": "code",
}
_HEADING_TAGS = {f"h{i}" for i in range(1, 7)}
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


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _normalize_preformatted_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")


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
                parts.append(f"{markers[0]}{inner}{markers[1]}")
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


def _looks_like_page_nav_label(value: str | None) -> bool:
    normalized = _normalize_text(value or "")
    if not normalized:
        return False
    return bool(_PAGE_LABEL_PATTERN.fullmatch(normalized))


def _looks_like_generic_spine_nav_title(value: str | None) -> bool:
    return _normalize_text(value or "").casefold() in _GENERIC_SPINE_NAV_TITLES


def _parse_xml_document(raw: bytes) -> ET.Element:
    # EPUB XHTML frequently includes named HTML entities like &times; that
    # ElementTree's XML parser does not resolve by default.
    return ET.fromstring(html.unescape(raw.decode("utf-8")))


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

        if self._active_block is not None and tag == "img":
            metadata = self._active_block["metadata"]
            assert isinstance(metadata, dict)
            source_path = self._active_block["source_path"]
            assert isinstance(source_path, str)
            src = attr_map.get("src", "")
            alt = _normalize_text(attr_map.get("alt", ""))
            if src:
                metadata["image_src"] = src
                metadata["image_path"] = _join_path(posixpath.dirname(source_path), src)
            if alt:
                metadata["image_alt"] = alt
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
                active_block = {
                    "tag": tag,
                    "block_type": block_type,
                    "anchor": attr_map.get("id"),
                    "metadata": {"tag": tag},
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
            nav_map = self._read_nav_map(archive, manifest)
            metadata = self._read_metadata(opf_root)
            chapters = self._read_spine_chapters(archive, opf_root, manifest, nav_map)
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
            title_text = _normalize_text("".join(title_element.itertext()))
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
        nav_root = _parse_xml_document(archive.read(nav_path))
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
            nav_map[posixpath.normpath(normalized_href)] = title
        return nav_map

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
            body = chapter_root.find(".//xhtml:body", _XHTML_NS)
            if body is None:
                return ParsedChapter(chapter_id=f"chapter-{chapter_index}", href=href, title=None, blocks=[])
            blocks = self._extract_blocks(body, href)
        except ET.ParseError:
            blocks = _FallbackHTMLBlockExtractor(href).extract(raw.decode("utf-8", errors="replace"))
        nav_title = nav_map.get(posixpath.normpath(href))
        heading_title = next((block.text for block in blocks if block.block_type == "heading"), None)
        chapter_title = self._resolve_chapter_title(nav_title, heading_title)

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
        if normalized_title not in _TOC_LIKE_TITLES and not any(token in href for token in _TOC_LIKE_PATH_TOKENS):
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
        blocks: list[ParsedBlock] = []

        def visit(element: ET.Element) -> None:
            local = _local_name(element.tag)
            block_type = self._block_type_for_element(element, local)
            if block_type:
                text, metadata = self._block_text_and_metadata(element, local, href)
                if text or metadata.get("image_src"):
                    blocks.append(
                        ParsedBlock(
                            block_type=block_type,
                            text=text,
                            source_path=href,
                            ordinal=len(blocks) + 1,
                            anchor=element.attrib.get("id"),
                            metadata=metadata,
                        )
                    )
                    return

            for child in list(element):
                visit(child)

        for child in list(body):
            visit(child)
        return blocks

    def _block_type_for_element(self, element: ET.Element, local_name: str) -> str | None:
        class_tokens = _element_class_tokens(element)
        if _figure_like_container(local_name, class_tokens, element):
            return "caption"
        if local_name in _HEADING_TAGS:
            return "heading"
        if local_name in _BLOCK_TAGS:
            return _BLOCK_TAGS[local_name]
        epub_type = element.attrib.get("{http://www.idpf.org/2007/ops}type", "")
        if "footnote" in epub_type:
            return "footnote"
        if local_name == "aside" and "footnote" in epub_type:
            return "footnote"
        return None

    def _block_text_and_metadata(
        self,
        element: ET.Element,
        local_name: str,
        href: str,
    ) -> tuple[str, dict[str, object]]:
        metadata: dict[str, object] = {"tag": local_name}
        class_tokens = _element_class_tokens(element)
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
