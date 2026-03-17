from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.services.packet_experiment_diff import compare_experiment_payloads


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare two single-packet experiment JSON artifacts and emit a structured diff."
    )
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output-path", type=Path, required=True)
    parser.add_argument("--baseline-label", default="baseline")
    parser.add_argument("--candidate-label", default="candidate")
    args = parser.parse_args()
    args.baseline = args.baseline.resolve()
    args.candidate = args.candidate.resolve()
    args.output_path = args.output_path.resolve()

    baseline_payload = json.loads(args.baseline.read_text(encoding="utf-8"))
    candidate_payload = json.loads(args.candidate.read_text(encoding="utf-8"))
    artifacts = compare_experiment_payloads(
        baseline_payload,
        candidate_payload,
        baseline_label=args.baseline_label,
        candidate_label=args.candidate_label,
    )
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    args.output_path.write_text(
        json.dumps(artifacts.payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(args.output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
