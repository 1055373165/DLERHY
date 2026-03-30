# Forge Decisions

1. Workspace decision
- Stay in the single main repo checkout on `main`.
- Do not create a second live worktree.

2. Mainline decision
- Current mainline is `Runtime self-heal closure`.
- Runtime V2 control-plane work is stable enough; the next focus is incident/repair execution, not more reviewer surface polish.

3. Scope control decision
- Prioritize changes that directly improve reviewer/operator closed-loop efficiency.
- Push non-blocking polish into `docs/optimization-backlog.md`, not the mainline.

4. Current write-set decision
- Prefer staying inside:
  - `src/book_agent/services/runtime_repair_planner.py`
  - `src/book_agent/app/runtime/controllers/incident_controller.py`
  - `src/book_agent/app/runtime/controllers/export_controller.py`
  - `src/book_agent/app/runtime/controllers/review_controller.py`
  - `tests/test_runtime_repair_planner.py`
  - `tests/test_export_controller.py`
  - `tests/test_incident_controller.py`
  - `docs/mainline-progress.md`
- Expand only if blocked by a real dependency.

5. Verification decision
- Every batch must pass:
  - `.venv/bin/python -m unittest tests.test_runtime_repair_planner tests.test_export_controller tests.test_incident_controller tests.test_req_mx_01_review_deadlock_self_heal`
  - `.venv/bin/python -m py_compile src/book_agent/services/runtime_repair_planner.py src/book_agent/app/runtime/controllers/incident_controller.py src/book_agent/app/runtime/controllers/export_controller.py src/book_agent/app/runtime/controllers/review_controller.py tests/test_runtime_repair_planner.py tests/test_export_controller.py tests/test_incident_controller.py`

6. Immediate next-slice decision
- The next dependency-closed slice is `repair dispatch lineage`.
- Goal: keep the new structured repair plan, but turn it into a runtime-owned dispatch/execution lineage that a future repair agent can claim and complete deterministically.
