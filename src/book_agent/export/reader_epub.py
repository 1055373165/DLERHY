"""Reader deliverables built from the merged Chinese book: a clean single HTML and an EPUB for any source.

The merged HTML export is the one rendering of the whole translated book
(chapters, images, tables, code, formulas). Readers get it in two shapes:

- ``reader_html``: the operator-facing translation statistics removed and a
  table of contents added (images are embedded separately by
  ``standalone.inline_local_assets``);
- ``build_reader_epub``: an EPUB 3 package (one XHTML file per chapter, the
  images as files, a navigation document and an NCX for older readers).
  Source-preserving EPUB exports only exist for EPUB sources; this one works
  for PDF books too.

HTML is converted to well-formed XHTML with the standard library parser:
void elements are closed, attributes quoted and escaped, entities resolved,
and stray end tags dropped.
"""

from __future__ import annotations

import html
import mimetypes
import re
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

_USAGE_SUMMARY = re.compile(r"<section class='usage-summary'.*?</ul>\s*</section>", re.DOTALL)
_CHAPTER_START = re.compile(r"<section class='chapter' id='(?P<id>[^']+)'>")
_CHAPTER_TITLE = re.compile(r"<div class='chapter-head'>\s*<h2[^>]*>(?P<title>.*?)</h2>", re.DOTALL)
_HERO = re.compile(r"<header class='hero'[^>]*>.*?</header>", re.DOTALL)
_HERO_TITLE = re.compile(r"<h1[^>]*>(?P<title>.*?)</h1>", re.DOTALL)
_MAIN_END = re.compile(r"</main>", re.IGNORECASE)
_TAGS = re.compile(r"<[^>]+>")

_TOC_STYLE = (
    "<style>.reader-toc{margin:0 0 28px;padding:18px 22px;border:1px solid rgba(0,0,0,.1);border-radius:14px;"
    "background:rgba(255,255,255,.7)}.reader-toc h2{margin:0 0 10px;font-size:1.05rem}"
    ".reader-toc ol{margin:0;padding-left:1.4em;line-height:1.9}.reader-toc a{color:inherit}</style>"
)
# E-readers get their own plain stylesheet: the web export's layout (viewport units, grids,
# sticky sidebars) is meaningless on a paged reader and some engines reject it.
EPUB_CSS = """
body{margin:0 4%;line-height:1.75;font-family:serif}
h1{font-size:1.6em;margin:1.2em 0 .6em;line-height:1.3}
h2{font-size:1.35em;margin:1.2em 0 .6em;line-height:1.3}
h3,h4{font-size:1.1em;margin:1em 0 .5em}
.title-page{margin-top:30%;text-align:center}
.title-page .meta{margin-top:1.5em;color:#555}
.chapter-head h2{margin-top:0}
.block{margin:.8em 0}
.zh{text-align:justify}
.list_item .zh{padding-left:1.2em;text-indent:-1.2em}
.quote{border-left:3px solid #999;padding-left:.8em;color:#333}
.footnote .zh,.caption .zh,figcaption{font-size:.9em;color:#444}
figure{margin:1em 0;text-align:center}
img{max-width:100%;height:auto}
pre,code{font-family:monospace;font-size:.85em}
pre{white-space:pre-wrap;word-break:break-all;background:#f4f4f4;padding:.6em}
table{border-collapse:collapse;width:100%;font-size:.9em}
th,td{border:1px solid #bbb;padding:3px 5px;vertical-align:top}
details,.artifact-note,.usage-summary{display:none}
"""
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
_DROP = {"script"}


def _text(fragment: str) -> str:
    return " ".join(html.unescape(_TAGS.sub("", fragment)).split())


@dataclass(frozen=True, slots=True)
class ReaderChapter:
    anchor: str
    title: str
    body: str


