"""A translated web page as a Markdown article: the reading copy (and blog post) of a web document.

The page's translated nodes are written back into the captured snapshot first (as for the Chinese
web page), so the Markdown keeps what the page itself shows around them: pictures with their
captions, tables, code, links, emphasis, lists and quotes. Site menus, banners and footers are left
out: only the page's <main> (or <article>, or the <body> without <nav>, <footer> and page <header>)
is read. The bilingual edition sets each translated passage's English, as a quote, under it.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urljoin, urlsplit

from book_agent.domain.structure import web

_DROPPED = frozenset(
    {
        "script", "style", "noscript", "template", "svg", "math", "iframe", "canvas", "object", "head", "title",
        "button", "form", "input", "select", "textarea", "nav", "footer", "dialog", "audio",
    }
)
_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_BLOCKS = frozenset(
    {
        "address", "article", "aside", "blockquote", "dd", "details", "div", "dl", "dt", "figcaption", "figure",
        "footer", "header", "hr", "li", "main", "ol", "p", "pre", "section", "summary", "table", "ul", "video",
        *_HEADINGS,
    }
)
_SPACE = re.compile(r"[ \t\r\n\f]+")
_CODE_LANGUAGE = re.compile(r"(?:^|\s)(?:language-|lang-)?(python|javascript|typescript|json|bash|shell|sh|yaml|html|css|sql|go|rust|java|plaintext|text)(?:\s|$)")


def _absolute(url: str | None, base_url: str | None) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if base_url:
        url = urljoin(base_url, url)
    # Next.js serves pictures through its resizer (/_next/image?url=<original>); link the original.
    parts = urlsplit(url)
    if parts.path.endswith("/_next/image"):
        original = (parse_qs(parts.query).get("url") or [""])[0]
        if original:
            return urljoin(url, original)
    return url


def _drop_after(container: web.Element, target: web.Element) -> bool:
    """Remove everything after ``target`` inside ``container``; True when ``target`` was found."""
    for index, child in enumerate(container.children):
        if child is target or (isinstance(child, web.Element) and _drop_after(child, target)):
            del container.children[index + 1 :]
            return True
    return False


def _article_root(root: web.Element) -> web.Element:
    main = web.find_first(root, "main")
    if main is not None:
        # The article's heading and byline may sit above its <article>; what follows it in <main>
        # (a newsletter sign-up, related posts) is the site's.
        article = web.find_first(main, "article")
        if article is not None:
            _drop_after(main, article)
        return main
    article = web.find_first(root, "article")
    if article is not None:
        return article
    body = web.find_first(root, "body") or root
    # Outside <main>/<article>, a page <header> is the site banner.
    web.remove_elements(body, lambda element: element.tag == "header")
    return body


class _Writer:
    def __init__(self, *, base_url: str | None, sources: dict[int, str] | None) -> None:
        self.base_url = base_url
        self.sources = sources  # node id -> English, for the bilingual edition
        self.blocks: list[str] = []

    # Inline content -------------------------------------------------------------------------

    def inline(self, node: web.Text | web.Element, *, in_table: bool = False) -> str:
        if isinstance(node, web.Text):
            return _SPACE.sub(" ", web._text_value(node))
        tag = node.tag
        if tag in _DROPPED:
            return ""
        if tag == "br":
            return " " if in_table else "  \n"
        if tag == "img":
            return self.image(node)
        if tag == "code":
            code = _SPACE.sub(" ", web._plain_text(node)).strip()
            if not code:
                return ""
            fence = "``" if "`" in code else "`"
            return f"{fence}{code}{fence}"
        inner = "".join(self.inline(child, in_table=in_table) for child in node.children)
        if tag in {"strong", "b", "em", "i"}:
            marker = "**" if tag in {"strong", "b"} else "*"
            stripped = inner.strip()
            if not stripped:
                return inner
            leading = inner[: len(inner) - len(inner.lstrip())]
            trailing = inner[len(inner.rstrip()) :]
            return f"{leading}{marker}{stripped}{marker}{trailing}"
        if tag == "a":
            href = _absolute(node.attrs.get("href"), self.base_url)
            stripped = inner.strip()
            if not stripped or not href or href.startswith(("javascript:", "#")):
                return inner
            leading = inner[: len(inner) - len(inner.lstrip())]
            trailing = inner[len(inner.rstrip()) :]
            return f"{leading}[{stripped}]({href.replace(')', '%29').replace(' ', '%20')}){trailing}"
        return inner

    def image(self, node: web.Element) -> str:
        src = _absolute(node.attrs.get("src"), self.base_url)
        if not src or src.startswith("data:"):
            return ""
        # Line-art illustrations and icons beside the article's title (SVG) are decoration.
        if urlsplit(src).path.lower().endswith(".svg"):
            return ""
        alt = _SPACE.sub(" ", node.attrs.get("alt") or "").strip().replace("]", "")
        return f"![{alt}]({src})"

    def text_of(self, element: web.Element, *, in_table: bool = False) -> str:
        text = "".join(self.inline(child, in_table=in_table) for child in element.children)
        lines = [line.strip() for line in text.split("  \n")]
        return "  \n".join(line for line in lines if line).strip()

    def source_quote(self, element: web.Element) -> str | None:
        """The English of the translated nodes in ``element``, for the bilingual edition."""
        if self.sources is None:
            return None
        texts = [
            self.sources[int(node.attrs[web.NODE_ATTR])]
            for node in [element, *web.iter_elements(element)]
            if (node.attrs.get(web.NODE_ATTR) or "").isdigit() and int(node.attrs[web.NODE_ATTR]) in self.sources
        ]
        text = " ".join(" ".join(text.split()) for text in texts if text.strip())
        return f"> {text}" if text else None

    # Block content --------------------------------------------------------------------------

    def emit(self, text: str, element: web.Element | None = None) -> None:
        if not text.strip():
            return
        self.blocks.append(text)
        quote = self.source_quote(element) if element is not None else None
        if quote:
            self.blocks.append(quote)

    def block(self, element: web.Element) -> None:
        tag = element.tag
        if tag in _DROPPED:
            return
        if tag in _HEADINGS:
            text = self.text_of(element).replace("  \n", " ")
            if text:
                self.emit(f"{'#' * _HEADINGS[tag]} {text}", element)
            return
        if tag == "p" or tag in {"figcaption", "summary", "dt", "dd"}:
            text = self.text_of(element)
            if tag == "figcaption" and text:
                text = f"*{text}*"
            self.emit(text, element)
            return
        if tag in {"ul", "ol"}:
            lines = self.list_lines(element, depth=0)
            if lines:
                self.blocks.append("\n".join(lines))
            return
        if tag == "blockquote":
            inner = _Writer(base_url=self.base_url, sources=self.sources)
            inner.children(element)
            if inner.blocks:
                self.blocks.append("\n>\n".join("\n".join(f"> {line}" if line else ">" for line in block.split("\n")) for block in inner.blocks))
            return
        if tag == "pre":
            self.code_block(element)
            return
        if tag == "table":
            table = self.table(element)
            if table:
                self.blocks.append(table)
            return
        if tag == "img":
            self.emit(self.image(element))
            return
        if tag == "video":
            src = _absolute(element.attrs.get("src") or self._video_source(element), self.base_url)
            if src:
                self.blocks.append(f"[▶ 视频]({src})")
            return
        if tag == "hr":
            self.blocks.append("---")
            return
        self.children(element)

    @staticmethod
    def _video_source(element: web.Element) -> str | None:
        source = next((child for child in web.iter_elements(element) if child.tag == "source"), None)
        return source.attrs.get("src") if source is not None else None

    def children(self, element: web.Element) -> None:
        """Blocks among the children, with loose inline content between them as paragraphs."""
        run: list[web.Text | web.Element] = []

        def flush() -> None:
            if not run:
                return
            holder = web.Element(tag="p", attrs={}, start="<p>", end="</p>", children=list(run))
            text = self.text_of(holder)
            run.clear()
            if text:
                self.emit(text, holder)

        for child in element.children:
            if isinstance(child, web.Element) and (child.tag in _BLOCKS or child.tag in _DROPPED or self._holds_blocks(child)):
                flush()
                self.block(child)
            else:
                run.append(child)
        flush()

    def _holds_blocks(self, element: web.Element) -> bool:
        return any(child.tag in _BLOCKS for child in web.iter_elements(element))

    def list_lines(self, element: web.Element, *, depth: int) -> list[str]:
        lines: list[str] = []
        ordered = element.tag == "ol"
        number = int(element.attrs.get("start") or 1) if (element.attrs.get("start") or "").isdigit() else 1
        for item in element.children:
            if not isinstance(item, web.Element) or item.tag != "li":
                continue
            nested = [child for child in item.children if isinstance(child, web.Element) and child.tag in {"ul", "ol"}]
            holder = web.Element(
                tag="li", attrs=item.attrs, start=item.start, end=item.end,
                children=[child for child in item.children if child not in nested],
            )
            text = self.text_of(holder).replace("  \n", " ")
            marker = f"{number}." if ordered else "-"
            indent = "   " * depth
            if text:
                lines.append(f"{indent}{marker} {text}")
                quote = self.source_quote(holder)
                if quote:
                    lines.append(f"{indent}   {quote}")
                number += 1
            for child in nested:
                lines.extend(self.list_lines(child, depth=depth + 1))
        return lines

    def code_block(self, element: web.Element) -> None:
        # The code itself: its <code> when there is one (a "Copy" button may sit beside it).
        code_element = next((child for child in web.iter_elements(element) if child.tag == "code"), None)
        code = web._plain_text(code_element or element).strip("\n")
        if not code.strip():
            return
        classes = " ".join(
            str(node.attrs.get("class") or "") for node in [element, *web.iter_elements(element)] if node.tag in {"pre", "code"}
        )
        match = _CODE_LANGUAGE.search(classes.lower())
        language = match.group(1) if match and match.group(1) not in {"plaintext", "text"} else ""
        fence = "````" if "```" in code else "```"
        self.blocks.append(f"{fence}{language}\n{code}\n{fence}")

    def table(self, element: web.Element) -> str | None:
        rows: list[list[str]] = []
        header_rows = 0
        for row in (node for node in web.iter_elements(element) if node.tag == "tr"):
            cells = [cell for cell in row.children if isinstance(cell, web.Element) and cell.tag in {"td", "th"}]
            if not cells:
                continue
            texts = [self.text_of(cell, in_table=True).replace("|", "\\|") for cell in cells]
            if not rows and all(cell.tag == "th" for cell in cells):
                header_rows = 1
            rows.append(texts)
        if not rows:
            return None
        width = max(len(row) for row in rows)
        rows = [row + [""] * (width - len(row)) for row in rows]
        # Markdown tables need a header row; a table without one gets an empty header.
        header = rows[0] if header_rows else [""] * width
        body = rows[1:] if header_rows else rows
        lines = [
            "| " + " | ".join(header) + " |",
            "| " + " | ".join("---" for _ in range(width)) + " |",
            *("| " + " | ".join(row) + " |" for row in body),
        ]
        return "\n".join(lines)


def translated_page_markdown(
    snapshot: str,
    translations: dict[int, str],
    *,
    base_url: str | None = None,
    sources: dict[int, str] | None = None,
    skip_title: str | None = None,
) -> str:
    """The article in ``snapshot`` as Markdown, each marked node replaced by its translation.

    ``sources``: node id -> English, which makes the bilingual edition.
    ``skip_title``: the article's title as the caller already set it above the body; its first
    heading with this text is left out, with what stands above it (a section label, breadcrumbs).
    """
    root = web.parse_html(snapshot)
    for node_id, element in web.marked_nodes(root):
        translation = (translations.get(node_id) or "").strip()
        if not translation:
            continue
        _source, tags = web.node_source_text(element)
        rendered = web.render_translation(translation, tags)
        element.children = web.parse_html(rendered).children
    article = _article_root(root)
    writer = _Writer(base_url=base_url, sources=sources)
    writer.children(article)
    blocks = writer.blocks
    if skip_title:
        wanted = " ".join(skip_title.split())
        for index, block in enumerate(blocks):
            if block.startswith("#") and " ".join(block.lstrip("#").split()) == wanted:
                # Above the title: the site's section label or breadcrumbs.
                del blocks[: index + 1]
                if sources is not None and blocks and blocks[0].startswith("> "):
                    del blocks[0]  # the title's English, under it
                break
    return "\n\n".join(block for block in blocks if block.strip()) + "\n"
