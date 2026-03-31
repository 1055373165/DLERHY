# Forge State

last_update_time: 2026-03-31 15:43:13 +0800
mode: resume
current_step: batch-19_verified
active_batch: batch-19
authoritative_batch_contract: .forge/batches/batch-19.md
expected_report_path: .forge/reports/batch-19-report.md

active_worker_slot:
- worker_id: none
- worker_nickname: none
- model: none
- reasoning: none
- dispatch_time: none
- last_harvest_check: none

completed_items:
- Runtime V2 round-2 control-plane closure is considered complete and reusable.
- Memory Governance proposal-first / review-commit / explicit override loop is complete enough for product use.
- Chapter Workbench already supports focused / flow / release-ready operator modes.
- release-ready lane now exposes pressure suggestion, stay-or-switch judgment, confidence, and drift across queue rail / Operator Lens / Session Digest.
- Forge batch-1 is verified complete: release-ready lane now also exposes a higher-level lane health summary across queue rail / Operator Lens / Session Digest.
- Forge batch-2 is verified complete: release-ready lane health now drives a stronger top-line routing cue across queue rail / Operator Lens / Session Digest.
- Forge batch-3 is verified complete: decisive release-ready views now collapse supporting signals by default and keep route-first decisions on top.
- Forge batch-4 is verified complete: route-first release-ready decisions now sit closer to lane entry, so reviewers can decide whether to stay in-lane before scanning mid-page detail.
- Forge batch-5 is verified complete: route-first release-ready decisions now sit inside the Operator Lens entry layer, so reviewers can act at lane entry instead of after reading mid-page cards.
- Forge batch-6 is verified complete: route-first release-ready decisions now sit in a pre-entry cue before the lane is actually selected, reducing wrong-lane scans.
- Forge batch-7 is verified complete: route-first release-ready decisions now influence Operator Lens choice itself, so reviewers can pick the right lane before entering subqueue-level handling.
- Forge batch-8 is verified complete: release-ready route-first guidance now appears in Active Scope / Session Digest summaries, so reviewers can judge lane worthiness before reaching Operator Lens controls.
- Forge batch-9 is verified complete: release-ready route-first guidance is now actionable at the summary layer, so reviewers can enter the right lane directly from Active Scope / Session Digest.
- Forge batch-10 is verified complete: summary-level release-ready guidance is now compressed into a clearer lane go/no-go read, so reviewers can read “continue / switch / stop” faster before diving into lane detail.
- Forge batch-11 is verified complete: the lane go/no-go card now reads as a shorter “status + reason + action” decision, so reviewers can trust the top-level route without reading as much supporting copy.
- Forge batch-12 is verified complete: the top-level lane go/no-go card now replaces multiple summary chips with a single compact summary line, so queue/session decisions rely on fewer visual layers.
- Forge batch-13 is verified complete: when the top-level lane go/no-go card exists, duplicate Lens/Session entry suggestion cards now collapse away, so queue/session level route trust depends on one primary cue instead of several similar cards.
- Forge batch-14 is verified complete: runtime incidents now generate structured repair plans that capture owned files, validation, bundle rollout, and replay scope, so self-heal execution can move from hardcoded controller actions toward runtime-owned repair dispatch.
- Forge batch-15 is verified complete: runtime patch proposals now seed repair dispatch lineage and the review/export self-heal flows claim, execute, validate, and publish through that lineage, so repair execution is no longer implied by controller code alone.
- Forge batch-16 is verified complete: runtime repair dispatch is now bound to a claimable `REPAIR` work-item lane, so proposal/incident repair lineage is no longer only JSON metadata and can be deterministically picked up by a future repair agent.
- Forge batch-17 is verified complete: repair execution now runs through the executor-owned `REPAIR` lane end-to-end, so work-items only succeed after validate/publish/finalize, and both REQ-MX-01 and REQ-EX-02 prove the scheduled repair can be claimed, executed, and replayed after commit.
- Forge batch-18 is verified complete: repair execution is now delegated through an explicit `RuntimeRepairWorker` plus work-item contract metadata, so the executor only orchestrates the lane and the next slice can focus on independent worker selection instead of untangling inline repair code.
- Forge batch-19 is verified complete: repair work-items are now resolved through an explicit worker registry keyed by `worker_hint / worker_contract_version`, and unknown contracts fail deterministically through the repair lane instead of exploding outside the work-item lifecycle.

failed_items:
- none recorded in the current handoff state

