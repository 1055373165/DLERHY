#!/usr/bin/env bash
# Run every tests/test_*.py in its own interpreter, N files at a time.
#
# The suite is a set of unittest modules that each build their own SQLite
# database and, in places, start executor threads; running them in one
# process has produced cross-test interference before, so one process per
# file is the supported way to run the whole suite (locally and in CI).
#
#   scripts/run_tests_per_file.sh            # all files, 4 in parallel
#   PARALLEL=8 scripts/run_tests_per_file.sh
#   scripts/run_tests_per_file.sh tests/test_export_golden.py tests/test_cli.py
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PARALLEL="${PARALLEL:-4}"
PYTHON="${PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
    if [[ -x .venv/bin/python ]]; then PYTHON=".venv/bin/python"; else PYTHON="python"; fi
fi
REPORT_DIR="${REPORT_DIR:-$ROOT_DIR/.test-tmp/reports}"
mkdir -p "$REPORT_DIR"

FILES=()
if [[ $# -gt 0 ]]; then
    FILES=("$@")
else
    # (no mapfile: macOS ships bash 3.2)
    while IFS= read -r file; do FILES+=("$file"); done < <(ls tests/test_*.py | sort)
fi

run_one() {
    local file="$1"
    local name
    name="$(basename "$file" .py)"
    local log="$REPORT_DIR/$name.log"
    if "$PYTHON" -m pytest -q -p no:cacheprovider -o addopts="" "$file" >"$log" 2>&1; then
        printf 'PASS %s  %s\n' "$name" "$(grep -E 'passed|skipped' "$log" | tail -1)"
        return 0
    fi
    printf 'FAIL %s  %s\n' "$name" "$(grep -E 'passed|failed|error' "$log" | tail -1)"
    return 1
}
export -f run_one
export PYTHON REPORT_DIR

printf '%s\n' "${FILES[@]}" | xargs -P "$PARALLEL" -I{} bash -c 'run_one "$@"' _ {} | tee "$REPORT_DIR/summary.txt"

failures=$(grep -c '^FAIL' "$REPORT_DIR/summary.txt" || true)
echo "---"
echo "files: ${#FILES[@]}  failed: $failures  (logs in $REPORT_DIR)"
if [[ "$failures" != "0" ]]; then
    grep '^FAIL' "$REPORT_DIR/summary.txt"
    exit 1
fi
