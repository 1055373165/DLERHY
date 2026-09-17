"""Parameterized chapter HTML verifier (CLI over book_agent.export.qa).

Audit rules (production-grade contract per the spec doc §3.2):
  R1 figure_coverage         all Figure {prefix}.1..{prefix}.K captioned
  R2 heading_count_sane      h2 count under chapter-specific cap
  R3 caption_no_body_dup     figcaption text not duplicated in body <p>
  R4 caption_no_body_lead    figcaption isn't a body sentence
  R5 no_orphan_brackets      no orphan "]" / "[" / "{" / "}" body lines
  R6 image_render_coverage   ≥ K real <img> figures rendered
  R7 source_fold_well_formed bilingual source folds carry a summary and body
  R8 source_fold_coverage    bilingual source folds cover ≥ 80% of body units

The checks live in ``book_agent.export.qa`` and also run after run-based
exports (``services/export_qa.py``), where failures become review issues.

Usage:
    verify_chapter.py --html <path> --figure-prefix N --figure-count K \\
        [--max-h2 24] [--bilingual] [--report-path <json>]

Exit code: 0 on PASS, 1 on FAIL, 2 on bad input.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from book_agent.export.qa import audit_html  # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--html", required=True, type=Path, help="HTML file to verify")
    p.add_argument("--figure-prefix", required=True, help='Chapter figure prefix, e.g. "3" for "Figure 3.1..N"')
    p.add_argument("--figure-count", required=True, type=int, help="Expected figure count (e.g. 11 for ch3)")
    p.add_argument("--max-h2", type=int, default=30, help="Maximum allowed <h2> count (default 30)")
    p.add_argument("--bilingual", action="store_true", help="Treat HTML as bilingual: enable R7/R8 audits")
    p.add_argument("--min-img-coverage", type=int, default=None, help="Override expected <img> count")
    p.add_argument("--report-path", type=Path, default=None, help="Where to write verify_report.json")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    html_path: Path = args.html
    if not html_path.is_file():
        print(f"[verify] HTML not found at {html_path}", file=sys.stderr)
        return 2
    report = audit_html(
        html_path.read_text(encoding="utf-8"),
        figure_prefix=args.figure_prefix,
        figure_count=args.figure_count,
        max_h2=args.max_h2,
        bilingual=args.bilingual,
        min_img_coverage=args.min_img_coverage,
    )
    print(f"[verify] Inspecting {html_path}")
    print(f"[verify] {report.structure['h2']} h2, {report.structure['figcaption']} figcaption, {report.structure['p']} body p")
    print()
    for check in report.checks:
        print(f"  [{'PASS' if check.ok else 'FAIL'}] {check.name}")
        print(f"         {check.detail}")
    print()
    print("=== figcaptions inventory ===")
    for cap in report.figcaptions:
        print(f"  {cap[:120]}")
    payload = {
        "html_path": str(html_path),
        "figure_prefix": args.figure_prefix,
        "figure_count": args.figure_count,
        "bilingual": args.bilingual,
        "all_ok": report.all_ok,
        "structure": report.structure,
        "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in report.checks],
        "figcaptions": report.figcaptions,
    }
    report_path = args.report_path or html_path.with_name(html_path.stem + ".verify_report.json")
    report_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[verify] wrote {report_path}")
    return 0 if report.all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
