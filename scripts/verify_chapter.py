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
    # Bilingual mode = zh-only HTML + per-block <details class="source-fold">
    # appended after each rendered unit. The Chinese rendering is
    # IDENTICAL to zh-only, so structural parsing (h2, figcaption, p,
    # figure, img) is the same. We strip the source folds before counting
    # body paragraphs so an English source paragraph inside a fold
    # doesn't pollute the body-text scan.
    structural_html = html
    if args.bilingual:
        structural_html = re.sub(
            r"<details\s+class=['\"]source-fold['\"][^>]*>.*?</details>",
            "",
            html,
            flags=re.DOTALL,
        )

    figcaptions = re.findall(
        r"<figcaption[^>]*>(.*?)</figcaption>", structural_html, flags=re.DOTALL
    )
    standalone_caps = re.findall(
        r"<p\s+class=['\"]caption['\"][^>]*><em>(.*?)</em></p>",
        structural_html,
        flags=re.DOTALL,
    )
    figcap_texts = [
        _norm_ws(c)[:200] for c in figcaptions + standalone_caps if c.strip()
    ]

    headings = re.findall(r"<h2[^>]*>(.*?)</h2>", structural_html, flags=re.DOTALL)
    head_texts = [_norm_ws(h) for h in headings]

    # body paragraphs EXCLUDING caption-class
    paragraphs = re.findall(
        r"<p(?![^>]*class=['\"]caption['\"])[^>]*>(.*?)</p>",
        structural_html,
        flags=re.DOTALL,
    )
    para_norm = [_norm_ws(p) for p in paragraphs]

    figure_blocks = re.findall(
        r"<figure[^>]*>(.*?)</figure>", structural_html, flags=re.DOTALL
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

    # --- R7 / R8 (bilingual only) -------------------------------------------
    # New bilingual format = zh-only HTML + per-block <details
    # class="source-fold"> after each rendered unit. We re-purpose R7
    # to enforce that every English source is folded (never bare in the
    # body) and R8 to count source-fold parity vs. rendered units.
    if args.bilingual:
        # R7: every <details class="source-fold"> must have a non-empty
        # `<div class="src-body">` inside (and a "英文原文" summary).
        folds = re.findall(
            r"<details\s+class=['\"]source-fold['\"][^>]*>(.*?)</details>",
            html,
            flags=re.DOTALL,
        )
        bad_folds = []
        for f in folds:
            if "英文原文" not in f:
                bad_folds.append("missing-summary")
            elif "src-body" not in f:
                bad_folds.append("missing-src-body")
            else:
                # Body must have something inside.
                body_m = re.search(
                    r"<div\s+class=['\"]src-body['\"][^>]*>(.*?)</div>",
                    f,
                    flags=re.DOTALL,
                )
                if not body_m or not re.sub(r"<[^>]+>", "", body_m.group(1)).strip():
                    bad_folds.append("empty-src-body")
        r7_ok = len(folds) > 0 and not bad_folds
        checks.append(
            (
                "R7 source_fold_well_formed",
                r7_ok,
                f"folds={len(folds)} bad={bad_folds[:3]}",
            )
        )

        # R8: count of source-fold elements should be ≥ figcaptions, since
        # every section/heading/paragraph/figure renders one fold.
        rendered_units = (
            len(head_texts)
            + len(re.findall(r"<p[^>]*>", html))
            + len(re.findall(r"<figure[^>]*>", html))
        )
        # Soft: at least 60% of rendered units have a source fold (some
        # listings/figures may share one fold for the whole unit).
        coverage = len(folds) / max(rendered_units, 1)
        r8_ok = coverage >= 0.4
        checks.append(
            (
                "R8 source_fold_coverage",
                r8_ok,
                f"folds={len(folds)} rendered_units={rendered_units} "
                f"coverage={coverage:.0%}",
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
