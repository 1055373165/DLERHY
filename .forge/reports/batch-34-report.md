# Batch 34 Report

Batch: `batch-34`
Status: `verified`
Completed at: `2026-04-01 00:44:15 +0800`

## Delivered

- Added the first truly independent remote repair-agent contract path via
  `runtime_repair_contract_runner`.
- Added contract-backed executors and transports so remote repair no longer assumes the far side is
  `runtime_repair_runner`.
- Enabled auto-default remote HTTP contract routing when a global repair endpoint is configured for
  export, review, and packet bounded repair lanes.

## Verification

- `.venv/bin/python -m unittest tests.test_runtime_repair_contract tests.test_runtime_repair_contract_runner tests.test_runtime_repair_remote_agent tests.test_runtime_repair_transport tests.test_runtime_repair_planner tests.test_runtime_repair_executor tests.test_runtime_repair_registry tests.test_export_controller tests.test_incident_controller tests.test_req_mx_01_review_deadlock_self_heal tests.test_req_ex_02_export_misrouting_self_heal tests.test_packet_runtime_repair tests.test_runtime_lane_health tests.test_controller_runner tests.test_controller_runner_packet_repair tests.test_run_execution.RunExecutionServiceTests.test_ensure_repair_dispatch_work_item_seeds_claimable_repair_lane_once tests.test_run_execution.RunExecutionServiceTests.test_executor_fails_repair_work_item_for_unknown_worker_hint tests.test_run_execution.RunExecutionServiceTests.test_executor_fails_repair_work_item_for_unknown_executor_hint tests.test_run_execution.RunExecutionServiceTests.test_executor_fails_repair_work_item_for_unknown_transport_hint`
  - `Ran 85 tests, OK`

## Notes

- Remaining warnings are SQLite `ResourceWarning` / FastAPI `on_event` deprecations, not runtime
  self-heal regressions.