def split_merged_book(document: str) -> tuple[str, list[ReaderChapter]]:
    """(book title, chapters) of a merged HTML export."""
    hero = _HERO.search(document)
    title_match = _HERO_TITLE.search(hero.group(0)) if hero else None
    title = _text(title_match.group("title")) if title_match else ""
    starts = list(_CHAPTER_START.finditer(document))
    main_end = _MAIN_END.search(document, starts[-1].end() if starts else 0)
    end_of_book = main_end.start() if main_end else len(document)
    chapters: list[ReaderChapter] = []
    for index, start in enumerate(starts):
        stop = starts[index + 1].start() if index + 1 < len(starts) else end_of_book
        body = document[start.start():stop]
        heading = _CHAPTER_TITLE.search(body)
        chapters.append(
            ReaderChapter(
                anchor=start.group("id"),
                title=_text(heading.group("title")) if heading else f"第{index + 1}部分",
                body=body,
            )
        )
    return title, chapters


def reader_html(document: str) -> str:
    """The merged book for readers: no translation statistics, a table of contents after the title."""
    document = _USAGE_SUMMARY.sub("", document)
    _, chapters = split_merged_book(document)
    if not chapters:
        return document
    items = "".join(f"<li><a href='#{html.escape(c.anchor)}'>{html.escape(c.title)}</a></li>" for c in chapters)
    toc = f"<nav class='reader-toc' aria-label='目录'><h2>目录</h2><ol>{items}</ol></nav>"
    hero = _HERO.search(document)
    if hero is not None:
        document = document[: hero.end()] + toc + document[hero.end():]
    return document.replace("</head>", _TOC_STYLE + "</head>", 1)


# --- XHTML -------------------------------------------------------------------------------


