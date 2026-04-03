# Forge Batch 26

Batch: `batch-26`
Mainline: `runtime-self-heal-mainline`
Frozen at: `2026-03-31 20:03:19 +0800`

## Goal

Broaden transport-backed repair execution beyond the built-in subprocess path by adding a
configured command transport and wiring run-level transport override into the review/export repair
planning path, while preserving deterministic REPAIR work-item lifecycle semantics.

## Scope

- Add `configured_command_repair_transport` behind the existing transport registry.
- Add `BOOK_AGENT_RUNTIME_REPAIR_TRANSPORT_COMMAND` as the command source for that transport.
- Let review/export repair planning honor run-level `preferred_repair_transport_hint` and
  `preferred_repair_transport_contract_version`.
- Prove at least one bounded repair lane executes successfully through the configured command
  transport.

## Verification

- `.venv/bin/python -m unittest tests.test_runtime_repair_transport tests.test_runtime_repair_planner tests.test_runtime_repair_executor tests.test_runtime_repair_registry tests.test_export_controller tests.test_incident_controller tests.test_req_mx_01_review_deadlock_self_heal tests.test_req_ex_02_export_misrouting_self_heal tests.test_run_execution.RunExecutionServiceTests.test_ensure_repair_dispatch_work_item_seeds_claimable_repair_lane_once tests.test_run_execution.RunExecutionServiceTests.test_executor_fails_repair_work_item_for_unknown_worker_hint tests.test_run_execution.RunExecutionServiceTests.test_executor_fails_repair_work_item_for_unknown_executor_hint tests.test_run_execution.RunExecutionServiceTests.test_executor_fails_repair_work_item_for_unknown_transport_hint`
- `.venv/bin/python -m py_compile src/book_agent/core/config.py src/book_agent/services/runtime_repair_agent_adapter.py src/book_agent/services/runtime_repair_executor.py src/book_agent/services/runtime_repair_registry.py src/book_agent/services/runtime_repair_transport.py src/book_agent/services/runtime_repair_planner.py src/book_agent/services/runtime_repair_worker.py src/book_agent/services/run_execution.py src/book_agent/app/runtime/controllers/incident_controller.py src/book_agent/app/runtime/controllers/export_controller.py src/book_agent/app/runtime/controllers/review_controller.py src/book_agent/app/runtime/document_run_executor.py src/book_agent/services/workflows.py src/book_agent/tools/runtime_repair_runner.py tests/test_runtime_repair_transport.py tests/test_runtime_repair_executor.py tests/test_runtime_repair_registry.py tests/test_runtime_repair_planner.py tests/test_export_controller.py tests/test_incident_controller.py tests/test_req_mx_01_review_deadlock_self_heal.py tests/test_req_ex_02_export_misrouting_self_heal.py tests/test_run_execution.py`
