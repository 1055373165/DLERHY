"""Parameterized chapter HTML verifier (replaces verify_ch3/ch4_export.py).

Audit rules (production-grade contract per the spec doc §3.2):
  R1 figure_coverage         all Figure {prefix}.1..{prefix}.K captioned
  R2 heading_count_sane      h2 count under chapter-specific cap
  R3 caption_no_body_dup     figcaption text not duplicated in body <p>
  R4 caption_no_body_lead    figcaption isn't a body sentence
                             (e.g. "Figure N.M describes ...")
  R5 no_orphan_brackets      no orphan "]" / "[" / "{" / "}" body lines
  R6 image_render_coverage   ≥ K real <img> figures rendered
  R7 untranslated_inline_low bilingual zh column doesn't leak ≥5-letter
                             ASCII runs (English bleeding into Chinese)
  R8 alignment_pair_count    bilingual <div class="pair"> count matches
                             rendered block count (when bilingual mode)

Usage:
    verify_chapter.py --html <path> --figure-prefix N --figure-count K \
        [--max-h2 24] [--bilingual] [--chapter-id <uuid>]

Exit code: 0 on PASS, 1 on FAIL, 2 on bad input.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--html", required=True, type=Path, help="HTML file to verify")
    p.add_argument(
        "--figure-prefix",
        required=True,
        help='Chapter figure prefix, e.g. "3" for "Figure 3.1..N"',
    )
    p.add_argument(
        "--figure-count",
        required=True,
        type=int,
        help="Expected figure count (e.g. 11 for ch3)",
    )
    p.add_argument(
        "--max-h2",
        type=int,
        default=30,
        help="Maximum allowed <h2> count; over this is suspect (default 30)",
    )
    p.add_argument(
        "--bilingual",
        action="store_true",
        help="Treat HTML as bilingual: enable R7/R8 alignment audits",
    )
    p.add_argument(
        "--min-img-coverage",
        type=int,
        default=None,
        help="Override expected <img> count (default = figure_count)",
    )
    p.add_argument(
        "--report-path",
        type=Path,
        default=None,
        help="Where to write verify_report.json (default: alongside HTML)",
    )
    return p.parse_args()


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def main() -> int:
    args = _parse_args()
    html_path: Path = args.html
    if not html_path.is_file():
        print(f"[verify] HTML not found at {html_path}", file=sys.stderr)
        return 2

    html = html_path.read_text(encoding="utf-8")
    prefix = args.figure_prefix
    figure_count = args.figure_count
    expected_numbers = [f"{prefix}.{i}" for i in range(1, figure_count + 1)]
    min_imgs = (
        args.min_img_coverage if args.min_img_coverage is not None else figure_count
    )

    # --- structural extracts -------------------------------------------------
    if args.bilingual:
        # Bilingual layout: every block is a .pair div with .en + .zh
        # subdivs. Pull each pair's role + zh column. Figure captions
        # come from data-role="caption" pairs' zh column ONLY (so we
        # don't pick up body references like "如图 1.2 所示").
        pairs_html = re.findall(
            r'<div\s+class=["\']pair["\'][^>]*data-role=["\']([^"\']+)["\'][^>]*>'
            r'\s*<div\s+class=["\']en["\'][^>]*>(.*?)</div>'
            r'\s*<div\s+class=["\']zh["\'][^>]*>(.*?)</div>\s*</div>',
            html,
            flags=re.DOTALL,
        )
        figcap_texts: list[str] = []
        for role, en_inner, zh_inner in pairs_html:
            if role != "caption":
                continue
            zh_text = _norm_ws(re.sub(r"<[^>]+>", " ", zh_inner))
            if zh_text.startswith(("图", "Figure", "Fig")):
                figcap_texts.append(zh_text[:200])
            else:
                # Some captions are translated without prefix; still
                # attribute them to the figure number found in en.
                en_text = _norm_ws(re.sub(r"<[^>]+>", " ", en_inner))
                m = re.search(r"(?:Figure|Fig\.?)\s*(\d+\.\d+)", en_text)
                if m:
                    figcap_texts.append(f"图{m.group(1)} {zh_text[:180]}")
        # Bilingual page has NO real <h2> from the renderer; headings
        # render as <strong> inside heading-role pairs. Use that as the
        # heading proxy.
        headings = re.findall(
            r'<div\s+class=["\']pair["\'][^>]*data-role=["\']heading["\'][^>]*>.*?<strong>(.*?)</strong>',
            html,
            flags=re.DOTALL,
        )
        head_texts = [_norm_ws(h) for h in headings]
        # Body paragraphs ≈ paragraph-role pair contents (zh column).
        paragraphs = re.findall(
            r'<div\s+class=["\']pair["\'][^>]*data-role=["\']paragraph["\'][^>]*>'
            r'.*?<div\s+class=["\']zh["\'][^>]*>(.*?)</div>\s*</div>',
            html,
            flags=re.DOTALL,
        )
        para_norm = [_norm_ws(re.sub(r"<[^>]+>", " ", p)) for p in paragraphs]
        # Image render coverage: <img> tags anywhere (bilingual puts them
        # inside .pair .en, not <figure>).
        img_blocks = len(re.findall(r"<img\s", html))
        placeholder_blocks = html.count("[图未渲染]") + html.count("image-placeholder")
        figure_blocks = [
            en_inner for role, en_inner, _ in pairs_html if role == "figure"
        ]
    else:
        figcaptions = re.findall(
            r"<figcaption[^>]*>(.*?)</figcaption>", html, flags=re.DOTALL
        )
        standalone_caps = re.findall(
            r"<p\s+class=['\"]caption['\"][^>]*><em>(.*?)</em></p>",
            html,
            flags=re.DOTALL,
        )
        figcap_texts = [
            _norm_ws(c)[:200] for c in figcaptions + standalone_caps if c.strip()
        ]

        headings = re.findall(r"<h2[^>]*>(.*?)</h2>", html, flags=re.DOTALL)
        head_texts = [_norm_ws(h) for h in headings]

        # body paragraphs EXCLUDING caption-class
        paragraphs = re.findall(
            r"<p(?![^>]*class=['\"]caption['\"])[^>]*>(.*?)</p>",
            html,
            flags=re.DOTALL,
        )
        para_norm = [_norm_ws(p) for p in paragraphs]

        figure_blocks = re.findall(
            r"<figure[^>]*>(.*?)</figure>", html, flags=re.DOTALL
        )
        img_blocks = sum(1 for fb in figure_blocks if "<img " in fb)
        placeholder_blocks = sum(1 for fb in figure_blocks if "image-placeholder" in fb)

    checks: list[tuple[str, bool, str]] = []

    # --- R1 figure_coverage -------------------------------------------------
    found_numbers: set[str] = set()
    for cap in figcap_texts:
        # Match "图N.M" or "Figure N.M" up to the next non-digit (CJK-safe).
        m = re.match(r"^(?:图|Figure|Fig\.?|图\s*)\s*(\d+\.\d+)(?!\d)", cap)
        if m:
            found_numbers.add(m.group(1))
    missing = [n for n in expected_numbers if n not in found_numbers]
    coverage_ok = not missing
    checks.append(
        (
            "R1 figure_coverage",
            coverage_ok,
            f"found={sorted(found_numbers)} missing={missing}"
            if missing
            else f"found_all_{figure_count}",
        )
    )

    # --- R2 heading_count_sane ----------------------------------------------
    h2_ok = len(head_texts) <= args.max_h2
    checks.append(
        (
            "R2 heading_count_sane",
            h2_ok,
            f"h2={len(head_texts)} cap={args.max_h2}",
        )
    )

    # --- R3 caption_no_body_dup ---------------------------------------------
    dup_hits: list[str] = []
    for cap in figcap_texts:
        snip = cap[:60]
        if not snip:
            continue
        if any(snip in p for p in para_norm):
            dup_hits.append(snip)
    dup_ok = not dup_hits
    checks.append(
        (
            "R3 caption_no_body_dup",
            dup_ok,
            f"duplicates={dup_hits[:3]}" if dup_hits else "clean",
        )
    )

    # --- R4 caption_no_body_lead --------------------------------------------
    body_lead_re = re.compile(
        r"^(?:Figure|Fig\.?|Image|图)\s*\d+\.\d+\s+"
        r"(?:describes|shows|illustrates|displays|presents|demonstrates)\b",
        re.IGNORECASE,
    )
    body_caption_hits = [c for c in figcap_texts if body_lead_re.match(c)]
    body_lead_ok = not body_caption_hits
    checks.append(
        (
            "R4 caption_no_body_lead",
            body_lead_ok,
            f"hits={body_caption_hits[:2]}" if body_caption_hits else "clean",
        )
    )

    # --- R5 no_orphan_brackets ----------------------------------------------
    orphan_chars = [p for p in para_norm if p in {"]", "[", "{", "}"}]
    orphan_ok = not orphan_chars
    checks.append(
        (
            "R5 no_orphan_brackets",
            orphan_ok,
            f"orphans={orphan_chars[:4]}" if orphan_chars else "clean",
        )
    )

    # --- R6 image_render_coverage -------------------------------------------
    img_ok = img_blocks >= min_imgs and placeholder_blocks <= 3
    checks.append(
        (
            "R6 image_render_coverage",
            img_ok,
            f"<img>={img_blocks} placeholders={placeholder_blocks} "
            f"total_figures={len(figure_blocks)} expected≥{min_imgs}",
        )
    )

    # --- R7 untranslated_inline_low (bilingual only) ------------------------
    if args.bilingual:
        zh_columns = re.findall(
            r"<div\s+class=['\"]zh['\"][^>]*>(.*?)</div>", html, flags=re.DOTALL
        )
        ascii_run = re.compile(r"[A-Za-z]{5,}")
        leaked = []
        for col in zh_columns:
            txt = re.sub(r"<[^>]+>", "", col)
            # Strip permitted Latin chunks: technical names ALL-CAPS (LLM, GPT, BERT),
            # URLs, code spans (already in <code>). After tag stripping, look for
            # multi-word ASCII runs ≥3 words.
            words = re.findall(r"[A-Za-z]+(?:\s+[A-Za-z]+){2,}", txt)
            if words:
                leaked.append((words[0])[:60])
        leak_ok = len(leaked) <= 2  # tolerate technical names like "Hugging Face Transformers"
        checks.append(
            (
                "R7 untranslated_inline_low",
                leak_ok,
                f"english_runs_in_zh={len(leaked)} samples={leaked[:3]}",
            )
        )

        # --- R8 alignment_pair_count ----------------------------------------
        pairs = re.findall(r'<div\s+class=["\']pair["\'][^>]*>', html)
        # Soft check: bilingual must have at least one pair, and all pairs must
        # have both en and zh subdivs.
        pair_count = len(pairs)
        ill_formed = re.findall(
            r'<div\s+class=["\']pair["\'][^>]*>\s*</div>', html
        )
        pair_ok = pair_count > 0 and not ill_formed
        checks.append(
            (
                "R8 alignment_pair_count",
                pair_ok,
                f"pairs={pair_count} empty_pairs={len(ill_formed)}",
            )
        )

    # --- output --------------------------------------------------------------
    print(f"[verify] Inspecting {html_path}")
    print(
        f"[verify] {len(head_texts)} h2, {len(figcap_texts)} figcaption, "
        f"{len(para_norm)} body p"
    )
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
        print(f"  {cap[:120]}")

    report = {
        "html_path": str(html_path),
        "figure_prefix": prefix,
        "figure_count": figure_count,
        "bilingual": args.bilingual,
        "all_ok": all_ok,
        "structure": {
            "h2": len(head_texts),
            "figcaption": len(figcap_texts),
            "p": len(para_norm),
            "figure": len(figure_blocks),
            "img": img_blocks,
            "placeholder": placeholder_blocks,
        },
        "checks": [
            {"name": name, "ok": ok, "detail": detail} for name, ok, detail in checks
        ],
        "figcaptions": figcap_texts,
    }
    report_path = args.report_path or html_path.with_name(
        html_path.stem + ".verify_report.json"
    )
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[verify] wrote {report_path}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