class _XhtmlWriter(HTMLParser):
    def __init__(self, rewrite_url) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []
        self.skip_depth = 0
        self.rewrite_url = rewrite_url

    def _attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> str:
        parts = []
        seen: set[str] = set()
        for name, value in attrs:
            name = name.lower()
            if name in seen or not re.fullmatch(r"[a-z_:][-a-z0-9_:.]*", name) or name.startswith("on"):
                continue
            seen.add(name)
            value = value if value is not None else name
            if name in {"src", "href"}:
                value = self.rewrite_url(tag, name, value)
            parts.append(f' {name}="{html.escape(value, quote=True)}"')
        return "".join(parts)

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if self.skip_depth or tag in _DROP:
            self.skip_depth += 0 if tag in _VOID else 1
            return
        if tag in _VOID:
            self.out.append(f"<{tag}{self._attrs(tag, attrs)}/>")
            return
        self.out.append(f"<{tag}{self._attrs(tag, attrs)}>")
        self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if self.skip_depth or tag in _DROP:
            return
        self.out.append(f"<{tag}{self._attrs(tag, attrs)}/>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.skip_depth:
            if tag in _DROP:
                self.skip_depth -= 1
            return
        if tag in _VOID or tag not in self.stack:
            return  # stray end tag
        while self.stack:
            open_tag = self.stack.pop()
            self.out.append(f"</{open_tag}>")
            if open_tag == tag:
                break

    def handle_data(self, data):
        if not self.skip_depth:
            self.out.append(html.escape(data, quote=False))

    def close(self) -> str:
        super().close()
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        return "".join(self.out)


def to_xhtml(fragment: str, rewrite_url=lambda tag, attr, value: value) -> str:
    writer = _XhtmlWriter(rewrite_url)
    writer.feed(fragment)
    return writer.close()


# --- EPUB --------------------------------------------------------------------------------


def _xhtml_page(title: str, body: str, *, lang: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{lang}" xml:lang="{lang}">'
        f"<head><meta charset=\"utf-8\"/><title>{html.escape(title)}</title>"
        '<link rel="stylesheet" type="text/css" href="../styles/book.css"/></head>'
        f"<body>{body}</body></html>"
    )


def build_reader_epub(
    document: str,
    base_dir: Path,
    output: Path,
    *,
    title: str | None = None,
    author: str | None = None,
    lang: str = "zh-CN",
    identifier: str | None = None,
) -> Path:
    """Write an EPUB 3 of a merged HTML export; images are read relative to ``base_dir``."""
    book_title, chapters = split_merged_book(_USAGE_SUMMARY.sub("", document))
    title = title or book_title or "译本"
    if not chapters:
        raise ValueError("the merged export has no chapters")
    root = base_dir.resolve()
    images: dict[str, tuple[str, Path]] = {}  # source url -> (epub path, file)

    def rewrite(tag: str, attr: str, value: str) -> str:
        if value.startswith("#") or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", value):
            if value.startswith("#chapter-") or value == "#top":
                return "#"
            return value
        if tag != "img" or attr != "src":
            return value
        if value not in images:
            path = (root / unquote(value.split("?", 1)[0])).resolve()
            if root not in path.parents or not path.is_file():
                return value
            name = f"img{len(images) + 1:04d}{path.suffix.lower() or '.png'}"
            images[value] = (f"images/{name}", path)
        return f"../{images[value][0]}"

    css = EPUB_CSS
    identifier = identifier or f"urn:uuid:{uuid.uuid4()}"
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    pages: list[tuple[str, str, str]] = []  # (id, href, title)
    files: dict[str, str] = {}
    hero = _HERO.search(document)
    title_body = f"<section class='title-page'><h1>{html.escape(title)}</h1>" + (
        f"<p class='meta'>{html.escape(author)}</p>" if author else ""
    ) + "</section>"
    if hero is not None:
        files["text/title.xhtml"] = _xhtml_page(title, to_xhtml(title_body), lang=lang)
        pages.append(("title", "text/title.xhtml", title))
    for index, chapter in enumerate(chapters, start=1):
        href = f"text/chapter-{index:03d}.xhtml"
        files[href] = _xhtml_page(chapter.title, to_xhtml(chapter.body, rewrite), lang=lang)
        pages.append((f"chapter-{index:03d}", href, chapter.title))

    nav_items = "".join(
        f'<li><a href="{href.removeprefix("text/")}">{html.escape(page_title)}</a></li>'
        for page_id, href, page_title in pages
        if page_id != "title"
    )
    files["text/nav.xhtml"] = (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{lang}" xml:lang="{lang}">'
        f"<head><meta charset=\"utf-8\"/><title>目录</title></head><body>"
        f'<nav epub:type="toc" id="toc"><h1>目录</h1><ol>{nav_items}</ol></nav></body></html>'
    )
    nav_points = "".join(
        f'<navPoint id="np-{n}" playOrder="{n}"><navLabel><text>{html.escape(page_title)}</text></navLabel>'
        f'<content src="{href}"/></navPoint>'
        for n, (page_id, href, page_title) in enumerate(pages, start=1)
    )
    toc_ncx = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
        f'<head><meta name="dtb:uid" content="{html.escape(identifier)}"/></head>'
        f"<docTitle><text>{html.escape(title)}</text></docTitle><navMap>{nav_points}</navMap></ncx>"
    )
    manifest = [
        '<item id="nav" href="text/nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        '<item id="css" href="styles/book.css" media-type="text/css"/>',
    ]
    manifest += [f'<item id="{page_id}" href="{href}" media-type="application/xhtml+xml"/>' for page_id, href, _ in pages]
    for n, (epub_path, file_path) in enumerate(images.values(), start=1):
        media_type = mimetypes.guess_type(file_path.name)[0] or "image/png"
        manifest.append(f'<item id="image-{n}" href="{epub_path}" media-type="{media_type}"/>')
    spine = "".join(f'<itemref idref="{page_id}"/>' for page_id, _, _ in pages)
    content_opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:identifier id="book-id">{html.escape(identifier)}</dc:identifier>'
        f"<dc:title>{html.escape(title)}</dc:title><dc:language>{lang}</dc:language>"
        + (f"<dc:creator>{html.escape(author)}</dc:creator>" if author else "")
        + f'<meta property="dcterms:modified">{modified}</meta></metadata>'
        f"<manifest>{''.join(manifest)}</manifest>"
        f'<spine toc="ncx">{spine}</spine></package>'
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w") as archive:
        # The mimetype entry comes first and uncompressed (EPUB OCF).
        archive.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>'
            "</container>",
            compress_type=zipfile.ZIP_DEFLATED,
        )
        archive.writestr("OEBPS/content.opf", content_opf, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("OEBPS/toc.ncx", toc_ncx, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("OEBPS/styles/book.css", css, compress_type=zipfile.ZIP_DEFLATED)
        for href, text in files.items():
            archive.writestr(f"OEBPS/{href}", text, compress_type=zipfile.ZIP_DEFLATED)
        for epub_path, file_path in images.values():
            archive.write(file_path, f"OEBPS/{epub_path}", compress_type=zipfile.ZIP_STORED)
    return output
