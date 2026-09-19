"""Capture a web page as a self-contained HTML snapshot to translate.

The snapshot is what the page looks like, frozen: the server-rendered HTML with
its stylesheets inlined and every other URL (images, fonts, links) made
absolute, so it renders the same from a local file. Scripts are removed: the
translated page must not re-render itself back into English, and a copy of
someone else's page should not run their code. Pages that only build their
content in the browser therefore capture empty and are refused.

Prose nodes are stamped for translation (``domain.structure.web``) before the
snapshot is written, so parser and exporter agree on them by construction.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from book_agent.domain.structure import web

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0 Safari/537.36 book-agent"
)
MAX_PAGE_BYTES = 10 * 1024 * 1024
MAX_STYLESHEET_BYTES = 4 * 1024 * 1024
MAX_STYLESHEETS = 40
# Web fonts are embedded: font files need CORS, so a copy served from anywhere
# else would fall back to system fonts and lose the page's typography.
MAX_FONT_BYTES = 2 * 1024 * 1024
MAX_TOTAL_FONT_BYTES = 12 * 1024 * 1024
MAX_REDIRECTS = 5
TIMEOUT_SECONDS = 20.0

_URL_ATTRS = ("href", "src", "poster", "action", "data-src", "background")
_SRCSET_ATTRS = ("srcset", "imagesrcset", "data-srcset")
_DROPPED_LINK_RELS = frozenset({"preload", "modulepreload", "prefetch", "preconnect", "dns-prefetch", "manifest"})
_KEEP_SCHEMES = ("#", "data:", "mailto:", "tel:", "about:", "blob:")
_CSS_URL = re.compile(r"url\(\s*(['\"]?)(?P<url>[^'\")]*)\1\s*\)", re.IGNORECASE)
_FONT_FACE = re.compile(r"@font-face\s*\{[^}]*\}", re.IGNORECASE)
_FONT_TYPES = {".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf", ".otf": "font/otf"}
_CSS_IMPORT = re.compile(r"@import\s+(['\"])(?P<url>[^'\"]+)\1", re.IGNORECASE)

UrlGuard = Callable[[str], None]


class WebCaptureError(ValueError):
    """The page could not be captured (bad URL, network, not HTML, nothing to translate)."""


@dataclass(frozen=True, slots=True)
class WebCapture:
    path: Path
    url: str
    final_url: str
    node_count: int
    stylesheet_count: int


def _normalize_url(url: str) -> str:
    candidate = (url or "").strip()
    if candidate and "://" not in candidate:
        candidate = f"https://{candidate}"
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise WebCaptureError("请输入以 http:// 或 https:// 开头的网页链接。")
    return parsed._replace(fragment="").geturl()


@dataclass(frozen=True, slots=True)
class _Fetched:
    status_code: int
    url: str
    content_type: str
    text: str
    content: bytes | None = None


def _fetch(
    client: httpx.Client, url: str, guard: UrlGuard | None, *, limit: int, binary: bool = False
) -> _Fetched:
    """GET with redirects followed by hand, so every hop passes the guard; the body is capped at ``limit``."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        if guard is not None:
            guard(current)
        with client.stream("GET", current) as response:
            if response.is_redirect and response.headers.get("location"):
                current = urljoin(current, response.headers["location"])
                continue
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > limit:
                    raise WebCaptureError(f"网页内容超过 {limit // (1024 * 1024)} MB，无法抓取。")
            return _Fetched(
                status_code=response.status_code,
                url=str(response.url),
                content_type=(response.headers.get("content-type") or "").lower(),
                text="" if binary else bytes(body).decode(response.charset_encoding or "utf-8", errors="replace"),
                content=bytes(body) if binary else None,
            )
    raise WebCaptureError("网页重定向次数过多。")


def _absolute(value: str, base: str) -> str:
    stripped = value.strip()
    if not stripped or stripped.lower().startswith(_KEEP_SCHEMES):
        return value
    if stripped.lower().startswith("javascript:"):
        return "#"
    return urljoin(base, stripped)


