# Batch 28 Report

Status: `verified`
Completed at: `2026-03-31 21:20:16 +0800`

## Delivered

- Added run-level executor override support (`preferred_repair_execution_mode /
  preferred_repair_executor_hint / preferred_repair_executor_contract_version`) to review/export
  repair planning.
- Updated runtime repair planner dispatch contracts so executor routing is explicit and transport
  metadata is only attached when the chosen executor mode is `transport_backed`.
- Proved export misrouting can honor an `agent_backed` subprocess executor override end-to-end.
- Proved review deadlock can honor:
  - `http_repair_transport`
  - `configured_command_repair_transport`
  - `agent_backed` subprocess executor override
- This makes the two currently bounded self-heal lanes symmetrical across executor/transport routing
  semantics instead of leaving the richer routing contract proven on only one lane.

## Verification

- `.venv/bin/python -m unittest tests.test_runtime_repair_transport tests.test_runtime_repair_executor tests.test_runtime_repair_registry tests.test_runtime_repair_planner tests.test_export_controller tests.test_incident_controller tests.test_req_mx_01_review_deadlock_self_heal tests.test_req_ex_02_export_misrouting_self_heal tests.test_run_execution.RunExecutionServiceTests.test_ensure_repair_dispatch_work_item_seeds_claimable_repair_lane_once tests.test_run_execution.RunExecutionServiceTests.test_executor_fails_repair_work_item_for_unknown_worker_hint tests.test_run_execution.RunExecutionServiceTests.test_executor_fails_repair_work_item_for_unknown_executor_hint tests.test_run_execution.RunExecutionServiceTests.test_executor_fails_repair_work_item_for_unknown_transport_hint`
  - `Ran 40 tests, OK`
- `.venv/bin/python -m py_compile src/book_agent/core/config.py src/book_agent/services/runtime_repair_agent_adapter.py src/book_agent/services/runtime_repair_executor.py src/book_agent/services/runtime_repair_registry.py src/book_agent/services/runtime_repair_transport.py src/book_agent/services/runtime_repair_planner.py src/book_agent/services/runtime_repair_worker.py src/book_agent/services/run_execution.py src/book_agent/app/runtime/controllers/incident_controller.py src/book_agent/app/runtime/controllers/export_controller.py src/book_agent/app/runtime/controllers/review_controller.py src/book_agent/app/runtime/document_run_executor.py src/book_agent/services/workflows.py src/book_agent/tools/runtime_repair_runner.py tests/test_runtime_repair_transport.py tests/test_runtime_repair_executor.py tests/test_runtime_repair_registry.py tests/test_runtime_repair_planner.py tests/test_export_controller.py tests/test_incident_controller.py tests/test_req_mx_01_review_deadlock_self_heal.py tests/test_req_ex_02_export_misrouting_self_heal.py tests/test_run_execution.py`
  - `passed`

## Next Gap

- By the fork rule, the next slice should favor adding the next bounded repair lane over adding yet
  another transport variant, because lane coverage is now closer to the autonomous self-heal
  mainline than transport breadth alone.
- Remote executor / transport broadening remains high-impact and should stay on the active path, but
  the next batch should freeze around the next deterministic repair lane that can claim, execute,
  publish, and replay through the existing `REPAIR` lifecycle.
