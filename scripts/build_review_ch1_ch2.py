"""Concatenate ch1+ch2 bilingual HTML for review iterations.

Mirrors build_full_book_bilingual.py but restricted to the first two
chapters so we can ship a review-only file when only ch1+ch2 have been
re-translated.
"""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEST_TMP = ROOT / ".test-tmp"

CHAPTERS = [
    ("ch1", "第 1 章", "宏观图景：什么是大语言模型？", TEST_TMP / "ch1-export" / "ch1-bilingual.html"),
    ("ch2", "第 2 章", "分词器：大语言模型如何看待世界", TEST_TMP / "ch2-export" / "ch2-bilingual.html"),
]

OUT_PATH = TEST_TMP / "review-ch1-ch2-bilingual.html"


def _extract_body(html: str) -> str:
    m = re.search(r"<body[^>]*>(.*?)</body>", html, flags=re.DOTALL)
    if not m:
        raise SystemExit("missing <body> in chapter HTML")
    body = m.group(1)
    body = re.sub(r"<footer[^>]*>.*?</footer>\s*$", "", body, flags=re.DOTALL).strip()
    return body


def _extract_head(html: str) -> str:
    m = re.search(r"<head[^>]*>(.*?)</head>", html, flags=re.DOTALL)
    if not m:
        raise SystemExit("missing <head> in chapter HTML")
    return m.group(1)


def main() -> int:
    first_html = CHAPTERS[0][3].read_text(encoding="utf-8")
    shared_head = _extract_head(first_html)
    shared_head = re.sub(
        r"<title>.*?</title>",
        "<title>Review · ch1+ch2 · 中英双语</title>",
        shared_head,
        count=1,
        flags=re.DOTALL,
    )
    extra_css = """
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
    shared_head = shared_head + extra_css

    toc_items = [
        f'    <li><a href="#{ch_id}">{label} · {title}</a></li>'
        for ch_id, label, title, _ in CHAPTERS
    ]
    toc_html = (
        '<nav class="book-toc" id="toc">\n'
        '  <h2>目录</h2>\n'
        '  <ol>\n'
        + "\n".join(toc_items)
        + "\n  </ol>\n"
        "</nav>"
    )

    chapter_chunks: list[str] = []
    for idx, (ch_id, label, title, path) in enumerate(CHAPTERS):
        html = path.read_text(encoding="utf-8")
        body = _extract_body(html)
        body = re.sub(
            r"<h1(?![^>]*\bid=)",
            f'<h1 id="{ch_id}"',
            body,
            count=1,
        )
        divider = "" if idx == 0 else '<hr class="chapter-divider">\n'
        chapter_chunks.append(
            divider + body + '\n<p style="text-align:right; margin-top:2rem;">'
            '<a class="back-to-top" href="#toc">↑ 返回目录</a></p>'
        )

    body_html = (
        '<header style="text-align:center; margin: 2rem 0 1rem;">'
        '<h1 style="font-size: 2.4rem; margin: 0; color: inherit;">'
        'Review · ch1 + ch2'
        '</h1>'
        '<p style="margin: .35rem 0; font-size: 1.05rem; color: #666;">'
        '中英双语版 · 由 book-agent 翻译管线生成'
        '</p>'
        '</header>\n'
        + toc_html + "\n"
        + "\n".join(chapter_chunks)
        + '\n<footer>Review · ch1+ch2 · 中英双语版</footer>'
    )

    final = (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n'
        "<head>" + shared_head + "</head>\n"
        "<body>\n"
        + body_html
        + "\n</body>\n</html>\n"
    )

    OUT_PATH.write_text(final, encoding="utf-8")
    size_kb = OUT_PATH.stat().st_size / 1024
    print(f"[done] wrote {OUT_PATH} ({size_kb:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
