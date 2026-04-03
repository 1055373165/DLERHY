#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

python3 -m json.tool .forge/spec/FEATURES.json >/dev/null

LATEST_FEATURE_ID="$(
  python3 - <<'PY'
import json
from pathlib import Path

features = json.loads(Path(".forge/spec/FEATURES.json").read_text())
passing = [feature["id"] for feature in features if feature.get("passes")]
if not passing:
    raise SystemExit("no passing feature ids found")
print(sorted(passing, key=lambda value: int(value[1:]))[-1])
PY
)"

LATEST_REPORT_NAME="$(
  python3 - <<'PY'
import re
from pathlib import Path

reports = []
for path in Path(".forge/reports").glob("batch-*-report.md"):
    match = re.fullmatch(r"batch-(\d+)-report\.md", path.name)
    if match:
        reports.append((int(match.group(1)), path.name))
if not reports:
    raise SystemExit("no batch reports found")
print(sorted(reports)[-1][1])
PY
)"

LATEST_BATCH_NAME="${LATEST_REPORT_NAME%-report.md}"

CURRENT_STEP="$(
  python3 - <<'PY'
from pathlib import Path

for line in Path(".forge/STATE.md").read_text().splitlines():
    if line.startswith("current_step: "):
        print(line.split(": ", 1)[1].strip())
        break
else:
    raise SystemExit("missing current_step in STATE.md")
PY
)"

ACTIVE_BATCH="$(
  python3 - <<'PY'
from pathlib import Path

for line in Path(".forge/STATE.md").read_text().splitlines():
    if line.startswith("active_batch: "):
        print(line.split(": ", 1)[1].strip())
        break
else:
    raise SystemExit("missing active_batch in STATE.md")
PY
)"

AUTHORITATIVE_BATCH_CONTRACT="$(
  python3 - <<'PY'
from pathlib import Path

for line in Path(".forge/STATE.md").read_text().splitlines():
    if line.startswith("authoritative_batch_contract: "):
        print(line.split(": ", 1)[1].strip())
        break
else:
    raise SystemExit("missing authoritative_batch_contract in STATE.md")
PY
)"

EXPECTED_REPORT_PATH="$(
  python3 - <<'PY'
from pathlib import Path

for line in Path(".forge/STATE.md").read_text().splitlines():
    if line.startswith("expected_report_path: "):
        print(line.split(": ", 1)[1].strip())
        break
else:
    raise SystemExit("missing expected_report_path in STATE.md")
PY
)"

rg -n \
  "mainline_required|mainline_adjacent|out_of_band|stop-legality|no next dependency-closed slice remains|credible next change request|continuation scan" \
  forge-v2/SKILL.md \
  forge-v2/references/branch-intake-governance.md \
  forge-v2/references/long-running-hardening.md \
  forge-v2/references/requirement-amplification.md \
  .forge/spec/SPEC.md \
  .forge/DECISIONS.md \
  >/dev/null

rg -n \
  "validate_init_warning_hygiene\\.sh|validating smoke warning hygiene|SMOKE_LOG=.*mktemp" \
  .forge/init.sh \
  >/dev/null

rg -n \
  "FORBIDDEN_WARNING_PATTERN='on_event is deprecated\\|ResourceWarning: unclosed database'|forbidden warning output detected in default smoke|smoke warning hygiene validated" \
  .forge/scripts/validate_init_warning_hygiene.sh \
  >/dev/null

test -f ".forge/batches/${LATEST_BATCH_NAME}.md"
test -f ".forge/reports/${LATEST_REPORT_NAME}"

rg -F "current_step: ${CURRENT_STEP}" .forge/STATE.md >/dev/null
rg -F "active_batch: ${ACTIVE_BATCH}" .forge/STATE.md >/dev/null

if [ "$ACTIVE_BATCH" = "none" ]; then
  [ "$AUTHORITATIVE_BATCH_CONTRACT" = "none" ]
  [ "$EXPECTED_REPORT_PATH" = "none" ]
else
  [ "$AUTHORITATIVE_BATCH_CONTRACT" = ".forge/batches/${ACTIVE_BATCH}.md" ]
  [ "$EXPECTED_REPORT_PATH" = ".forge/reports/${ACTIVE_BATCH}-report.md" ]
  test -f "$AUTHORITATIVE_BATCH_CONTRACT"
fi

rg -n \
  "bash \\.forge/init\\.sh|change_request|warning hygiene|smoke warning hygiene validated|governance contract validated|${LATEST_BATCH_NAME}|${LATEST_REPORT_NAME}|${LATEST_FEATURE_ID}|current_step: ${CURRENT_STEP}|active_batch: ${ACTIVE_BATCH}|authoritative_batch_contract: ${AUTHORITATIVE_BATCH_CONTRACT}|expected_report_path: ${EXPECTED_REPORT_PATH}" \
  snapshot.md \
  progress.txt \
  docs/mainline-progress.md \
  >/dev/null

echo "[forge-v2:init] governance contract validated"
