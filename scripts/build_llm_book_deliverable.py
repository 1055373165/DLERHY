"""Package the full-book bilingual deliverable into ``LLM-Book/``.

What it produces under the project root:

    LLM-Book/
      llm-book-full-bilingual.md          # the combined markdown
      assets/
        ch1-fig1.1.png ... ch9-figN.M.png # all figure crops

The per-chapter ``*-bilingual.md`` files keep figures as plain caption
placeholders (``*图：…*``). For the user-facing deliverable we want
actual images inline, so this script:

1. For each chapter, walks the corresponding bilingual HTML, decodes
   every ``<img src="data:image/png;base64,…">`` plus its
   ``<figcaption>`` text, and saves the bytes to
   ``LLM-Book/assets/<chapter>-fig<N.M>.png``.
2. Reads the per-chapter MD, finds each ``*图：…*`` placeholder line,
   and replaces it (in document order) with
   ``![caption](assets/<file>)`` followed by an italic caption.
3. Concatenates all 9 rewritten chapter MDs into one combined
   markdown with a book title + chapter TOC at the top.
"""
from __future__ import annotations

import base64
import html as _html
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "LLM-Book"
ASSETS_DIR = OUT_DIR / "assets"

CHAPTERS = [
    ("ch1", "第 1 章", "宏观图景：什么是大语言模型？", ROOT / ".test-tmp/ch1-export"),
    ("ch2", "第 2 章", "分词器:大语言模型如何看待世界", ROOT / ".test-tmp/ch2-export"),
    ("ch3", "第 3 章", "Transformer 架构:输入如何转换为输出", ROOT / ".test-tmp/ch3-export-v2"),
    ("ch4", "第 4 章", "LLM 如何学习", ROOT / ".test-tmp/ch4-export"),
    ("ch5", "第 5 章", "如何约束 LLM 的行为", ROOT / ".test-tmp/ch5-export"),
    ("ch6", "第 6 章", "超越自然语言处理", ROOT / ".test-tmp/ch6-export"),
    ("ch7", "第 7 章", "LLM 的误解、局限与新兴能力", ROOT / ".test-tmp/ch7-export"),
    ("ch8", "第 8 章", "用大语言模型设计解决方案", ROOT / ".test-tmp/ch8-export"),
    ("ch9", "第 9 章", "构建与使用 LLM 的伦理", ROOT / ".test-tmp/ch9-export"),
]


def _slug_for_caption(caption: str) -> str | None:
    """Pull "N.M" out of a Chinese or English figure caption, if present."""
    m = re.match(r"^\s*(?:图|Figure|Fig\.?)\s*(\d+\.\d+)", caption)
    if m:
        return m.group(1)
    return None


def _strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


_FIGURE_RE = re.compile(
    r"<figure[^>]*class=['\"]figure['\"][^>]*>(.*?)</figure>",
    flags=re.DOTALL,
)
_IMG_RE = re.compile(
    r"<img[^>]*src=\"data:image/(png|jpeg);base64,([^\"]+)\"",
    flags=re.DOTALL,
)
_FIGCAP_RE = re.compile(r"<figcaption[^>]*>(.*?)</figcaption>", flags=re.DOTALL)


def extract_figures(html_text: str) -> list[tuple[str, str, str]]:
    """Return ordered ``(format, base64_data, caption_text)`` tuples."""
    out: list[tuple[str, str, str]] = []
    for m in _FIGURE_RE.finditer(html_text):
        body = m.group(1)
        img_m = _IMG_RE.search(body)
        if not img_m:
            continue
        cap_m = _FIGCAP_RE.search(body)
        caption = _strip_tags(cap_m.group(1)) if cap_m else ""
        out.append((img_m.group(1), img_m.group(2), caption))
    return out


_MD_FIGURE_PLACEHOLDER = re.compile(r"^\*图：(.+)\*$", flags=re.MULTILINE)
_FIGURE_NUM_RE = re.compile(r"(?:图|Figure|Fig\.?)\s*(\d+\.\d+)")


