from __future__ import annotations

import html
import posixpath
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

from book_agent.domain.structure.models import ParsedBlock, ParsedChapter, ParsedDocument

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


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _normalize_preformatted_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")


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


def _join_path(base_dir: str, href: str) -> str:
    if not base_dir:
        return href
    return posixpath.normpath(posixpath.join(base_dir, href))


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

        if self._active_block is None:
            block_type = _block_type_for_html(tag, attr_map)
            if block_type:
                self._active_block = {
                    "tag": tag,
                    "block_type": block_type,
                    "anchor": attr_map.get("id"),
                    "metadata": {"tag": tag},
                    "text_parts": [],
                    "source_path": self.href,
                }
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
            self._append_text(f"</{tag}>")
            return
        if self._active_block is not None and self._active_block["tag"] == tag:
            self._finalize_active_block()

    def handle_data(self, data: str) -> None:
        if not self._inside_content():
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
        raw_text = "".join(text_parts)
        text = _normalize_preformatted_text(raw_text) if block_type == "code" else _normalize_text(raw_text)
        metadata = dict(self._active_block["metadata"])
        if not text and metadata.get("image_alt"):
            text = str(metadata["image_alt"])
        elif not text and metadata.get("image_src"):
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
        title = opf_root.findtext(".//dc:title", default=None, namespaces=_OPF_NS)
        author = opf_root.findtext(".//dc:creator", default=None, namespaces=_OPF_NS)
        language = opf_root.findtext(".//dc:language", default=None, namespaces=_OPF_NS)
        if title:
            metadata["title"] = _normalize_text(title)
        if author:
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
        for anchor in nav_root.findall(".//xhtml:nav//xhtml:a", _XHTML_NS):
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
        for idx, itemref in enumerate(opf_root.findall(".//opf:spine/opf:itemref", _OPF_NS), start=1):
            item_id = itemref.attrib["idref"]
            manifest_item = manifest.get(item_id)
            if manifest_item is None:
                continue
            if manifest_item["media_type"] not in {"application/xhtml+xml", "text/html"}:
                continue
            chapters.append(self._parse_chapter(archive, manifest_item["href"], nav_map, idx))
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
        chapter_title = nav_title or heading_title

        return ParsedChapter(
            chapter_id=f"chapter-{chapter_index}",
            href=href,
            title=chapter_title,
            blocks=blocks,
            metadata={"source_path": href},
        )

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

    def _block_text_and_metadata(self, element: ET.Element, local_name: str, href: str) -> tuple[str, dict[str, str]]:
        metadata: dict[str, str] = {"tag": local_name}
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
            if metadata.get("image_alt"):
                return metadata["image_alt"], metadata
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
        return _normalize_text("".join(element.itertext())), metadata
