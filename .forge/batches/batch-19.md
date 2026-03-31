# Forge Batch 19

## Scope

Add a pluggable repair worker registry so runtime self-heal resolves `REPAIR` work-items by
`worker_hint / worker_contract_version` instead of hardwiring a single worker in the executor.

## Goals

- Introduce a deterministic registry/factory for repair workers.
- Route executor-owned `REPAIR` work-items through that registry.
- Keep default in-process mappings for:
  - `review_deadlock_repair_agent`
  - `export_routing_repair_agent`
- Make unsupported worker hints or contract versions fail inside the repair lane lifecycle, not
  outside work-item bookkeeping.

## Acceptance

```bash
.venv/bin/python -m unittest \
  tests.test_runtime_repair_registry \
  tests.test_runtime_repair_planner \
  tests.test_export_controller \
  tests.test_incident_controller \
  tests.test_req_mx_01_review_deadlock_self_heal \
  tests.test_req_ex_02_export_misrouting_self_heal \
  tests.test_run_execution.RunExecutionServiceTests.test_ensure_repair_dispatch_work_item_seeds_claimable_repair_lane_once \
  tests.test_run_execution.RunExecutionServiceTests.test_executor_fails_repair_work_item_for_unknown_worker_hint
```

```bash
.venv/bin/python -m py_compile \
  src/book_agent/services/runtime_repair_registry.py \
  src/book_agent/services/runtime_repair_planner.py \
  src/book_agent/services/runtime_repair_worker.py \
  src/book_agent/services/run_execution.py \
  src/book_agent/app/runtime/controllers/incident_controller.py \
  src/book_agent/app/runtime/controllers/export_controller.py \
  src/book_agent/app/runtime/controllers/review_controller.py \
  src/book_agent/app/runtime/document_run_executor.py \
  src/book_agent/services/workflows.py \
  tests/test_runtime_repair_registry.py \
  tests/test_runtime_repair_planner.py \
  tests/test_export_controller.py \
  tests/test_incident_controller.py \
  tests/test_req_mx_01_review_deadlock_self_heal.py \
  tests/test_req_ex_02_export_misrouting_self_heal.py \
  tests/test_run_execution.py
```
