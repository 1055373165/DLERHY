#!/bin/bash
# Closed-loop QA runner: export → verify → merge reports.
#
# Usage:
#   bash scripts/qa_run_chapter.sh ch3
#   bash scripts/qa_run_chapter.sh ch4
#   QA_STRICT=1 bash scripts/qa_run_chapter.sh ch4   # strict mode
#
# Behaviour:
#   * Runs the chapter's export script (which writes
#     ``<chapter>-zh.html`` + ``<chapter>-zh.qa_report.json``).
#   * Runs the chapter's verifier (which writes
#     ``<chapter>-zh.verify_report.json``).
#   * Merges both into ``<chapter>-zh.qa_combined.json``.
#   * Prints a one-line PASS/FAIL summary per chapter.
#   * Returns non-zero if either step failed (export with QA_STRICT, or
#     any verifier check FAIL).
set -uo pipefail
cd "$(dirname "$0")/.."

CHAPTER="${1:-}"
case "$CHAPTER" in
  ch3)
    EXPORT_SCRIPT="scripts/export_chapter3.sh"
    VERIFY_SCRIPT="scripts/verify_ch3_export.py"
    HTML_PATH=".test-tmp/ch3-export-v2/chapter3-zh.html"
    ;;
  ch4)
    EXPORT_SCRIPT="scripts/export_chapter4.sh"
    VERIFY_SCRIPT="scripts/verify_ch4_export.py"
    HTML_PATH=".test-tmp/ch4-export/chapter4-zh.html"
    ;;
  *)
    echo "Usage: $0 {ch3|ch4}" >&2
    exit 2
    ;;
esac

QA_REPORT="${HTML_PATH%.html}.qa_report.json"
VERIFY_REPORT="${HTML_PATH%.html}.verify_report.json"
COMBINED_REPORT="${HTML_PATH%.html}.qa_combined.json"

echo "==> [1/3] export $CHAPTER"
bash "$EXPORT_SCRIPT"
EXPORT_RC=$?

echo
echo "==> [2/3] verify $CHAPTER"
.venv/bin/python "$VERIFY_SCRIPT" "$HTML_PATH"
VERIFY_RC=$?

echo
echo "==> [3/3] merge reports → $COMBINED_REPORT"
.venv/bin/python - "$QA_REPORT" "$VERIFY_REPORT" "$COMBINED_REPORT" <<'PY'
import json
import sys
from pathlib import Path

qa_path, verify_path, out_path = (Path(p) for p in sys.argv[1:4])
qa = json.loads(qa_path.read_text(encoding="utf-8")) if qa_path.is_file() else {}
verify = (
    json.loads(verify_path.read_text(encoding="utf-8"))
    if verify_path.is_file()
    else {}
)

verify_failed = [c for c in verify.get("checks", []) if not c.get("ok")]
combined = {
    "html_path": qa.get("output_path") or verify.get("html_path"),
    "chapter": qa.get("chapter"),
    "ordinal_range": qa.get("ordinal_range"),
    "totals": qa.get("totals"),
    "repair_stats": qa.get("repair_stats"),
    "repair_details": qa.get("repair_details"),
    "image_skip_reasons": qa.get("image_skip_reasons"),
    "export_warnings": qa.get("warnings", []),
    "export_errors": qa.get("errors", []),
    "verify_all_ok": verify.get("all_ok"),
    "verify_structure": verify.get("structure"),
    "verify_checks": verify.get("checks"),
    "verify_failed_checks": verify_failed,
    "figcaptions": verify.get("figcaptions"),
}
out_path.write_text(json.dumps(combined, indent=2, ensure_ascii=False), encoding="utf-8")
# One-liner status
ok = (
    not qa.get("errors")
    and verify.get("all_ok") is True
)
status = "PASS" if ok else "FAIL"
fixes = qa.get("repair_stats") or {}
fired = [f"{k}={v}" for k, v in fixes.items() if v]
print(f"[qa] {status} :: {qa.get('chapter', {}).get('label', 'chapter')} :: fixes[{', '.join(fired) or 'none'}]")
print(f"[qa] failed_checks: {[c['name'] for c in verify_failed]}")
print(f"[qa] export_errors: {qa.get('errors', [])}")
PY
MERGE_RC=$?

# Exit with the worst rc.
RC=0
if [ "$EXPORT_RC" -ne 0 ]; then RC="$EXPORT_RC"; fi
if [ "$VERIFY_RC" -ne 0 ]; then RC="$VERIFY_RC"; fi
if [ "$MERGE_RC" -ne 0 ]; then RC="$MERGE_RC"; fi
exit $RC
