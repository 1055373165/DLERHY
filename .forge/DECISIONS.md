# Forge Decisions

1. Workspace decision
- Stay in the single main repo checkout on `main`.
- Do not create a second live worktree.

2. Mainline decision
- Current mainline is `Runtime self-heal closure`.
- Runtime V2 control-plane work is stable enough; the next focus is incident/repair execution, not more reviewer surface polish.

3. Scope control decision
- Prioritize changes that directly improve runtime self-heal closure.
- Push non-blocking polish into `docs/optimization-backlog.md`, not the mainline.

4. Current write-set decision
- Prefer staying inside:
  - `src/book_agent/services/runtime_repair_planner.py`
  - `src/book_agent/services/runtime_repair_worker.py`
  - `src/book_agent/services/runtime_repair_registry.py`
  - `src/book_agent/app/runtime/controllers/incident_controller.py`
  - `src/book_agent/app/runtime/controllers/export_controller.py`
  - `src/book_agent/app/runtime/controllers/review_controller.py`
  - `src/book_agent/services/run_execution.py`
  - `src/book_agent/infra/repositories/run_control.py`
  - `src/book_agent/app/runtime/document_run_executor.py`
  - `src/book_agent/services/workflows.py`
  - `tests/test_runtime_repair_planner.py`
  - `tests/test_runtime_repair_registry.py`
  - `tests/test_export_controller.py`
  - `tests/test_incident_controller.py`
  - `tests/test_run_execution.py`
  - `tests/test_req_mx_01_review_deadlock_self_heal.py`
  - `tests/test_req_ex_02_export_misrouting_self_heal.py`
  - `docs/mainline-progress.md`
  - `progress.txt`
- Expand only if blocked by a real dependency.

5. Verification decision
- Every batch must pass:
  - `.venv/bin/python -m unittest tests.test_runtime_repair_registry tests.test_runtime_repair_planner tests.test_export_controller tests.test_incident_controller tests.test_req_mx_01_review_deadlock_self_heal tests.test_req_ex_02_export_misrouting_self_heal tests.test_run_execution.RunExecutionServiceTests.test_ensure_repair_dispatch_work_item_seeds_claimable_repair_lane_once tests.test_run_execution.RunExecutionServiceTests.test_executor_fails_repair_work_item_for_unknown_worker_hint`
  - `.venv/bin/python -m py_compile src/book_agent/services/runtime_repair_registry.py src/book_agent/services/runtime_repair_planner.py src/book_agent/services/runtime_repair_worker.py src/book_agent/services/run_execution.py src/book_agent/app/runtime/controllers/incident_controller.py src/book_agent/app/runtime/controllers/export_controller.py src/book_agent/app/runtime/controllers/review_controller.py src/book_agent/app/runtime/document_run_executor.py src/book_agent/services/workflows.py tests/test_runtime_repair_registry.py tests/test_runtime_repair_planner.py tests/test_export_controller.py tests/test_incident_controller.py tests/test_req_mx_01_review_deadlock_self_heal.py tests/test_req_ex_02_export_misrouting_self_heal.py tests/test_run_execution.py`

6. Immediate next-slice decision
- The next dependency-closed slice is `distinct repair-agent implementations`.
- Goal: keep the new registry deterministic, then let `worker_hint` / `worker_contract_version` route to genuinely separate repair workers or agent adapters instead of always resolving to the same in-process implementation.
