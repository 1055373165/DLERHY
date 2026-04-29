#!/bin/bash
# Run after scripts/translate_chapter3.py --ordinal-lo 326 --ordinal-hi 466
# completes. Sequence:
#   1. Demote misclassified CODE blocks back to PARAGRAPH and re-mark
#      their sentences translatable (mirrors the chapter-3 fix path).
#   2. Translate any orphan sentences that the packet builder skipped.
#   3. Generate the chapter-4 HTML export.
#   4. Run the chapter-4 verifier.
set -euo pipefail
cd "$(dirname "$0")/.."

CHAPTER_ID="${CHAPTER_ID:-de30483c-ec5f-5d3d-a728-69de943db663}"
ORDINAL_LO="${ORDINAL_LO:-326}"
ORDINAL_HI="${ORDINAL_HI:-466}"

echo "==> [1/4] reclassify misclassified CODE blocks"
CHAPTER_ID="$CHAPTER_ID" ORDINAL_LO="$ORDINAL_LO" ORDINAL_HI="$ORDINAL_HI" \
  .venv/bin/python scripts/reclassify_misclassified_code.py

echo
echo "==> [2/4] translate any orphan sentences"
CHAPTER_ID="$CHAPTER_ID" ORDINAL_LO="$ORDINAL_LO" ORDINAL_HI="$ORDINAL_HI" \
  .venv/bin/python scripts/translate_orphan_sentences.py || true

echo
echo "==> [3-4/4] export + verify chapter 4 (closed-loop QA)"
bash scripts/qa_run_chapter.sh ch4
