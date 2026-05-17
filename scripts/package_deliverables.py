"""Package the 9-chapter book into four deliverable bundles.

Each bundle is a self-contained directory containing one combined book
file and an ``assets/`` subdirectory with all referenced images:

  deliverable/
    md-zh/             ← single Chinese-only Markdown + assets
    md-bilingual/      ← single bilingual Markdown + assets
    html-zh/           ← single Chinese-only HTML + assets
    html-bilingual/    ← single bilingual HTML + assets

Image extraction strategy:
- Per-chapter HTML files embed every image as a ``data:image/...;base64,``
  src. We parse the HTML, decode each base64 payload, write it to
  ``assets/fig-N-M.<ext>`` (named by the "图N.M" label parsed from the
  alt/caption), and rewrite the ``src`` to the relative asset path.
- The MD files don't carry images today, so we post-process them: for
  every ``*图：图N.M …*`` caption marker we inject an ``![…](assets/…)``
  reference on the line above.
"""
from __future__ import annotations

import base64
import hashlib
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_TMP = ROOT / ".test-tmp"
OUT_ROOT = ROOT / "deliverable"

CHAPTERS = [
    ("ch1", "第 1 章", "宏观图景：什么是大语言模型？", TEST_TMP / "ch1-export"),
    ("ch2", "第 2 章", "分词器：大语言模型如何看待世界", TEST_TMP / "ch2-export"),
    # ch3 lives in ch3-export-v2 per chapters_config.json (history).
    ("ch3", "第 3 章", "Transformer 架构：输入如何转换为输出", TEST_TMP / "ch3-export-v2"),
    ("ch4", "第 4 章", "LLM 如何学习", TEST_TMP / "ch4-export"),
    ("ch5", "第 5 章", "如何约束 LLM 的行为", TEST_TMP / "ch5-export"),
    ("ch6", "第 6 章", "超越自然语言处理", TEST_TMP / "ch6-export"),
    ("ch7", "第 7 章", "LLM 的误解、局限与新兴能力", TEST_TMP / "ch7-export"),
    ("ch8", "第 8 章", "用大语言模型设计解决方案", TEST_TMP / "ch8-export"),
    ("ch9", "第 9 章", "构建与使用 LLM 的伦理", TEST_TMP / "ch9-export"),
]


def _resolve_export_dir(name: str, default: Path) -> Path:
    if default.is_dir():
        return default
    raise SystemExit(f"chapter dir missing: {default}")


_MIME_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
}

_DATA_URL_RE = re.compile(
    r"data:(?P<mime>image/[a-zA-Z0-9+.\-]+);base64,(?P<b64>[A-Za-z0-9+/=]+)"
)


