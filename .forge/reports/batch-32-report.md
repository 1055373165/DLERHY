# Batch 32 Report

Status: `verified`
Completed at: `2026-03-31 23:09:35 +0800`

## Delivered

- Added an explicit repair request contract in
  [runtime_repair_contract.py](/Users/smy/project/book-agent/src/book_agent/services/runtime_repair_contract.py).
- `REPAIR` work-items now carry full agent-facing request context, including:
  - request contract version
  - full repair dispatch snapshot
  - full repair plan snapshot
  - owned files
  - validation / bundle / replay sections
- Added a matching repair result contract and applied it across:
  - in-process executor output
  - subprocess/HTTP runner output
- This means transports and external repair executors no longer depend on ad hoc dict shape to
  understand what should be repaired or how to report completion.

## Verification

- `.venv/bin/python -m unittest tests.test_runtime_repair_contract tests.test_runtime_repair_transport tests.test_runtime_repair_planner tests.test_runtime_repair_executor tests.test_runtime_repair_registry tests.test_export_controller tests.test_incident_controller tests.test_req_mx_01_review_deadlock_self_heal tests.test_req_ex_02_export_misrouting_self_heal tests.test_packet_runtime_repair tests.test_runtime_lane_health tests.test_controller_runner tests.test_controller_runner_packet_repair tests.test_run_execution.RunExecutionServiceTests.test_ensure_repair_dispatch_work_item_seeds_claimable_repair_lane_once tests.test_run_execution.RunExecutionServiceTests.test_executor_fails_repair_work_item_for_unknown_worker_hint tests.test_run_execution.RunExecutionServiceTests.test_executor_fails_repair_work_item_for_unknown_executor_hint tests.test_run_execution.RunExecutionServiceTests.test_executor_fails_repair_work_item_for_unknown_transport_hint`
  - `Ran 62 tests, OK`
- `.venv/bin/python -m py_compile src/book_agent/services/runtime_repair_contract.py src/book_agent/services/runtime_repair_executor.py src/book_agent/tools/runtime_repair_runner.py tests/test_runtime_repair_contract.py tests/test_runtime_repair_executor.py tests/test_packet_runtime_repair.py`
  - `passed`

## Next Gap

- The next mainline step is to replace the registry-bound local runner assumption with a truly
  remote / agent-facing repair executor that consumes this explicit request/result contract.
