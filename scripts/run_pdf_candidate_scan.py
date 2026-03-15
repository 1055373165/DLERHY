from __future__ import annotations

import argparse
import json
from pathlib import Path

from book_agent.tools.pdf_smoke import discover_pdf_smoke_candidates


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Discover local PDF smoke candidates.")
    parser.add_argument("roots", nargs="+", help="Directories or PDF files to scan")
    parser.add_argument("--non-recursive", action="store_true")
    parser.add_argument("--max-files", type=int, default=None)
    parser.add_argument("--report-path", required=False)
    parser.add_argument("--manifest-path", required=False)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    summary = discover_pdf_smoke_candidates(
        args.roots,
        recursive=not args.non_recursive,
        max_files=args.max_files,
    )
    payload = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.report_path:
        Path(args.report_path).write_text(payload, encoding="utf-8")
    if args.manifest_path:
        Path(args.manifest_path).write_text(
            json.dumps({"cases": summary["recommended_cases"]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