def _figure_label_from_caption(text: str) -> str | None:
    """Pull the "1.1" / "12.3" prefix out of a figure alt/caption.

    Returns None when no figure-N.M pattern is present (e.g. an inline
    illustration with no caption number).
    """
    if not text:
        return None
    cleaned = re.sub(r"\s+", " ", text).strip()
    m = re.match(r"图\s*(\d+(?:\.\d+)?)", cleaned)
    if m:
        return m.group(1)
    m = re.match(r"Figure\s*(\d+(?:\.\d+)?)", cleaned, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def _safe_label_for_filename(label: str | None, *, fallback_chapter: str, fallback_index: int) -> str:
    if label:
        return f"fig-{label.replace('.', '-')}"
    return f"{fallback_chapter}-fig-{fallback_index:02d}"


def _extract_figures_from_html(
    html: str,
    *,
    chapter_id: str,
    assets_dir: Path,
    seen_hashes: dict[str, str],
) -> tuple[str, dict[str, str]]:
    """Replace every base64 ``<img src>`` with a relative asset path.

    Returns (rewritten_html, figure_label → asset_filename map).
    """
    figure_map: dict[str, str] = {}
    fallback_counter = [0]

    def _process_figure(match: re.Match[str]) -> str:
        fig_html = match.group(0)
        alt_match = re.search(r'alt="([^"]*)"', fig_html)
        cap_match = re.search(r"<figcaption[^>]*>(.*?)</figcaption>", fig_html, re.DOTALL)
        caption_text = alt_match.group(1) if alt_match else (cap_match.group(1) if cap_match else "")
        # Strip nested HTML inside caption so the label parser sees plain text.
        caption_text = re.sub(r"<[^>]+>", "", caption_text)
        label = _figure_label_from_caption(caption_text)
        fallback_counter[0] += 1
        filename_stem = _safe_label_for_filename(
            label, fallback_chapter=chapter_id, fallback_index=fallback_counter[0]
        )

        def _swap(data_match: re.Match[str]) -> str:
            mime = data_match.group("mime")
            b64 = data_match.group("b64")
            ext = _MIME_EXT.get(mime, ".bin")
            digest = hashlib.sha1(b64.encode("ascii")).hexdigest()[:10]
            existing = seen_hashes.get(digest)
            if existing:
                if label and label not in figure_map:
                    figure_map[label] = existing
                return f"assets/{existing}"
            filename = f"{filename_stem}-{digest}{ext}"
            asset_path = assets_dir / filename
            asset_path.write_bytes(base64.b64decode(b64))
            seen_hashes[digest] = filename
            if label:
                figure_map.setdefault(label, filename)
            return f"assets/{filename}"

        return _DATA_URL_RE.sub(_swap, fig_html)

    rewritten = re.sub(r"<figure[^>]*>.*?</figure>", _process_figure, html, flags=re.DOTALL)

    # Catch any base64 images that aren't wrapped in <figure>.
    def _swap_loose(data_match: re.Match[str]) -> str:
        mime = data_match.group("mime")
        b64 = data_match.group("b64")
        ext = _MIME_EXT.get(mime, ".bin")
        digest = hashlib.sha1(b64.encode("ascii")).hexdigest()[:10]
        existing = seen_hashes.get(digest)
        if existing:
            return f"assets/{existing}"
        fallback_counter[0] += 1
        filename = f"{chapter_id}-loose-{fallback_counter[0]:02d}-{digest}{ext}"
        (assets_dir / filename).write_bytes(base64.b64decode(b64))
        seen_hashes[digest] = filename
        return f"assets/{filename}"

    rewritten = _DATA_URL_RE.sub(_swap_loose, rewritten)
    return rewritten, figure_map


def _extract_body(html: str) -> str:
    m = re.search(r"<body[^>]*>(.*?)</body>", html, flags=re.DOTALL)
    if not m:
        raise SystemExit("missing <body> tag")
    body = m.group(1)
    body = re.sub(r"<footer[^>]*>.*?</footer>\s*$", "", body, flags=re.DOTALL).strip()
    return body


def _extract_head(html: str) -> str:
    m = re.search(r"<head[^>]*>(.*?)</head>", html, flags=re.DOTALL)
    if not m:
        raise SystemExit("missing <head> tag")
    return m.group(1)


_BOOK_EXTRA_CSS = """
<style>
  body { max-width: 820px; }
  nav.book-toc { margin: 1.5rem 0 3rem; padding: 1rem 1.5rem;
    background: rgba(244, 236, 216, .55); border-left: 3px solid #5a6a82;
    border-radius: 4px; }
  nav.book-toc h2 { margin: 0 0 .55rem; font-size: 1.05rem; color: #5a6a82;
    border: none; padding: 0; }
  nav.book-toc ol { margin: 0; padding-left: 1.4rem; }
  nav.book-toc li { margin: .25rem 0; line-height: 1.7; }
  nav.book-toc a { color: #2c3a52; text-decoration: none;
    border-bottom: 1px dotted rgba(90, 106, 130, .4); }
  nav.book-toc a:hover { border-bottom-style: solid; }
  hr.chapter-divider { margin: 4rem 0 2rem; border: none;
    border-top: 1px dashed rgba(127, 127, 127, .35); }
  a.back-to-top { display: inline-block; margin: 1rem 0 0; font-size: .85rem;
    color: #6a6a6a; text-decoration: none;
    border-bottom: 1px dotted rgba(127, 127, 127, .35); }
  a.back-to-top:hover { color: #2c3a52; border-bottom-style: solid; }
  @media (prefers-color-scheme: dark) {
    nav.book-toc { background: rgba(255, 255, 255, .04);
      border-left-color: #8a9ab2; }
    nav.book-toc h2 { color: #aab4c8; }
    nav.book-toc a { color: #d2d8e0; border-bottom-color: rgba(170, 180, 200, .4); }
    a.back-to-top { color: #999; }
    a.back-to-top:hover { color: #d2d8e0; }
  }
</style>
"""


def _build_html_book(
    chapter_htmls: list[tuple[str, str, str, str]],  # (id, label, title, rewritten_html)
    *,
    book_title: str,
) -> str:
    first_html = chapter_htmls[0][3]
    shared_head = _extract_head(first_html)
    shared_head = re.sub(
        r"<title>.*?</title>",
        f"<title>{book_title}</title>",
        shared_head,
        count=1,
        flags=re.DOTALL,
    )
    shared_head = shared_head + _BOOK_EXTRA_CSS

    toc_items = [
        f'    <li><a href="#{ch_id}">{label} · {title}</a></li>'
        for ch_id, label, title, _ in chapter_htmls
    ]
    toc_html = (
        '<nav class="book-toc" id="toc">\n'
        '  <h2>目录</h2>\n'
        '  <ol>\n'
        + "\n".join(toc_items)
        + "\n  </ol>\n"
        "</nav>"
    )

    chunks: list[str] = []
    for idx, (ch_id, _label, _title, html) in enumerate(chapter_htmls):
        body = _extract_body(html)
        body = re.sub(
            r"<h1(?![^>]*\bid=)",
            f'<h1 id="{ch_id}"',
            body,
            count=1,
        )
        divider = "" if idx == 0 else '<hr class="chapter-divider">\n'
        chunks.append(
            divider + body + '\n<p style="text-align:right; margin-top:2rem;">'
            '<a class="back-to-top" href="#toc">↑ 返回目录</a></p>'
        )

    body_html = (
        '<header style="text-align:center; margin: 2rem 0 1rem;">'
        f'<h1 style="font-size: 2.4rem; margin: 0; color: inherit;">{book_title}</h1>'
        '<p style="margin: .35rem 0; font-size: 1.05rem; color: #666;">'
        '由 book-agent 翻译管线生成'
        '</p>'
        '</header>\n'
        + toc_html + "\n"
        + "\n".join(chunks)
        + f'\n<footer>{book_title} · 由 book-agent 翻译管线生成</footer>'
    )

    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n'
        "<head>" + shared_head + "</head>\n"
        "<body>\n"
        + body_html
        + "\n</body>\n</html>\n"
    )