def _absolute_srcset(value: str, base: str) -> str:
    candidates = []
    for candidate in value.split(","):
        parts = candidate.strip().split(None, 1)
        if not parts:
            continue
        candidates.append(" ".join([_absolute(parts[0], base), *parts[1:]]))
    return ", ".join(candidates)


def absolutize_css(css: str, base: str) -> str:
    css = _CSS_IMPORT.sub(lambda match: f"@import url({_absolute(match.group('url'), base)})", css)
    return _CSS_URL.sub(lambda match: f"url(\"{_absolute(match.group('url'), base)}\")", css)


def _rel_tokens(element: web.Element) -> set[str]:
    return {token.lower() for token in (element.attrs.get("rel") or "").split()}


class _Snapshot:
    def __init__(self, client: httpx.Client, guard: UrlGuard | None, base: str) -> None:
        self.client = client
        self.guard = guard
        self.base = base
        self.stylesheets = 0
        self.fonts: dict[str, str | None] = {}
        self.font_bytes = 0

    def _stylesheet(self, element: web.Element) -> web.Element | None:
        href = _absolute(element.attrs.get("href") or "", self.base)
        if not href or self.stylesheets >= MAX_STYLESHEETS:
            return None
        try:
            response = _fetch(self.client, href, self.guard, limit=MAX_STYLESHEET_BYTES)
        except (httpx.HTTPError, WebCaptureError):
            return None
        if response.status_code >= 400:
            return None
        self.stylesheets += 1
        css = self.embed_fonts(absolutize_css(response.text, response.url)).replace("</style", "<\\/style")
        attrs: dict[str, str | None] = {"data-ba-href": href}
        if element.attrs.get("media"):
            attrs["media"] = element.attrs["media"]
        style = web.Element("style", {}, "", "</style>", [web.Text(css)])
        web.set_attrs(style, attrs)
        return style

    def _embed_font(self, url: str) -> str | None:
        suffix = Path(urlparse(url).path).suffix.lower()
        media_type = _FONT_TYPES.get(suffix)
        if media_type is None or self.font_bytes >= MAX_TOTAL_FONT_BYTES:
            return None
        if url not in self.fonts:
            try:
                fetched = _fetch(self.client, url, self.guard, limit=MAX_FONT_BYTES, binary=True)
            except (httpx.HTTPError, WebCaptureError):
                fetched = None
            if fetched is None or fetched.status_code >= 400 or fetched.content is None:
                self.fonts[url] = None
            else:
                self.font_bytes += len(fetched.content)
                self.fonts[url] = f"data:{media_type};base64,{base64.b64encode(fetched.content).decode('ascii')}"
        return self.fonts[url]

    def embed_fonts(self, css: str) -> str:
        def face(match: re.Match[str]) -> str:
            return _CSS_URL.sub(
                lambda url: f'url("{self._embed_font(url.group("url")) or url.group("url")}")', match.group(0)
            )

        return _FONT_FACE.sub(face, css)

    def _rewrite(self, element: web.Element) -> None:
        attrs = dict(element.attrs)
        changed = False
        for name in _URL_ATTRS:
            if attrs.get(name):
                attrs[name] = _absolute(attrs[name] or "", self.base)
                changed = True
        for name in _SRCSET_ATTRS:
            if attrs.get(name):
                attrs[name] = _absolute_srcset(attrs[name] or "", self.base)
                changed = True
        if attrs.get("style") and "url(" in (attrs["style"] or "").lower():
            attrs["style"] = absolutize_css(attrs["style"] or "", self.base)
            changed = True
        if element.tag == "img" and not attrs.get("src") and attrs.get("data-src"):
            attrs["src"] = attrs["data-src"]
            changed = True
        for name in ("integrity", "nonce"):
            if name in attrs:
                del attrs[name]
                changed = True
        if changed:
            web.set_attrs(element, attrs)

    def _keep(self, element: web.Element) -> web.Element | None:
        if element.tag in {"script", "base"}:
            return None
        if element.tag == "meta":
            equiv = (element.attrs.get("http-equiv") or "").lower()
            if equiv in {"content-security-policy", "refresh"}:
                return None
        if element.tag == "link":
            rels = _rel_tokens(element)
            if rels & _DROPPED_LINK_RELS:
                return None
            if "stylesheet" in rels:
                return self._stylesheet(element) or element
        return element

    def visit(self, element: web.Element) -> None:
        children: list[web.Text | web.Element] = []
        for child in element.children:
            if isinstance(child, web.Element):
                kept = self._keep(child)
                if kept is None:
                    continue
                if kept is child:
                    self._rewrite(child)
                    if child.tag == "style":
                        css = self.embed_fonts(absolutize_css(web.serialize_children(child), self.base))
                        child.children = [web.Text(css)]
                    else:
                        self.visit(child)
                children.append(kept)
            else:
                children.append(child)
        element.children = children