working_tree_scope:
- /Users/smy/project/book-agent/progress.txt
- /Users/smy/project/book-agent/.forge/STATE.md
- /Users/smy/project/book-agent/.forge/DECISIONS.md
- /Users/smy/project/book-agent/.forge/batches/batch-1.md
- /Users/smy/project/book-agent/.forge/batches/batch-2.md
- /Users/smy/project/book-agent/.forge/batches/batch-3.md
- /Users/smy/project/book-agent/.forge/batches/batch-4.md
- /Users/smy/project/book-agent/.forge/batches/batch-5.md
- /Users/smy/project/book-agent/.forge/batches/batch-6.md
- /Users/smy/project/book-agent/.forge/batches/batch-7.md
- /Users/smy/project/book-agent/.forge/batches/batch-8.md
- /Users/smy/project/book-agent/.forge/batches/batch-9.md
- /Users/smy/project/book-agent/.forge/batches/batch-10.md
- /Users/smy/project/book-agent/.forge/batches/batch-11.md
- /Users/smy/project/book-agent/.forge/batches/batch-12.md
- /Users/smy/project/book-agent/.forge/batches/batch-13.md
- /Users/smy/project/book-agent/.forge/batches/batch-14.md
- /Users/smy/project/book-agent/.forge/batches/batch-15.md
- /Users/smy/project/book-agent/.forge/batches/batch-16.md
- /Users/smy/project/book-agent/.forge/batches/batch-17.md
- /Users/smy/project/book-agent/.forge/batches/batch-18.md
- /Users/smy/project/book-agent/.forge/batches/batch-19.md
- /Users/smy/project/book-agent/.forge/log.md
- /Users/smy/project/book-agent/.forge/reports/batch-1-report.md
- /Users/smy/project/book-agent/.forge/reports/batch-2-report.md
- /Users/smy/project/book-agent/.forge/reports/batch-3-report.md
- /Users/smy/project/book-agent/.forge/reports/batch-4-report.md
- /Users/smy/project/book-agent/.forge/reports/batch-5-report.md
- /Users/smy/project/book-agent/.forge/reports/batch-6-report.md
- /Users/smy/project/book-agent/.forge/reports/batch-7-report.md
- /Users/smy/project/book-agent/.forge/reports/batch-8-report.md
- /Users/smy/project/book-agent/.forge/reports/batch-9-report.md
- /Users/smy/project/book-agent/.forge/reports/batch-10-report.md
- /Users/smy/project/book-agent/.forge/reports/batch-11-report.md
- /Users/smy/project/book-agent/.forge/reports/batch-12-report.md
- /Users/smy/project/book-agent/.forge/reports/batch-13-report.md
- /Users/smy/project/book-agent/.forge/reports/batch-14-report.md
- /Users/smy/project/book-agent/.forge/reports/batch-15-report.md
- /Users/smy/project/book-agent/.forge/reports/batch-16-report.md
- /Users/smy/project/book-agent/.forge/reports/batch-17-report.md
- /Users/smy/project/book-agent/.forge/reports/batch-18-report.md
- /Users/smy/project/book-agent/.forge/reports/batch-19-report.md
- /Users/smy/project/book-agent/docs/mainline-progress.md
- /Users/smy/project/book-agent/src/book_agent/services/runtime_repair_planner.py
- /Users/smy/project/book-agent/src/book_agent/services/runtime_repair_worker.py
- /Users/smy/project/book-agent/src/book_agent/services/runtime_repair_registry.py
- /Users/smy/project/book-agent/src/book_agent/services/run_execution.py
- /Users/smy/project/book-agent/src/book_agent/app/runtime/controllers/incident_controller.py
- /Users/smy/project/book-agent/src/book_agent/app/runtime/controllers/export_controller.py
- /Users/smy/project/book-agent/src/book_agent/app/runtime/controllers/review_controller.py
- /Users/smy/project/book-agent/src/book_agent/app/runtime/document_run_executor.py
- /Users/smy/project/book-agent/src/book_agent/services/workflows.py
- /Users/smy/project/book-agent/tests/test_runtime_repair_planner.py
- /Users/smy/project/book-agent/tests/test_runtime_repair_registry.py
- /Users/smy/project/book-agent/tests/test_export_controller.py
- /Users/smy/project/book-agent/tests/test_incident_controller.py
- /Users/smy/project/book-agent/tests/test_req_ex_02_export_misrouting_self_heal.py
- /Users/smy/project/book-agent/tests/test_run_execution.py
- /Users/smy/project/book-agent/tests/test_req_mx_01_review_deadlock_self_heal.py

last_verified_test_baseline:
- command: .venv/bin/python -m unittest tests.test_runtime_repair_registry tests.test_runtime_repair_planner tests.test_export_controller tests.test_incident_controller tests.test_req_mx_01_review_deadlock_self_heal tests.test_req_ex_02_export_misrouting_self_heal tests.test_run_execution.RunExecutionServiceTests.test_ensure_repair_dispatch_work_item_seeds_claimable_repair_lane_once tests.test_run_execution.RunExecutionServiceTests.test_executor_fails_repair_work_item_for_unknown_worker_hint
  result: Ran 15 tests, OK
- command: .venv/bin/python -m py_compile src/book_agent/services/runtime_repair_registry.py src/book_agent/services/runtime_repair_planner.py src/book_agent/services/runtime_repair_worker.py src/book_agent/services/run_execution.py src/book_agent/app/runtime/controllers/incident_controller.py src/book_agent/app/runtime/controllers/export_controller.py src/book_agent/app/runtime/controllers/review_controller.py src/book_agent/app/runtime/document_run_executor.py src/book_agent/services/workflows.py tests/test_runtime_repair_registry.py tests/test_runtime_repair_planner.py tests/test_export_controller.py tests/test_incident_controller.py tests/test_req_mx_01_review_deadlock_self_heal.py tests/test_req_ex_02_export_misrouting_self_heal.py tests/test_run_execution.py
  result: passed

handoff_source:
- /Users/smy/project/book-agent/progress.txt

next_mainline_focus:
- Move the new repair worker registry toward truly distinct repair-agent implementations, so runtime self-heal can hand `REPAIR` work-items to independent workers instead of resolving every hint to the same in-process implementation.
