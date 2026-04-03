# Forge Batch 32

Batch: `batch-32`
Mainline: `runtime-self-heal-mainline`
Frozen at: `2026-03-31 23:09:35 +0800`

## Goal

Promote repair work-item input/output into explicit external-agent contracts, so transports and
 independent repair executors no longer rely on ad hoc dict shape when handling repair requests.

## Scope

- Add an explicit repair request contract for `REPAIR` work-item input.
- Add an explicit repair result contract for runner/executor output.
- Thread those contracts through run execution, incident dispatch seeding, transports, and the
  subprocess runner.
- Prove the new contracts reach work-item input and transport payloads.

## Verification

- `.venv/bin/python -m unittest tests.test_runtime_repair_contract tests.test_runtime_repair_transport tests.test_runtime_repair_planner tests.test_runtime_repair_executor tests.test_runtime_repair_registry tests.test_export_controller tests.test_incident_controller tests.test_req_mx_01_review_deadlock_self_heal tests.test_req_ex_02_export_misrouting_self_heal tests.test_packet_runtime_repair tests.test_runtime_lane_health tests.test_controller_runner tests.test_controller_runner_packet_repair tests.test_run_execution.RunExecutionServiceTests.test_ensure_repair_dispatch_work_item_seeds_claimable_repair_lane_once tests.test_run_execution.RunExecutionServiceTests.test_executor_fails_repair_work_item_for_unknown_worker_hint tests.test_run_execution.RunExecutionServiceTests.test_executor_fails_repair_work_item_for_unknown_executor_hint tests.test_run_execution.RunExecutionServiceTests.test_executor_fails_repair_work_item_for_unknown_transport_hint`
- `.venv/bin/python -m py_compile src/book_agent/services/runtime_repair_contract.py src/book_agent/services/runtime_repair_executor.py src/book_agent/services/runtime_repair_transport.py src/book_agent/services/runtime_repair_planner.py src/book_agent/services/runtime_repair_worker.py src/book_agent/services/runtime_repair_registry.py src/book_agent/services/runtime_repair_agent_adapter.py src/book_agent/services/run_execution.py src/book_agent/app/runtime/controllers/incident_controller.py src/book_agent/app/runtime/controllers/packet_controller.py src/book_agent/app/runtime/controller_runner.py src/book_agent/tools/runtime_repair_runner.py tests/test_runtime_repair_contract.py tests/test_runtime_repair_transport.py tests/test_runtime_repair_planner.py tests/test_runtime_repair_executor.py tests/test_runtime_repair_registry.py tests/test_packet_runtime_repair.py tests/test_controller_runner.py tests/test_controller_runner_packet_repair.py tests/test_run_execution.py`
