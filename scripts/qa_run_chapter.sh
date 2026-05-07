#!/bin/bash
# Closed-loop QA runner: export → verify → merge reports.
#
# Usage:
#   bash scripts/qa_run_chapter.sh ch{1..9}             # zh-only mode
#   QA_STRICT=1 bash scripts/qa_run_chapter.sh ch4
#   BILINGUAL=1 bash scripts/qa_run_chapter.sh ch1      # bilingual mode
#
# Reads chapter→DB mapping from scripts/chapters_config.json so adding
# a new chapter requires zero shell-level changes.
set -uo pipefail
cd "$(dirname "$0")/.."

CHAPTER="${1:-}"
CONFIG="scripts/chapters_config.json"
BILINGUAL="${BILINGUAL:-0}"

if [ -z "$CHAPTER" ]; then
  echo "Usage: $0 <ch1|ch2|ch3|ch4|ch5|ch6|ch7|ch8|ch9>" >&2
  exit 2
fi

# Pull config fields with python (jq isn't guaranteed installed).
read_field() {
  .venv/bin/python -c "
import json, sys
cfg = json.load(open('$CONFIG'))
ch = cfg['chapters'].get('$CHAPTER')
if ch is None:
    sys.exit(2)
print(ch.get('$1', ''))
"
}

CHAPTER_ID=$(read_field chapter_id) || { echo "[qa] unknown chapter: $CHAPTER" >&2; exit 2; }
CHAPTER_LABEL=$(read_field chapter_label)
CHAPTER_TITLE=$(read_field chapter_title)
ORDINAL_LO=$(read_field ordinal_lo)
ORDINAL_HI=$(read_field ordinal_hi)
FIGURE_PREFIX=$(read_field figure_prefix)
FIGURE_COUNT=$(read_field figure_count)
OUTPUT_DIR=$(read_field output_dir)
SOURCE_LABEL=$(.venv/bin/python -c "import json; print(json.load(open('$CONFIG'))['source_label'])")
DOCUMENT_ID=$(.venv/bin/python -c "import json; print(json.load(open('$CONFIG'))['document_id'])")

mkdir -p "$OUTPUT_DIR"

if [ "$BILINGUAL" = "1" ]; then
  HTML_PATH="$OUTPUT_DIR/${CHAPTER}-bilingual.html"
  EXPORT_PY="scripts/export_chapter_bilingual.py"
  VERIFY_FLAGS="--bilingual"
else
  HTML_PATH="$OUTPUT_DIR/${CHAPTER}-zh.html"
  EXPORT_PY="scripts/export_chapter_zh_html.py"
  VERIFY_FLAGS=""
fi

QA_REPORT="${HTML_PATH%.html}.qa_report.json"
VERIFY_REPORT="${HTML_PATH%.html}.verify_report.json"
COMBINED_REPORT="${HTML_PATH%.html}.qa_combined.json"

echo "==> [1/3] export $CHAPTER (BILINGUAL=$BILINGUAL)"
DOCUMENT_ID="$DOCUMENT_ID" \
CHAPTER_ID="$CHAPTER_ID" \
ORDINAL_LO="$ORDINAL_LO" \
ORDINAL_HI="$ORDINAL_HI" \
OUTPUT_PATH="$HTML_PATH" \
CHAPTER_LABEL="$CHAPTER_LABEL" \
CHAPTER_TITLE="$CHAPTER_TITLE" \
SOURCE_LABEL="$SOURCE_LABEL" \
.venv/bin/python "$EXPORT_PY"
EXPORT_RC=$?

echo
echo "==> [2/3] verify $CHAPTER"
.venv/bin/python scripts/verify_chapter.py \
  --html "$HTML_PATH" \
  --figure-prefix "$FIGURE_PREFIX" \
  --figure-count "$FIGURE_COUNT" \
  --report-path "$VERIFY_REPORT" \
  $VERIFY_FLAGS
VERIFY_RC=$?

echo
echo "==> [3/3] merge reports → $COMBINED_REPORT"
.venv/bin/python - "$QA_REPORT" "$VERIFY_REPORT" "$COMBINED_REPORT" "$CHAPTER" <<'PY'
import json
import sys
from pathlib import Path

qa_path, verify_path, out_path, chapter = (
    Path(sys.argv[1]),
    Path(sys.argv[2]),
    Path(sys.argv[3]),
    sys.argv[4],
)
qa = json.loads(qa_path.read_text(encoding="utf-8")) if qa_path.is_file() else {}
verify = (
    json.loads(verify_path.read_text(encoding="utf-8"))
    if verify_path.is_file()
    else {}
)

verify_failed = [c for c in verify.get("checks", []) if not c.get("ok")]
combined = {
    "chapter_key": chapter,
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
ok = (not qa.get("errors")) and verify.get("all_ok") is True
status = "PASS" if ok else "FAIL"
fixes = qa.get("repair_stats") or {}
fired = [f"{k}={v}" for k, v in fixes.items() if v]
print(
    f"[qa] {status} :: {qa.get('chapter', {}).get('label', chapter)} :: "
    f"fixes[{', '.join(fired) or 'none'}]"
)
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
