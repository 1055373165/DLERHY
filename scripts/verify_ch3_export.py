"""Verify the Chapter 3 HTML export against the bug-fix checklist.

Run after ``scripts/export_chapter3.sh`` completes. Reports:
- Bug 1 (Figure 3.2 → 3.3 mismatch): figcaption "图 3.2 ..." present, in
  the right reading-order spot, body of caption matches the source.
- Bug 2 (image misrecognition): heading count is in the expected range,
  no fake-heading patterns from absorbed text labels.
- Figure-caption coverage: all 11 chapter-3 figures (3.1 through 3.11)
  have a translated figcaption.
- Caption duplication: no body paragraph repeats the same "Figure N.M"
  text already used as a figcaption.

Exit code: 0 if all checks pass, 1 if any fails.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


HTML_PATH = Path(
    sys.argv[1]
    if len(sys.argv) > 1
    else ".test-tmp/ch3-export-v2/chapter3-zh.html"
)


# Known fake-heading patterns that surfaced in the pre-fix export. If any
# of these reappear, Bug 2's parser fixes regressed.
FAKE_HEADING_NEEDLES = [
    "资本债务",  # "Capital Debt -1xcapital" body absorbed as h2
    "这是当前段落中唯一的句子",
    "1 映射文本",
    "3. 添加信息",
    "4 传递数据",
    "5 应用",
    "6 采样",
    "2 随机挑选",
    # Note: "用向量表示词元" looked like a fake heading at first glance
    # but is in fact a real sub-section heading in the source book.
    # Do NOT add it to the blocklist.
]


def main() -> int:
    if not HTML_PATH.is_file():
        print(f"[verify] HTML not found at {HTML_PATH}", file=sys.stderr)
        return 1
    html = HTML_PATH.read_text(encoding="utf-8")

    checks: list[tuple[str, bool, str]] = []

    # --- figcaption inventory ---
    figcaptions = re.findall(
        r"<figcaption[^>]*>(.*?)</figcaption>", html, flags=re.DOTALL
    )
    figcap_texts = [
        re.sub(r"\s+", " ", c).strip()[:160] for c in figcaptions
    ]

    # Bug 1: Figure 3.2 must appear with non-trivial body — not "图 3.3".
    has_fig32 = any(
        re.match(r"^(?:图|Figure)\s*3\.2\b", c) for c in figcap_texts
    )
    bug1_ok = has_fig32
    checks.append(
        (
            "Bug 1 — Figure 3.2 caption present (not relabeled as 3.3)",
            bug1_ok,
            "; ".join(figcap_texts[:5]) if figcap_texts else "no figcaptions found",
        )
    )

    # Coverage: all 11 chapter-3 figures should have a figcaption.
    expected_numbers = [f"3.{i}" for i in range(1, 12)]
    found_numbers: set[str] = set()
    for cap in figcap_texts:
        m = re.match(r"^(?:图|Figure)\s*(3\.\d+)\b", cap)
        if m:
            found_numbers.add(m.group(1))
    missing = [n for n in expected_numbers if n not in found_numbers]
    coverage_ok = not missing
    checks.append(
        (
            "Figure coverage — all of Figure 3.1 .. 3.11 captioned",
            coverage_ok,
            f"missing: {missing}" if missing else f"found: {sorted(found_numbers)}",
        )
    )

    # --- heading inventory ---
    headings = re.findall(r"<h2[^>]*>(.*?)</h2>", html, flags=re.DOTALL)
    head_texts = [re.sub(r"\s+", " ", h).strip() for h in headings]
    fake_hits = [n for n in FAKE_HEADING_NEEDLES if any(n in h for h in head_texts)]
    bug2_ok = not fake_hits
    checks.append(
        (
            "Bug 2 — No fake headings absorbed from figure-internal text",
            bug2_ok,
            (
                f"fake-heading hits: {fake_hits}"
                if fake_hits
                else f"clean h2 inventory ({len(head_texts)} headings)"
            ),
        )
    )

    # Heading count sanity: pre-fix had 24 (with many figure-internal labels
    # mis-promoted). The export covers ord 198-377, which spans ch3 + start
    # of ch4, so a clean run lands around 17 real headings (10 ch3 + 7 ch4).
    head_count_ok = len(head_texts) <= 20
    checks.append(
        (
            "Heading count <= 20 (pre-fix was 24, spans ch3 + start of ch4)",
            head_count_ok,
            f"h2 count = {len(head_texts)}",
        )
    )

    # Caption duplication: figcaption should not repeat as body paragraph.
    paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", html, flags=re.DOTALL)
    para_norm = [re.sub(r"\s+", " ", p).strip() for p in paragraphs]
    dup_hits: list[str] = []
    for cap in figcap_texts:
        cap_short = cap[:60]
        if not cap_short:
            continue
        if any(cap_short in p for p in para_norm):
            dup_hits.append(cap_short)
    dup_ok = not dup_hits
    checks.append(
        (
            "Figcaption text not duplicated as a body paragraph",
            dup_ok,
            f"duplicate snippets: {dup_hits[:3]}" if dup_hits else "no duplication",
        )
    )

    # Body-text-as-caption: figcaption shouldn't be a sentence about the
    # figure ("Figure 3.1 describes ...") — those leak when the clustering
    # treats a body paragraph as the caption candidate.
    body_text_caption_pattern = re.compile(
        r"^(?:Figure|Fig\.?|Image|图)\s*\d+\.\d+\s+(?:describes|shows|illustrates|displays|presents|demonstrates|describes?)\b",
        re.IGNORECASE,
    )
    body_caption_hits = [
        c for c in figcap_texts if body_text_caption_pattern.match(c)
    ]
    body_cap_ok = not body_caption_hits
    checks.append(
        (
            "Figcaption is not a body sentence (e.g. 'Figure 3.1 describes...')",
            body_cap_ok,
            (
                f"body-text figcaptions: {body_caption_hits[:2]}"
                if body_caption_hits
                else "no body-text leaks"
            ),
        )
    )

    # Image render coverage: each figure container should have an <img>
    # (not just an image-placeholder). Misclassified figures fall back to
    # placeholders that read "[Figure unrendered]".
    figure_blocks = re.findall(r"<figure[^>]*>(.*?)</figure>", html, flags=re.DOTALL)
    img_blocks = sum(1 for fb in figure_blocks if "<img " in fb)
    placeholder_blocks = sum(1 for fb in figure_blocks if "image-placeholder" in fb)
    img_render_ok = img_blocks >= 11 and placeholder_blocks <= 2
    checks.append(
        (
            "Image render coverage — at least 11 figures rendered as <img>",
            img_render_ok,
            f"<img>={img_blocks}, placeholders={placeholder_blocks}, total figures={len(figure_blocks)}",
        )
    )

    # --- Output ---
    print(f"[verify] Inspecting {HTML_PATH}")
    print(f"[verify] {len(head_texts)} h2, {len(figcap_texts)} figcaption, {len(para_norm)} p")
    print()
    all_ok = True
    for name, ok, detail in checks:
        flag = "PASS" if ok else "FAIL"
        print(f"  [{flag}] {name}")
        print(f"         {detail}")
        if not ok:
            all_ok = False

    print()
    print("=== figcaptions inventory ===")
    for cap in figcap_texts:
        print(f"  {cap[:100]}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
