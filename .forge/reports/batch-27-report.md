# Batch 27 Report

Status: `verified`
Completed at: `2026-03-31 20:09:47 +0800`

## Delivered

- Added `http_repair_transport` behind the runtime repair transport registry.
- Added HTTP transport configuration for endpoint, timeout, and bearer token.
- Preserved run-level `preferred_repair_transport_hint / preferred_repair_transport_contract_version`
  so bounded repair lanes can opt into the HTTP transport without reopening planner/controller
  semantics.
- Proved export misrouting self-heal can execute end-to-end through the HTTP transport, with the
  remote-transport hint preserved in repair dispatch, repair work-item input, and final repair
  result lineage.

## Verification

- `.venv/bin/python -m unittest tests.test_runtime_repair_transport tests.test_runtime_repair_planner tests.test_runtime_repair_executor tests.test_runtime_repair_registry tests.test_export_controller tests.test_incident_controller tests.test_req_mx_01_review_deadlock_self_heal tests.test_req_ex_02_export_misrouting_self_heal tests.test_run_execution.RunExecutionServiceTests.test_ensure_repair_dispatch_work_item_seeds_claimable_repair_lane_once tests.test_run_execution.RunExecutionServiceTests.test_executor_fails_repair_work_item_for_unknown_worker_hint tests.test_run_execution.RunExecutionServiceTests.test_executor_fails_repair_work_item_for_unknown_executor_hint tests.test_run_execution.RunExecutionServiceTests.test_executor_fails_repair_work_item_for_unknown_transport_hint`
  - `Ran 34 tests, OK`
- `.venv/bin/python -m py_compile src/book_agent/core/config.py src/book_agent/services/runtime_repair_agent_adapter.py src/book_agent/services/runtime_repair_executor.py src/book_agent/services/runtime_repair_registry.py src/book_agent/services/runtime_repair_transport.py src/book_agent/services/runtime_repair_planner.py src/book_agent/services/runtime_repair_worker.py src/book_agent/services/run_execution.py src/book_agent/app/runtime/controllers/incident_controller.py src/book_agent/app/runtime/controllers/export_controller.py src/book_agent/app/runtime/controllers/review_controller.py src/book_agent/app/runtime/document_run_executor.py src/book_agent/services/workflows.py src/book_agent/tools/runtime_repair_runner.py tests/test_runtime_repair_transport.py tests/test_runtime_repair_executor.py tests/test_runtime_repair_registry.py tests/test_runtime_repair_planner.py tests/test_export_controller.py tests/test_incident_controller.py tests/test_req_mx_01_review_deadlock_self_heal.py tests/test_req_ex_02_export_misrouting_self_heal.py tests/test_run_execution.py`
  - `passed`

## Next Gap

- Broaden transport-backed repair execution beyond the current export misrouting + review deadlock
  lanes and beyond the current built-in subprocess / configured-command / HTTP transports.
- Move toward more genuinely remote executor transports or more repair lanes without reopening
  deterministic REPAIR work-item lifecycle semantics.