def rewrite_md_with_images(
    md_text: str,
    figures: list[tuple[str, str, str]],
) -> tuple[str, list[tuple[str, str, str]]]:
    """Replace ``*图：…*`` placeholder lines with inline image refs.

    Matches placeholders to figures by parsing the "N.M" figure number
    from both sides (NOT by position), so a MD that's missing a few
    placeholders still produces correct ordering and any unmatched
    figures can be appended at the end of the chapter.

    ``figures`` is a list of ``(figure_number_or_None, caption_text,
    asset_relative_path)``. Returns the rewritten MD plus the list of
    figures that did NOT get inlined (so the caller can append them).
    """
    by_num: dict[str, tuple[str, str]] = {}
    no_num: list[tuple[str, str]] = []
    for num, cap, asset in figures:
        if num:
            by_num[num] = (cap, asset)
        else:
            no_num.append((cap, asset))
    used: set[str] = set()

    def _sub(m: re.Match[str]) -> str:
        caption_inside = m.group(1).strip()
        num_m = _FIGURE_NUM_RE.search(caption_inside)
        if num_m and num_m.group(1) in by_num:
            num = num_m.group(1)
            cap, asset = by_num[num]
            used.add(num)
            alt = cap.replace("[", "").replace("]", "")[:160] or "figure"
            return f"![{alt}]({asset})\n\n*{cap}*"
        # Fallback: use the next un-numbered figure if any.
        if no_num:
            cap, asset = no_num.pop(0)
            alt = cap.replace("[", "").replace("]", "")[:160] or "figure"
            return f"![{alt}]({asset})\n\n*{cap}*"
        return m.group(0)

    new_md = _MD_FIGURE_PLACEHOLDER.sub(_sub, md_text)
    leftover = [
        (num, cap, asset)
        for num, cap, asset in figures
        if num and num not in used
    ]
    # No-num figures consumed lazily above are gone from ``no_num`` now.
    leftover.extend((None, cap, asset) for cap, asset in no_num)
    return new_md, leftover


def main() -> int:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    per_chapter_md: list[str] = []
    saved_total = 0
    for ch_id, ch_label, ch_title, base in CHAPTERS:
        html_path = base / f"{ch_id}-bilingual.html"
        md_path = base / f"{ch_id}-bilingual.md"
        if not html_path.is_file() or not md_path.is_file():
            print(f"[warn] missing {html_path} or {md_path}; skipping")
            continue
        html_text = html_path.read_text(encoding="utf-8")
        md_text = md_path.read_text(encoding="utf-8")

        figs = extract_figures(html_text)
        records: list[tuple[str | None, str, str]] = []
        used_names: set[str] = set()
        for fmt, b64, caption in figs:
            slug = _slug_for_caption(caption) or f"k{len(records) + 1}"
            ext = "png" if fmt == "png" else "jpg"
            name = f"{ch_id}-fig{slug}.{ext}"
            n = 2
            while name in used_names:
                name = f"{ch_id}-fig{slug}-{n}.{ext}"
                n += 1
                if n > 9:
                    break
            used_names.add(name)
            (ASSETS_DIR / name).write_bytes(base64.b64decode(b64))
            saved_total += 1
            fig_num_m = _FIGURE_NUM_RE.match(caption)
            fig_num = fig_num_m.group(1) if fig_num_m else None
            records.append((fig_num, caption, f"assets/{name}"))

        new_md, leftover = rewrite_md_with_images(md_text, records)
        if leftover:
            tail = ["", "", "### 本章插图（补充）", ""]
            for num, cap, asset in leftover:
                alt = (cap or "figure")[:160]
                tail.append(f"![{alt}]({asset})")
                tail.append("")
                if cap:
                    tail.append(f"*{cap}*")
                    tail.append("")
            new_md = new_md.rstrip() + "\n" + "\n".join(tail)
        print(
            f"[{ch_id}] saved {len(records)} images; inlined "
            f"{len(records) - len(leftover)}, appended {len(leftover)}"
        )
        per_chapter_md.append(new_md)

    # Compose the final book.
    intro = [
        "# How Large Language Models Work · 中英双语版",
        "",
        "> 由 **book-agent** 翻译管线生成。",
        "> 每段中文翻译下方的 `<details><summary>英文原文</summary>...</details>` 即为对应的原文，可点击展开。",
        "> 图片资源位于同目录的 `assets/` 文件夹（共 " + str(saved_total) + " 张）。",
        "",
        "## 目录",
        "",
    ]
    for ch_id, ch_label, ch_title, _ in CHAPTERS:
        anchor = f"{ch_label}-{ch_title}".replace(" ", "-").replace(":", "").replace("：", "")
        intro.append(f"- [{ch_label} · {ch_title}](#{anchor.lower()})")
    intro.extend(["", "---", ""])

    body = "\n\n---\n\n".join(per_chapter_md)
    final = "\n".join(intro) + body + "\n\n---\n\n_由 book-agent 翻译管线生成 · 模型 deepseek-chat / deepseek-v4-flash_\n"

    out_md = OUT_DIR / "llm-book-full-bilingual.md"
    out_md.write_text(final, encoding="utf-8")
    print(f"\n[done] wrote {out_md} ({out_md.stat().st_size / 1024:.1f} KB)")
    print(f"[done] saved {saved_total} images under {ASSETS_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
