#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ ! -x ".venv/bin/python" ]; then
  echo "[forge-v2:init] missing .venv/bin/python" >&2
  exit 1
fi

echo "[forge-v2:init] workspace: $ROOT_DIR"
echo "[forge-v2:init] running runtime self-heal smoke baseline"

SMOKE_LOG="$(mktemp "${TMPDIR:-/tmp}/forge-v2-init-smoke.XXXXXX")"
trap 'rm -f "$SMOKE_LOG"' EXIT

.venv/bin/python -m unittest \
  tests.test_incident_controller \
  tests.test_export_controller \
  tests.test_req_mx_01_review_deadlock_self_heal \
  tests.test_packet_runtime_repair \
  tests.test_run_control_api \
  tests.test_req_ex_02_export_misrouting_self_heal \
  tests.test_api_workflow.ApiWorkflowTests.test_document_surfaces_include_latest_run_runtime_v2_context_for_review_recovery \
  tests.test_api_workflow.ApiWorkflowTests.test_document_surfaces_include_latest_run_runtime_v2_context_for_packet_recovery \
  tests.test_api_workflow.ApiWorkflowTests.test_document_exports_dashboard_lists_export_records \
  tests.test_api_workflow.ApiWorkflowTests.test_document_exports_dashboard_supports_filtering_and_pagination \
  2>&1 | tee "$SMOKE_LOG"

echo "[forge-v2:init] validating smoke warning hygiene"
bash .forge/scripts/validate_init_warning_hygiene.sh "$SMOKE_LOG"

echo "[forge-v2:init] validating forge-v2 governance contract"
bash .forge/scripts/validate_forge_v2_governance.sh
