"""Web pages as a translation source: one page in, the same page in Chinese out.

A captured page is kept as HTML (see ``book_agent.ingestion.web``). This module
owns the contract between that snapshot, the parser and the exporter:

- ``parse_html`` reads HTML into a lossless tree: serializing it gives back the
  exact source text, so everything the translation does not touch (styles,
  classes, layout markup, images) survives byte for byte;
- ``mark_translatable_nodes`` stamps ``data-ba-node="N"`` on every element that
  holds a run of prose, wrapping loose text in a ``<span>`` where a container
  mixes text with blocks;
- ``WebPageParser`` turns the marked snapshot into one chapter of blocks, each
  carrying its node number;
- ``translate_web_page`` writes translations back into those nodes and nothing
  else, so the Chinese page keeps the original layout.

Inline markup travels through translation as light markers: ``[text][n]`` for the
node's n-th kept inline element (a link, a footnote ``<sup>``, a ``<sub>``, ...),
``**bold**``, ``*emphasis*`` and ```code```. The exporter turns each marker back
into the node's own original tag (classes and styles included); a marker the
source did not have stays literal text.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from book_agent.domain.structure.models import ParsedBlock, ParsedChapter, ParsedDocument

NODE_ATTR = "data-ba-node"
SOURCE_URL_META = "book-agent:source-url"
PAGE_HREF = "index.html"

_VOID = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
)
# Elements whose content is never prose to translate.
_SKIP = frozenset(
    {
        "script", "style", "noscript", "template", "svg", "math", "pre", "code", "kbd", "samp", "textarea",
        "select", "iframe", "canvas", "video", "audio", "object", "head",
    }
)
# Inline elements a prose run may contain; anything else ends the run.
_PHRASING = frozenset(
    {
        "a", "abbr", "b", "bdi", "bdo", "br", "cite", "code", "data", "del", "dfn", "em", "font", "i", "ins",
        "kbd", "label", "mark", "q", "s", "samp", "small", "span", "strong", "sub", "sup", "time", "u", "var", "wbr",
    }
)
# Opening one of these closes an open <p> (HTML's implied end tag).
_CLOSES_P = frozenset(
    {
        "address", "article", "aside", "blockquote", "details", "div", "dl", "fieldset", "figcaption", "figure",
        "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "main", "menu", "nav", "ol", "p",
        "pre", "section", "table", "ul",
    }
)
_IMPLIED_SIBLINGS = {"li": {"li"}, "dt": {"dt", "dd"}, "dd": {"dt", "dd"}, "tr": {"tr"}, "td": {"td", "th"},
                     "th": {"td", "th"}, "option": {"option"}}
_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_MARKERS = {"strong": "**", "b": "**", "em": "*", "i": "*", "code": "`"}
# Inline elements kept through translation as ``[text][n]`` references.
_REFERENCED = frozenset({"a", "sup", "sub", "mark", "abbr", "q", "cite"})
_LATIN = re.compile(r"[A-Za-z]")
_SPACE = re.compile(r"\s+")
_NODE_ATTR_IN_TAG = re.compile(rf'\s{NODE_ATTR}="\d+"')


@dataclass(slots=True)
class Text:
    raw: str


@dataclass(slots=True)
class Element:
    tag: str
    attrs: dict[str, str | None]
    start: str
    end: str = ""
    children: list[Text | Element] = field(default_factory=list)


def _start_tag(tag: str, attrs: dict[str, str | None]) -> str:
    parts = [tag]
    for name, value in attrs.items():
        parts.append(name if value is None else f'{name}="{html.escape(value, quote=True)}"')
    return f"<{' '.join(parts)}>"


def set_attrs(element: Element, attrs: dict[str, str | None]) -> None:
    """Replace an element's attributes (its start tag is regenerated)."""
    element.attrs = attrs
    element.start = _start_tag(element.tag, attrs)


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.root = Element("#document", {}, "")
        self.stack: list[Element] = [self.root]

    def _append(self, node: Text | Element) -> None:
        self.stack[-1].children.append(node)

    def _close_to(self, index: int) -> None:
        del self.stack[index:]

    def _open_index(self, tags: set[str] | frozenset[str], *, stop: frozenset[str] = frozenset()) -> int | None:
        for index in range(len(self.stack) - 1, 0, -1):
            tag = self.stack[index].tag
            if tag in tags:
                return index
            if tag in stop:
                return None
        return None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _CLOSES_P:
            index = self._open_index({"p"}, stop=frozenset({"div", "section", "article", "li", "td", "th", "button"}))
            if index is not None:
                self._close_to(index)
        siblings = _IMPLIED_SIBLINGS.get(tag)
        if siblings:
            index = self._open_index(siblings, stop=frozenset({"ul", "ol", "dl", "table", "tbody", "thead", "select"}))
            if index is not None:
                self._close_to(index)
        element = Element(tag, dict(attrs), self.get_starttag_text() or _start_tag(tag, dict(attrs)))
        self._append(element)
        if tag not in _VOID:
            self.stack.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._append(Element(tag, dict(attrs), self.get_starttag_text() or _start_tag(tag, dict(attrs))))

    def handle_endtag(self, tag: str) -> None:
        index = self._open_index({tag})
        if index is None:
            # A stray end tag: browsers ignore it; kept as text so serializing stays byte-exact.
            self._append(Text(f"</{tag}>"))
            return
        self.stack[index].end = f"</{tag}>"
        self._close_to(index)

    def handle_data(self, data: str) -> None:
        self._append(Text(data))

    def handle_entityref(self, name: str) -> None:
        self._append(Text(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        self._append(Text(f"&#{name};"))

    def handle_comment(self, data: str) -> None:
        self._append(Text(f"<!--{data}-->"))

    def handle_decl(self, decl: str) -> None:
        self._append(Text(f"<!{decl}>"))

    def handle_pi(self, data: str) -> None:
        self._append(Text(f"<?{data}>"))

    def unknown_decl(self, data: str) -> None:
        self._append(Text(f"<![{data}]>"))


def parse_html(document: str) -> Element:
    builder = _TreeBuilder()
    builder.feed(document)
    builder.close()
    return builder.root


def serialize(node: Text | Element) -> str:
    if isinstance(node, Text):
        return node.raw
    return node.start + "".join(serialize(child) for child in node.children) + node.end


def serialize_children(node: Element) -> str:
    return "".join(serialize(child) for child in node.children)


def remove_elements(node: Element, predicate) -> None:
    """Drop every element (with its content) for which ``predicate`` is true."""
    node.children = [
        child for child in node.children if not (isinstance(child, Element) and predicate(child))
    ]
    for child in node.children:
        if isinstance(child, Element):
            remove_elements(child, predicate)


def iter_elements(node: Element):
    for child in node.children:
        if isinstance(child, Element):
            yield child
            yield from iter_elements(child)


def find_first(node: Element, tag: str) -> Element | None:
    return next((element for element in iter_elements(node) if element.tag == tag), None)


def _is_comment_or_decl(node: Text) -> bool:
    return node.raw.startswith(("<!", "<?"))


def _text_value(node: Text) -> str:
    return "" if _is_comment_or_decl(node) else html.unescape(node.raw)


def _plain_text(node: Text | Element) -> str:
    if isinstance(node, Text):
        return _text_value(node)
    if node.tag in {"script", "style", "template"}:
        return ""
    return "".join(_plain_text(child) for child in node.children)


def _has_latin(text: str) -> bool:
    return bool(_LATIN.search(text))


def _skipped(element: Element) -> bool:
    if element.tag in _SKIP or "hidden" in element.attrs:
        return True
    if (element.attrs.get("translate") or "").lower() == "no":
        return True
    classes = (element.attrs.get("class") or "").split()
    return "notranslate" in classes or "no-translate" in classes


def _is_prose(element: Element) -> bool:
    """Only text and inline elements below (inline <code> allowed): the whole element is one run."""
    for child in element.children:
        if isinstance(child, Element):
            if child.tag not in _PHRASING or (child.tag != "code" and _skipped(child)):
                return False
            if child.tag != "code" and not _is_prose(child):
                return False
    return True


def _is_prose_node(node: Text | Element) -> bool:
    if isinstance(node, Text):
        return True
    return node.tag in _PHRASING and not (node.tag != "code" and _skipped(node)) and _is_prose(node)


def _narrow(element: Element) -> Element:
    """Descend through wrappers with a single inline child, so ``<li><a>Research</a></li>`` marks the link.

    Marking the innermost element keeps a link's tag outside the translated text:
    a model that drops the ``[..][1]`` marker around a one-word label cannot lose the link.
    """
    while True:
        elements = [child for child in element.children if isinstance(child, Element)]
        loose = "".join(_text_value(child) for child in element.children if isinstance(child, Text))
        if len(elements) != 1 or loose.strip() or elements[0].tag in {"br", "code"}:
            return element
        element = elements[0]


class _Marker:
    def __init__(self) -> None:
        self.count = 0

    def _stamp(self, element: Element) -> None:
        attrs = dict(element.attrs)
        attrs[NODE_ATTR] = str(self.count)
        self.count += 1
        set_attrs(element, attrs)

    def visit(self, element: Element) -> None:
        if element.tag == "title":
            if _has_latin(_plain_text(element)):
                self._stamp(element)
            return
        if element.tag == "head":
            title = find_first(element, "title")
            if title is not None:
                self.visit(title)
            return
        if _skipped(element):
            return
        if element.tag != "#document" and element.tag not in {"html", "body"} and _is_prose(element):
            if _has_latin(_plain_text(element)):
                self._stamp(_narrow(element))
            return
        run: list[Text | Element] = []
        children: list[Text | Element] = []

        def flush() -> None:
            if not run:
                return
            text = "".join(_plain_text(node) for node in run)
            if _has_latin(text):
                meaningful = [node for node in run if not (isinstance(node, Text) and not _text_value(node).strip())]
                if len(meaningful) == 1 and isinstance(meaningful[0], Element):
                    self._stamp(_narrow(meaningful[0]))
                    children.extend(run)
                else:
                    wrapper = Element("span", {}, "", "</span>", list(run))
                    self._stamp(wrapper)
                    children.append(wrapper)
            else:
                children.extend(run)
            run.clear()

        for child in element.children:
            if isinstance(child, Text) and _is_comment_or_decl(child):
                flush()
                children.append(child)
            elif _is_prose_node(child):
                run.append(child)
            else:
                flush()
                children.append(child)
                self.visit(child)
        flush()
        element.children = children


def mark_translatable_nodes(root: Element) -> int:
    """Stamp every prose run with ``data-ba-node``; returns how many were marked."""
    marker = _Marker()
    marker.visit(root)
    return marker.count


def marked_nodes(root: Element) -> list[tuple[int, Element]]:
    nodes = []
    for element in iter_elements(root):
        value = element.attrs.get(NODE_ATTR)
        if value is not None and value.isdigit():
            nodes.append((int(value), element))
    return nodes


@dataclass(frozen=True, slots=True)
class Reference:
    """A kept inline element and the kept elements around it (outermost first)."""

    element: Element
    wrappers: tuple[Element, ...] = ()


@dataclass(slots=True)
class InlineTags:
    """The node's own inline tags, in document order, for turning markers back into markup."""

    references: list[Reference] = field(default_factory=list)
    by_marker: dict[str, list[Element]] = field(default_factory=dict)


def _is_referenced(element: Element) -> bool:
    return element.tag in _REFERENCED and (element.tag != "a" or bool(element.attrs.get("href")))


def _contains_referenced(element: Element) -> bool:
    return any(isinstance(child, Element) and (_is_referenced(child) or _contains_referenced(child))
               for child in element.children)


def _source_text(element: Element, tags: InlineTags, wrappers: tuple[Element, ...] = ()) -> str:
    parts: list[str] = []
    for child in element.children:
        if isinstance(child, Text):
            parts.append(_text_value(child))
            continue
        if child.tag == "br":
            parts.append(" ")
            continue
        referenced = _is_referenced(child)
        if referenced and _contains_referenced(child):
            # References do not nest: the innermost one is kept and re-wrapped in this one on export.
            parts.append(_source_text(child, tags, (*wrappers, child)))
            continue
        inner = _source_text(child, tags, wrappers) if child.tag != "code" else _plain_text(child)
        if not inner.strip():
            parts.append(inner)
            continue
        if referenced:
            tags.references.append(Reference(child, wrappers))
            parts.append(f"[{inner.strip()}][{len(tags.references)}]")
        elif child.tag in _MARKERS:
            marker = _MARKERS[child.tag]
            tags.by_marker.setdefault(marker, []).append(child)
            leading = inner[: len(inner) - len(inner.lstrip())]
            trailing = inner[len(inner.rstrip()):]
            parts.append(f"{leading}{marker}{inner.strip()}{marker}{trailing}")
        else:
            parts.append(inner)
    return "".join(parts)


def node_source_text(element: Element) -> tuple[str, InlineTags]:
    tags = InlineTags()
    return _SPACE.sub(" ", _source_text(element, tags)).strip(), tags


def _block_type(element: Element, ancestors: list[str]) -> str:
    chain = [element.tag, *reversed(ancestors)]
    for tag in chain:
        if tag in _HEADINGS:
            return "heading"
        if tag == "figcaption":
            return "caption"
        if tag == "blockquote":
            return "quote"
        if tag in {"li", "dd", "dt"}:
            return "list_item"
        if tag in {"p", "td", "th", "article", "section", "main", "body"}:
            break
    return "paragraph"


def _collect_blocks(root: Element) -> list[tuple[int, Element, list[str]]]:
    found: list[tuple[int, Element, list[str]]] = []

    def walk(element: Element, ancestors: list[str]) -> None:
        for child in element.children:
            if not isinstance(child, Element):
                continue
            value = child.attrs.get(NODE_ATTR)
            if value is not None and value.isdigit():
                found.append((int(value), child, ancestors))
                continue
            walk(child, [*ancestors, child.tag])

    walk(root, [])
    return found


# Site navigation, banners and footers: short labels repeated on every page of the site.
WEB_REGION_CHROME = "chrome"


def _is_chrome(ancestors: list[str]) -> bool:
    """Inside <nav> or <footer>, or a page <header> outside the article itself."""
    if "nav" in ancestors or "footer" in ancestors:
        return True
    if "header" in ancestors:
        header_at = ancestors.index("header")
        return not any(tag in {"main", "article"} for tag in ancestors[:header_at])
    return False


def page_source_url(root: Element) -> str | None:
    for element in iter_elements(root):
        if element.tag == "meta" and element.attrs.get("name") == SOURCE_URL_META:
            return element.attrs.get("content") or None
    return None


class WebPageParser:
    """A captured page as one chapter; each marked node is one block."""

    def parse(self, file_path: str | Path) -> ParsedDocument:
        root = parse_html(Path(file_path).read_text(encoding="utf-8"))
        html_element = find_first(root, "html")
        language = (html_element.attrs.get("lang") if html_element is not None else None) or "en"
        source_url = page_source_url(root)
        blocks: list[ParsedBlock] = []
        page_title: str | None = None
        for node_id, element, ancestors in _collect_blocks(root):
            text, _tags = node_source_text(element)
            if not text:
                continue
            if element.tag == "title":
                page_title = page_title or _plain_text(element).strip() or None
            block_type = _block_type(element, ancestors)
            metadata: dict[str, object] = {"tag": element.tag, "web_node": node_id}
            heading = next((tag for tag in [element.tag, *reversed(ancestors)] if tag in _HEADINGS), None)
            if block_type == "heading" and heading:
                metadata["heading_level"] = int(heading[1])
            if element.tag == "title":
                metadata["web_role"] = "page_title"
            if _is_chrome(ancestors):
                metadata["web_region"] = WEB_REGION_CHROME
            blocks.append(
                ParsedBlock(
                    block_type=block_type,
                    text=text,
                    source_path=PAGE_HREF,
                    ordinal=len(blocks) + 1,
                    anchor=f"ba-node-{node_id}",
                    metadata=metadata,
                )
            )
        title = page_title or next((block.text for block in blocks if block.block_type == "heading"), None)
        chapter = ParsedChapter(
            chapter_id="chapter-1",
            href=PAGE_HREF,
            title=title,
            blocks=blocks,
            metadata={"source_path": PAGE_HREF, **({"source_url": source_url} if source_url else {})},
        )
        return ParsedDocument(
            title=title,
            author=None,
            language=language.split("-")[0].lower(),
            chapters=[chapter],
            metadata={"title": title, "language": language, **({"source_url": source_url} if source_url else {})},
        )


_MARKUP = re.compile(r"\[(?P<ref_text>[^\[\]]+)\]\[(?P<ref>\d+)\]|(?P<marker>\*\*|\*|`)(?P<inner>.+?)(?P=marker)")


def _end_tag(element: Element) -> str:
    return element.end or f"</{element.tag}>"


def render_translation(translation: str, tags: InlineTags) -> str:
    """Translated text as HTML, markers turned back into the node's original inline tags."""
    used: dict[str, int] = {}
    out: list[str] = []
    position = 0
    for match in _MARKUP.finditer(translation):
        if match.group("ref") is not None:
            index = int(match.group("ref")) - 1
            if not 0 <= index < len(tags.references):
                continue
            reference = tags.references[index]
            inner = render_translation(match.group("ref_text"), InlineTags(by_marker=tags.by_marker))
            out.append(html.escape(translation[position : match.start()], quote=False))
            out.append("".join(wrapper.start for wrapper in reference.wrappers))
            out.append(f"{reference.element.start}{inner}{_end_tag(reference.element)}")
            out.append("".join(_end_tag(wrapper) for wrapper in reversed(reference.wrappers)))
            position = match.end()
            continue
        marker = match.group("marker")
        available = tags.by_marker.get(marker) or []
        if not available:
            continue
        count = used.get(marker, 0)
        element = available[min(count, len(available) - 1)]
        used[marker] = count + 1
        inner = (
            html.escape(match.group("inner"), quote=False)
            if marker == "`"
            else render_translation(match.group("inner"), InlineTags(references=tags.references))
        )
        out.append(html.escape(translation[position : match.start()], quote=False))
        out.append(f"{element.start}{inner}{_end_tag(element)}")
        position = match.end()
    out.append(html.escape(translation[position:], quote=False))
    return "".join(out)


def translate_web_page(document: str, translations: dict[int, str], *, lang: str = "zh-CN") -> tuple[str, int]:
    """The snapshot with each translated node's content replaced; returns (html, nodes patched)."""
    root = parse_html(document)
    patched = 0
    for node_id, element in marked_nodes(root):
        translation = (translations.get(node_id) or "").strip()
        if not translation:
            continue
        _source, tags = node_source_text(element)
        rendered = render_translation(translation, tags)
        element.children = [Text(rendered if element.tag != "title" else html.escape(translation, quote=False))]
        patched += 1
    html_element = find_first(root, "html")
    if html_element is not None:
        attrs = dict(html_element.attrs)
        attrs["lang"] = lang
        set_attrs(html_element, attrs)
    return _NODE_ATTR_IN_TAG.sub("", serialize(root)), patched
