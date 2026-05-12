"""Concatenate the 9 chapter ``*-bilingual.html`` files into one document.

Each chapter export wraps itself in ``<html><head>...<body>...</body>``,
so a naive cat would produce 9 documents back-to-back. We instead:

1. Reuse chapter 1's ``<head>`` (CSS, meta) as the book's shared style.
2. Strip the per-chapter document wrapper from chapters 2-9, keeping
   their body content and the per-chapter ``<h1>`` heading.
3. Add a top-level TOC linking to each chapter via an ``id`` we set on
   the chapter's first heading.
4. Replace per-chapter footers with one book-level footer.
"""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEST_TMP = ROOT / ".test-tmp"

CHAPTERS = [
    ("ch1", "第 1 章", "宏观图景：什么是大语言模型？", TEST_TMP / "ch1-export" / "ch1-bilingual.html"),
    ("ch2", "第 2 章", "分词器：大语言模型如何看待世界", TEST_TMP / "ch2-export" / "ch2-bilingual.html"),
    ("ch3", "第 3 章", "Transformer 架构：输入如何转换为输出", TEST_TMP / "ch3-export-v2" / "ch3-bilingual.html"),
    ("ch4", "第 4 章", "LLM 如何学习", TEST_TMP / "ch4-export" / "ch4-bilingual.html"),
    ("ch5", "第 5 章", "如何约束 LLM 的行为", TEST_TMP / "ch5-export" / "ch5-bilingual.html"),
    ("ch6", "第 6 章", "超越自然语言处理", TEST_TMP / "ch6-export" / "ch6-bilingual.html"),
    ("ch7", "第 7 章", "LLM 的误解、局限与新兴能力", TEST_TMP / "ch7-export" / "ch7-bilingual.html"),
    ("ch8", "第 8 章", "用大语言模型设计解决方案", TEST_TMP / "ch8-export" / "ch8-bilingual.html"),
    ("ch9", "第 9 章", "构建与使用 LLM 的伦理", TEST_TMP / "ch9-export" / "ch9-bilingual.html"),
]

OUT_PATH = TEST_TMP / "llm-book-full-bilingual.html"


def _extract_body(html: str) -> str:
    """Pull everything between ``<body>...</body>`` (exclusive of footer)."""
    m = re.search(r"<body[^>]*>(.*?)</body>", html, flags=re.DOTALL)
    if not m:
        raise SystemExit("missing <body> in chapter HTML")
    body = m.group(1)
    # Drop the per-chapter footer — book-level footer replaces it.
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
    # Swap the chapter-specific <title> to the book title.
    shared_head = re.sub(
        r"<title>.*?</title>",
        "<title>How Large Language Models Work · 中英双语版</title>",
        shared_head,
        count=1,
        flags=re.DOTALL,
    )
    # Additional book-level CSS overrides (chapter dividers, TOC styling,
    # back-to-top link).
    extra_css = """
<style>
  /* Book-level layout tweaks added by build_full_book_bilingual.py. */
  body { max-width: 820px; }
  nav.book-toc {
    margin: 1.5rem 0 3rem;
    padding: 1rem 1.5rem;
    background: rgba(244, 236, 216, .55);
    border-left: 3px solid #5a6a82;
    border-radius: 4px;
  }
  nav.book-toc h2 {
    margin: 0 0 .55rem;
    font-size: 1.05rem;
    color: #5a6a82;
    border: none;
    padding: 0;
  }
  nav.book-toc ol { margin: 0; padding-left: 1.4rem; }
  nav.book-toc li { margin: .25rem 0; line-height: 1.7; }
  nav.book-toc a {
    color: #2c3a52;
    text-decoration: none;
    border-bottom: 1px dotted rgba(90, 106, 130, .4);
  }
  nav.book-toc a:hover { border-bottom-style: solid; }
  hr.chapter-divider {
    margin: 4rem 0 2rem;
    border: none;
    border-top: 1px dashed rgba(127, 127, 127, .35);
  }
  a.back-to-top {
    display: inline-block;
    margin: 1rem 0 0;
    font-size: .85rem;
    color: #6a6a6a;
    text-decoration: none;
    border-bottom: 1px dotted rgba(127, 127, 127, .35);
  }
  a.back-to-top:hover { color: #2c3a52; border-bottom-style: solid; }
  @media (prefers-color-scheme: dark) {
    nav.book-toc {
      background: rgba(255, 255, 255, .04);
      border-left-color: #8a9ab2;
    }
    nav.book-toc h2 { color: #aab4c8; }
    nav.book-toc a { color: #d2d8e0; border-bottom-color: rgba(170, 180, 200, .4); }
    a.back-to-top { color: #999; }
    a.back-to-top:hover { color: #d2d8e0; }
  }
</style>
"""
    shared_head = shared_head + extra_css

    # Build TOC.
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
        # Tag the first <h1> with the chapter anchor so the TOC link
        # lands at the chapter heading rather than the top of the page.
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
        'How Large Language Models Work'
        '</h1>'
        '<p style="margin: .35rem 0; font-size: 1.05rem; color: #666;">'
        '中英双语版 · 由 book-agent 翻译管线生成'
        '</p>'
        '</header>\n'
        + toc_html + "\n"
        + "\n".join(chapter_chunks)
        + '\n<footer>How Large Language Models Work · 中英双语版 · 由 book-agent 翻译管线生成 · 模型 deepseek-chat / deepseek-v4-flash</footer>'
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