_MD_FIG_MARKER_RE = re.compile(r"^\*图：图\s*(\d+(?:\.\d+)?)([\s\S]*?)\*\s*$", re.MULTILINE)


def _inject_md_figures(md: str, figure_map: dict[str, str]) -> str:
    """Inject ``![alt](assets/…)`` before each ``*图：图N.M …*`` caption."""

    def _replace(match: re.Match[str]) -> str:
        label = match.group(1)
        full_caption = match.group(0)
        asset_filename = figure_map.get(label)
        if not asset_filename:
            return full_caption
        alt = re.sub(r"\s+", " ", match.group(2)).strip(" *")
        alt = alt[:120] if alt else f"图 {label}"
        # Two-line block: image first, then italic caption preserved.
        return f"![图 {label} {alt}](assets/{asset_filename})\n\n{full_caption}"

    return _MD_FIG_MARKER_RE.sub(_replace, md)


def _build_md_book(
    chapter_mds: list[tuple[str, str, str, str]],  # (id, label, title, md_with_images)
    *,
    book_title: str,
) -> str:
    toc = ["# " + book_title, "", "> 由 book-agent 翻译管线生成", "", "## 目录", ""]
    for ch_id, label, title, _ in chapter_mds:
        toc.append(f"- [{label} · {title}](#{ch_id})")
    toc.append("")

    parts = ["\n".join(toc)]
    for idx, (ch_id, label, title, md) in enumerate(chapter_mds):
        # Replace the chapter's first H1 with an anchor we can link to.
        # Most chapter MDs start with `# 第 N 章 …`. Add an HTML anchor
        # immediately above to keep Markdown source clean.
        header_anchor = f'<a id="{ch_id}"></a>\n\n'
        if idx == 0:
            parts.append(header_anchor + md.lstrip())
        else:
            parts.append("\n\n---\n\n" + header_anchor + md.lstrip())
    parts.append(f"\n\n---\n\n*{book_title} · 由 book-agent 翻译管线生成*\n")
    return "\n".join(parts)


