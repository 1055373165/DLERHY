# Forge Batch 28

Batch: `batch-28`
Mainline: `runtime-self-heal-mainline`
Frozen at: `2026-03-31 21:20:16 +0800`

## Goal

Promote repair routing from transport-only override to full run-level dispatch preference control,
and prove both bounded repair lanes can actually honor that routing through the deterministic
`REPAIR` work-item lifecycle.

## Scope

- Add run-level `preferred_repair_execution_mode / preferred_repair_executor_hint /
  preferred_repair_executor_contract_version` support in review/export repair planning.
- Keep transport override support intact and make planner dispatch contracts omit transport metadata
  when the selected executor mode does not need it.
- Prove export misrouting can honor a run-level executor override (`agent_backed` subprocess executor).
- Prove review deadlock can honor both HTTP and configured-command transport overrides, plus a
  run-level executor override.

## Verification

- `.venv/bin/python -m unittest tests.test_runtime_repair_transport tests.test_runtime_repair_executor tests.test_runtime_repair_registry tests.test_runtime_repair_planner tests.test_export_controller tests.test_incident_controller tests.test_req_mx_01_review_deadlock_self_heal tests.test_req_ex_02_export_misrouting_self_heal tests.test_run_execution.RunExecutionServiceTests.test_ensure_repair_dispatch_work_item_seeds_claimable_repair_lane_once tests.test_run_execution.RunExecutionServiceTests.test_executor_fails_repair_work_item_for_unknown_worker_hint tests.test_run_execution.RunExecutionServiceTests.test_executor_fails_repair_work_item_for_unknown_executor_hint tests.test_run_execution.RunExecutionServiceTests.test_executor_fails_repair_work_item_for_unknown_transport_hint`
- `.venv/bin/python -m py_compile src/book_agent/core/config.py src/book_agent/services/runtime_repair_agent_adapter.py src/book_agent/services/runtime_repair_executor.py src/book_agent/services/runtime_repair_registry.py src/book_agent/services/runtime_repair_transport.py src/book_agent/services/runtime_repair_planner.py src/book_agent/services/runtime_repair_worker.py src/book_agent/services/run_execution.py src/book_agent/app/runtime/controllers/incident_controller.py src/book_agent/app/runtime/controllers/export_controller.py src/book_agent/app/runtime/controllers/review_controller.py src/book_agent/app/runtime/document_run_executor.py src/book_agent/services/workflows.py src/book_agent/tools/runtime_repair_runner.py tests/test_runtime_repair_transport.py tests/test_runtime_repair_executor.py tests/test_runtime_repair_registry.py tests/test_runtime_repair_planner.py tests/test_export_controller.py tests/test_incident_controller.py tests/test_req_mx_01_review_deadlock_self_heal.py tests/test_req_ex_02_export_misrouting_self_heal.py tests/test_run_execution.py`