def _stamp_head(root: web.Element, source_url: str) -> None:
    head = web.find_first(root, "head")
    meta = web.Element("meta", {}, "")
    web.set_attrs(meta, {"name": web.SOURCE_URL_META, "content": source_url})
    charset = web.Element("meta", {}, "")
    web.set_attrs(charset, {"charset": "utf-8"})
    # The snapshot is written as UTF-8 whatever the page was served in.
    web.remove_elements(
        root,
        lambda element: element.tag == "meta"
        and ("charset" in element.attrs or (element.attrs.get("http-equiv") or "").lower() == "content-type"),
    )
    if head is None:
        html_element = web.find_first(root, "html") or root
        head = web.Element("head", {}, "<head>", "</head>")
        html_element.children.insert(0, head)
    head.children[0:0] = [charset, meta]


def _snapshot_name(url: str) -> str:
    parsed = urlparse(url)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", f"{parsed.hostname or 'page'}{parsed.path}").strip("-")
    return f"{slug[:120] or 'page'}.html"


def capture_web_page(
    url: str,
    target_dir: str | Path,
    *,
    client: httpx.Client | None = None,
    guard: UrlGuard | None = None,
) -> WebCapture:
    """Fetch ``url``, write its marked snapshot under ``target_dir`` and describe it."""
    source_url = _normalize_url(url)
    owned = client is None
    client = client or httpx.Client(
        timeout=TIMEOUT_SECONDS,
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en"},
    )
    try:
        try:
            response = _fetch(client, source_url, guard, limit=MAX_PAGE_BYTES)
        except httpx.HTTPError as exc:
            raise WebCaptureError(f"无法访问该网页：{exc}") from exc
        if response.status_code >= 400:
            raise WebCaptureError(f"网页返回 HTTP {response.status_code}，无法抓取。")
        content_type = response.content_type
        if content_type and "html" not in content_type:
            raise WebCaptureError(f"该链接不是网页（{content_type.split(';')[0]}）。PDF、EPUB 请下载后上传文件。")
        final_url = response.url
        root = web.parse_html(response.text)
        snapshot = _Snapshot(client, guard, final_url)
        snapshot.visit(root)
    finally:
        if owned:
            client.close()
    _stamp_head(root, source_url)
    node_count = web.mark_translatable_nodes(root)
    if node_count == 0:
        raise WebCaptureError("页面里没有找到可翻译的英文文字。如果该网站需要登录，或正文完全由浏览器脚本生成，暂时无法抓取。")
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)
    path = target / _snapshot_name(final_url)
    path.write_text(web.serialize(root), encoding="utf-8")
    return WebCapture(
        path=path,
        url=source_url,
        final_url=final_url,
        node_count=node_count,
        stylesheet_count=snapshot.stylesheets,
    )
