from __future__ import annotations

import argparse
import json
from pathlib import Path

from book_agent.tools.pdf_smoke import load_pdf_smoke_manifest, run_pdf_smoke_corpus


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a manifest-driven PDF smoke corpus.")
    parser.add_argument("--manifest-path", required=True)
    parser.add_argument("--output-dir", required=False)
    parser.add_argument("--report-path", required=False)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    cases = load_pdf_smoke_manifest(args.manifest_path)
    summary = run_pdf_smoke_corpus(cases, output_dir=args.output_dir)
    payload = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.report_path:
        Path(args.report_path).write_text(payload, encoding="utf-8")
    print(payload)
    return 1 if summary["failed_case_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
