from __future__ import annotations

import argparse
import json
from pathlib import Path

from book_agent.tools.pdf_smoke import (
    build_pdf_smoke_report,
    evaluate_pdf_smoke_expectations,
    load_pdf_smoke_expectations,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a structural smoke check for a local PDF.")
    parser.add_argument("--source-path", required=True)
    parser.add_argument("--report-path", required=False)
    parser.add_argument("--include-parse-summary", action="store_true")
    parser.add_argument("--expectation-path", required=False)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    report = build_pdf_smoke_report(args.source_path, include_parse_summary=args.include_parse_summary)
    failures: list[dict] = []
    if args.expectation_path:
        expectations = load_pdf_smoke_expectations(args.expectation_path)
        failures = evaluate_pdf_smoke_expectations(report, expectations)
        report["expectation_result"] = {
            "status": "passed" if not failures else "failed",
            "failure_count": len(failures),
            "failures": failures,
        }

    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report_path:
        Path(args.report_path).write_text(payload, encoding="utf-8")
    print(payload)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
