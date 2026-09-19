"""Single-file HTML deliverables: images embedded, chapters assembled into one book.

Exports are stored as an HTML file plus an ``assets/`` folder (images shared
between exports, content-addressed blobs). A reader wants one file that opens
anywhere, so downloads build it from the stored exports:

- ``inline_local_assets`` rewrites ``src``/``href`` references to local files
  under the export directory into ``data:`` URIs;
- ``assemble_bilingual_book`` joins the per-chapter bilingual exports into
  one document with a table of contents;
- ``drop_unused_katex`` removes the formula renderer (loaded from a CDN) when
  the document has no formulas, so such a book needs no network at all.
"""

from __future__ import annotations

import base64
import html
import mimetypes
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

_ATTRIBUTE_URL = re.compile(r"""(?P<attr>\b(?:src|href))=(?P<quote>['"])(?P<url>[^'"]+)(?P=quote)""")
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_KATEX_TAGS = re.compile(
    r"<link[^>]*katex[^>]*>|<script[^>]*katex[^>]*>\s*</script>|<script>[^<]*katex\.render[^<]*</script>",
    re.IGNORECASE,
)
# An element that holds a formula (the render script itself also names the class, so match elements only).
_KATEX_ELEMENT = re.compile(r"""class=['"][^'"]*\bkatex-source\b""")
_EMBEDDABLE_TYPES = ("image/", "font/", "text/css")


def inline_local_assets(document: str, base_dir: Path, *, resolve: Callable[[Path], Path | None] | None = None) -> str:
    """Replace references to files under ``base_dir`` with data URIs.

    ``resolve`` may map a missing local path to another location (e.g. the
    content-addressed blob of an asset). References that leave ``base_dir``,
    point elsewhere (http, #anchors, data:) or cannot be found are kept.
    """
    root = base_dir.resolve()
    cache: dict[str, str | None] = {}

    def data_uri(url: str) -> str | None:
        if url in cache:
            return cache[url]
        cache[url] = None
        relative = unquote(url.split("#", 1)[0].split("?", 1)[0])
        if not relative or relative.startswith("/"):
            return None
        candidate = (root / relative).resolve()
        if root not in candidate.parents:
            return None
        if not candidate.is_file() and resolve is not None:
            candidate = resolve(candidate) or candidate
        if not candidate.is_file():
            return None
        media_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if not media_type.startswith(_EMBEDDABLE_TYPES):
            return None
        encoded = base64.b64encode(candidate.read_bytes()).decode("ascii")
        cache[url] = f"data:{media_type};base64,{encoded}"
        return cache[url]

    def replace(match: re.Match[str]) -> str:
        url = html.unescape(match.group("url"))
        if url.startswith("#") or _SCHEME.match(url):
            return match.group(0)
        uri = data_uri(url)
        if uri is None:
            return match.group(0)
        quote = match.group("quote")
        return f"{match.group('attr')}={quote}{uri}{quote}"

    return _ATTRIBUTE_URL.sub(replace, document)


def drop_unused_katex(document: str) -> str:
    """Remove the CDN formula renderer when nothing in the document needs it."""
    if _KATEX_ELEMENT.search(document):
        return document
    return _KATEX_TAGS.sub("", document)


def local_references(document: str) -> list[str]:
    """Local (relative) src/href references left in a document; empty for a self-contained file."""
    urls = [html.unescape(match.group("url")) for match in _ATTRIBUTE_URL.finditer(document)]
    return [url for url in urls if not url.startswith("#") and not _SCHEME.match(url)]


@dataclass(frozen=True, slots=True)
class BookChapter:
    title: str
    document: str


_HEAD = re.compile(r"<head[^>]*>(?P<head>.*?)</head>", re.DOTALL | re.IGNORECASE)
_STYLE = re.compile(r"<style[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)
_MAIN = re.compile(r"<main[^>]*>(?P<main>.*)</main>", re.DOTALL | re.IGNORECASE)
_BODY = re.compile(r"<body[^>]*>(?P<body>.*)</body>", re.DOTALL | re.IGNORECASE)
_USAGE = re.compile(r"<section class='usage-summary'.*?</ul>\s*</section>", re.DOTALL)
_CHAPTER_KICKER = re.compile(r"<div class='hero-kicker'>[^<]*</div>")
_SCRIPTS = re.compile(r"<script[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE)

_BOOK_STYLE = """<style>
.book-toc{max-width:860px;margin:0 auto 32px;padding:20px 24px;background:var(--card,#fff);border-radius:12px}
.book-toc h2{margin:0 0 12px;font-size:1.1rem}
.book-toc ol{margin:0;padding-left:1.4em;line-height:1.9}
.book-toc a{color:inherit}
.book-chapter{scroll-margin-top:16px}
.book-chapter + .book-chapter{margin-top:48px;padding-top:32px;border-top:1px solid rgba(0,0,0,.12)}
.back-to-toc{display:block;margin:12px 0 0;text-align:right;font-size:.85rem}
</style>"""


def assemble_bilingual_book(*, title: str, subtitle: str | None, chapters: list[BookChapter]) -> str:
    """One bilingual HTML book from per-chapter bilingual exports (styles from the first chapter).

    Per-chapter translation statistics are left out: they describe the run, not the book.
    """
    if not chapters:
        raise ValueError("no chapters to assemble")
    head = _HEAD.search(chapters[0].document)
    styles = "".join(_STYLE.findall(head.group("head"))) if head else ""
    scripts: list[str] = []
    sections: list[str] = []
    toc: list[str] = []
    for index, chapter in enumerate(chapters, start=1):
        match = _MAIN.search(chapter.document) or _BODY.search(chapter.document)
        body = match.group(1) if match else chapter.document
        body = _USAGE.sub("", body)
        for script in _SCRIPTS.findall(chapter.document):
            if script not in scripts:
                scripts.append(script)
        body = _SCRIPTS.sub("", body)
        # Inside a book each chapter's banner names its place, not the export it came from.
        body = _CHAPTER_KICKER.sub(f"<div class='hero-kicker'>第 {index} 部分</div>", body, count=1)
        anchor = f"chapter-{index}"
        toc.append(f"<li><a href='#{anchor}'>{html.escape(chapter.title)}</a></li>")
        sections.append(
            f"<section class='book-chapter' id='{anchor}'>{body}<a class='back-to-toc' href='#toc'>↑ 目录</a></section>"
        )
    subtitle_html = f"<div class='source-title'>{html.escape(subtitle)}</div>" if subtitle else ""
    return (
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{html.escape(title)}</title>{styles}{_BOOK_STYLE}</head><body><main class='page'>"
        f"<header class='hero'><div class='hero-kicker'>中英文对照</div><h1>{html.escape(title)}</h1>{subtitle_html}</header>"
        f"<nav class='book-toc' id='toc'><h2>目录</h2><ol>{''.join(toc)}</ol></nav>"
        f"{''.join(sections)}</main>{''.join(scripts)}</body></html>"
    )