def _reset_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    (path / "assets").mkdir(parents=True, exist_ok=True)


def main() -> int:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    # Resolve per-chapter export paths up front so we fail fast on missing
    # directories (rather than half-way through a 12 MB combine).
    chapter_paths = []
    for ch_id, label, title, default_dir in CHAPTERS:
        chapter_paths.append((ch_id, label, title, _resolve_export_dir(ch_id, default_dir)))

    bundles = [
        ("md-zh", "zh.md", "*-zh.md", "How Large Language Models Work · 中文版"),
        ("md-bilingual", "bilingual.md", "*-bilingual.md", "How Large Language Models Work · 中英双语版"),
        ("html-zh", "zh.html", "*-zh.html", "How Large Language Models Work · 中文版"),
        ("html-bilingual", "bilingual.html", "*-bilingual.html", "How Large Language Models Work · 中英双语版"),
    ]

    for variant, source_suffix, _glob, book_title in bundles:
        is_html = variant.startswith("html-")
        bundle_dir = OUT_ROOT / variant
        _reset_dir(bundle_dir)
        assets_dir = bundle_dir / "assets"

        # First pass: read each chapter's HTML (zh or bilingual depending on
        # variant) — that's where the base64 images live — and extract assets.
        seen_hashes: dict[str, str] = {}
        chapter_html_payloads: list[tuple[str, str, str, str]] = []
        chapter_figure_maps: dict[str, dict[str, str]] = {}
        html_suffix = "zh.html" if not variant.endswith("bilingual") else "bilingual.html"
        for ch_id, label, title, export_dir in chapter_paths:
            html_path = export_dir / f"{ch_id}-{html_suffix}"
            if not html_path.is_file():
                raise SystemExit(f"missing HTML source: {html_path}")
            html = html_path.read_text(encoding="utf-8")
            rewritten, figure_map = _extract_figures_from_html(
                html, chapter_id=ch_id, assets_dir=assets_dir, seen_hashes=seen_hashes
            )
            chapter_html_payloads.append((ch_id, label, title, rewritten))
            chapter_figure_maps[ch_id] = figure_map

        if is_html:
            book_html = _build_html_book(chapter_html_payloads, book_title=book_title)
            output_file = bundle_dir / "book.html"
            output_file.write_text(book_html, encoding="utf-8")
        else:
            md_payloads: list[tuple[str, str, str, str]] = []
            for ch_id, label, title, export_dir in chapter_paths:
                md_path = export_dir / f"{ch_id}-{source_suffix}"
                if not md_path.is_file():
                    raise SystemExit(f"missing MD source: {md_path}")
                md = md_path.read_text(encoding="utf-8")
                md_with_figs = _inject_md_figures(md, chapter_figure_maps.get(ch_id, {}))
                md_payloads.append((ch_id, label, title, md_with_figs))
            book_md = _build_md_book(md_payloads, book_title=book_title)
            output_file = bundle_dir / "book.md"
            output_file.write_text(book_md, encoding="utf-8")

        asset_count = len(list(assets_dir.iterdir()))
        size_mb = output_file.stat().st_size / (1024 * 1024)
        print(f"[done] {variant}: {output_file.name} ({size_mb:.2f} MB), {asset_count} asset(s) under {assets_dir.relative_to(ROOT)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
