# Book Agent Runtime Self-Heal Snapshot

Last Updated: 2026-04-02 15:22 +0800
Workspace: `/Users/smy/project/book-agent`
Branch: `main`
Worktree Policy: single live worktree only

## 1. Snapshot Purpose

This snapshot is the authoritative human-readable handoff for the current development context.
It is written so that:

- the next programmer can recover the full active context without re-reading chat history
- `forge-v2` can take over from file truth and continue the runtime self-heal mainline
- the project can resume from the current verified `.forge/` state without creating a second live
  ledger

This document is intentionally high-granularity. It favors restartability over brevity.

## 2. What The Real Mainline Is

The current mainline is **not** frontend polish, reviewer workbench polish, or more release-ready UI.

The real mainline is:

`Runtime V2 control plane already landed` +
`runtime-owned self-heal closure` +
`repair agent contract / dispatch / execution / replay closure`

The north star is:

- when translation/review/export/packet runtime failures occur, the system should
  - classify the failure deterministically
  - generate a bounded repair plan
  - create repair dispatch lineage
  - bind that dispatch to a claimable `REPAIR` work item
  - route that work item through worker / executor / transport contracts
  - validate / publish / replay only after repair success
  - honor non-happy-path repair decisions such as `retry_later` and
    `manual_escalation_required`
  - resume failed repair tasks explicitly and safely

The user’s stated product goal is stronger than “good operator UX”:

- the system should become a genuinely self-deciding agent
- when something breaks, it should try to fix itself
- after repair, it should continue the failed translation flow
- the user should not need to manually babysit every failure

## 3. What Is Already Considered Stable

### 3.1 Runtime V2 / control-plane baseline

The following are already considered landed enough to build on:

- `ReviewSession` runtime scaffold
- packet/review lane health
- `RecoveryMatrix`
- review deadlock incidentization + bounded replay
- `chapter_hold` escalation boundary
- bundle rollback governance + stable revision rebinding
- `REQ-MX-01 ~ REQ-MX-04`
- `REQ-EX-02`

### 3.2 Supporting product surfaces

The following are usable, but no longer the active mainline:

- Memory Governance proposal-first loop
- explicit proposal approve / reject
- reviewer/operator workbench
- chapter queue / operator lens / release-ready lane UX

These are now secondary unless they directly block runtime self-heal.

## 4. Current Runtime Self-Heal Architecture

The active runtime self-heal stack now looks like this:

1. incident detected
2. structured repair plan created
3. repair dispatch created and stored on proposal / incident lineage
4. claimable `REPAIR` work item seeded
5. repair worker selected by worker registry
6. repair adapter selected by adapter contract
7. repair executor selected by executor contract
8. repair transport selected by transport contract
9. repair executed
10. validation / publish / replay only after success
11. non-default repair decisions interpreted deterministically
12. scheduler / claim layer honors retry/backoff/manual escalation semantics
13. explicit resume path can requeue blocked repair work items with overrides

### 4.1 Core files on the mainline

These files now define the runtime self-heal path:

- `/Users/smy/project/book-agent/src/book_agent/services/runtime_repair_planner.py`
- `/Users/smy/project/book-agent/src/book_agent/services/runtime_repair_contract.py`
- `/Users/smy/project/book-agent/src/book_agent/services/runtime_repair_remote_agent.py`
- `/Users/smy/project/book-agent/src/book_agent/services/runtime_repair_worker.py`
- `/Users/smy/project/book-agent/src/book_agent/services/runtime_repair_registry.py`
- `/Users/smy/project/book-agent/src/book_agent/services/runtime_repair_agent_adapter.py`
- `/Users/smy/project/book-agent/src/book_agent/services/runtime_repair_executor.py`
- `/Users/smy/project/book-agent/src/book_agent/services/runtime_repair_transport.py`
- `/Users/smy/project/book-agent/src/book_agent/services/run_execution.py`
- `/Users/smy/project/book-agent/src/book_agent/infra/repositories/run_control.py`
- `/Users/smy/project/book-agent/src/book_agent/app/runtime/controllers/incident_controller.py`
- `/Users/smy/project/book-agent/src/book_agent/app/runtime/controllers/export_controller.py`
- `/Users/smy/project/book-agent/src/book_agent/app/runtime/controllers/review_controller.py`
- `/Users/smy/project/book-agent/src/book_agent/app/runtime/controllers/packet_controller.py`
- `/Users/smy/project/book-agent/src/book_agent/app/runtime/controller_runner.py`
- `/Users/smy/project/book-agent/src/book_agent/app/runtime/document_run_executor.py`
- `/Users/smy/project/book-agent/src/book_agent/tools/runtime_repair_runner.py`
- `/Users/smy/project/book-agent/src/book_agent/tools/runtime_repair_contract_runner.py`

### 4.2 What the contracts now cover

The runtime already has explicit contracts for:

- repair request payload
- repair result payload
- worker hint / contract version
- adapter selection
- executor selection
- transport selection
- remote execution provenance
- repair agent decision
- decision-aware follow-up lineage

### 4.3 Bounded repair lanes already proven

Three bounded repair lanes are already on the mainline:

- `review_deadlock`
- `export_misrouting`
- `packet_runtime_defect`

These lanes already prove combinations of:

- in-process execution
- agent-backed subprocess executor
- configured-command transport
- HTTP transport
- remote contract-backed path
- controller-runner automatic repair path

## 5. Most Recent Completed Slices

The newest meaningful slices are now `batch-43` through `batch-62`.

### 5.1 Batch-43

Theme: **Forge v2 bootstrap from verified repo truth**

What landed:

- `.forge/spec/SPEC.md`
- `.forge/spec/FEATURES.json`
- `.forge/init.sh`
- explicit feature inventory with `F007` left as the next failing capability at that time

### 5.2 Batch-44

Theme: **workflow/API-friendly blockage summary**

What landed:

- normalized blockage summary on run summary `status_detail_json.runtime_v2`
- normalized blockage summary on document summary `runtime_v2_context`
- normalized blockage summary on export action result and export detail `runtime_v2_context`
- export-facing summary now prefers closed-loop recovery truth over stale pending repair metadata

### 5.3 Batch-45

Theme: **export dashboard blockage parity**

What landed:

- export dashboard records now expose `runtime_v2_context`
- callers can read export blockage summary from dashboard and no longer need a detail fetch just to
  know whether the repair lane is blocked or ready

### 5.4 Batch-46

Theme: **latest-run workflow parity beyond export-only context**

What landed:

- `_runtime_v2_context_for_run(...)` now projects bounded-lane recovery beyond export-only context
- document summary now surfaces review-deadlock blockage summary when that is the latest bounded
  recovery
- document history now exposes `latest_run_runtime_v2_context`

### 5.5 Batch-47

Theme: **Forge v2 smoke hardening**

What landed:

- `.forge/init.sh` widened from the older 33-test controller/runtime smoke to a 41-test smoke
  baseline
- the widened smoke now covers:
  - controller/runtime self-heal baseline
  - run summary blockage parity
  - export self-heal blockage parity
  - export dashboard blockage parity
  - document summary/history blockage parity

### 5.6 Batch-48

Theme: **human-facing handoff reconciliation**

What landed:

- `snapshot.md`, `progress.txt`, and `docs/mainline-progress.md` were brought back in sync with
  verified `.forge` truth

### 5.7 Batch-49

Theme: **packet latest-run workflow parity coverage**

What landed:

- explicit API regression coverage for packet latest-run workflow parity on document summary and
  history

### 5.8 Batch-50

Theme: **default Forge v2 smoke parity completion**

What landed:

- `.forge/init.sh` now also covers packet latest-run workflow parity
- default Forge v2 smoke baseline is now `Ran 42 tests, OK`

### 5.9 Batch-51

Theme: **Forge v2 governance hardening**

What landed:

- explicit branch-intake classes:
  - `mainline_required`
  - `mainline_adjacent`
  - `out_of_band`
- explicit single-ledger transaction rule for accepted / deferred / rejected branches
- explicit stop-legality audit rule
- current `.forge` truth now records that no next dependency-closed slice remains in the present
  `FEATURES.json` inventory

### 5.10 Batch-52

Theme: **Forge v2 governance smoke hardening**

What landed:

- `.forge/init.sh` now also validates governance drift after the 42-test runtime smoke
- `.forge/scripts/validate_forge_v2_governance.sh` checks:
  - `FEATURES.json` validity
  - governance-contract language across skill/reference/spec/decisions
  - handoff-doc truth for the current change-request entry point

### 5.11 Batch-53

Theme: **post-completion autonomous continuation**

What landed:

- fully green inventory is now a checkpoint, not permission to stop
- active takeover must run a continuation scan for the next credible `change_request`
- that new rule immediately selected lifecycle warning cleanup as the next adjacent slice

### 5.12 Batch-54

Theme: **FastAPI lifecycle warning cleanup**

What landed:

- deprecated FastAPI `on_event` hooks were replaced with a lifespan-managed lifecycle
- request-time DB bootstrap now reuses the same initialization path as lifespan startup
- default Forge v2 smoke no longer emits the lifecycle deprecation warning

### 5.13 Batch-55

Theme: **sqlite cleanup warning cleanup**

What landed:

- sqlite backfill helpers now close connections/cursors deterministically
- representative controller/runtime smoke tests now dispose engines explicitly
- default Forge v2 smoke no longer emits `ResourceWarning: unclosed database`

### 5.14 Batch-56

Theme: **default warning-hygiene gate**

What landed:

- `.forge/init.sh` now captures smoke output and validates warning hygiene itself
- the default smoke now hard-fails if the old FastAPI lifecycle or sqlite unclosed-database
  warnings return
- warning hygiene is no longer enforced only by ad hoc report probes

### 5.15 Batch-57

Theme: **warning-gate governance protection**

What landed:

- governance validation now proves `.forge/init.sh` still wires in the warning-hygiene gate
- governance validation now proves the warning-hygiene validator still blocks the two known
  warning classes
- warning hygiene is now protected both by runtime execution and by governance drift checks

### 5.16 Batch-58

Theme: **latest checkpoint governance protection**

What landed:

- governance validation now proves the latest verified batch/report artifacts still exist on disk
- governance validation now proves `.forge/STATE.md` still carries the explicit
  `mainline_complete` checkpoint fields
- the active checkpoint is now protected against silent on-disk drift as well as prose drift

### 5.17 Batch-59

Theme: **dynamic latest-checkpoint governance**

What landed:

- governance validation now derives the latest passing feature id from `FEATURES.json`
- governance validation now derives the latest batch/report checkpoint from `.forge/reports`
- future verified checkpoints no longer require another validator self-patch just to track newest
  marker ids

### 5.18 Batch-60

Theme: **dynamic smoke-contract governance**

What landed:

- governance validation no longer depends on a fixed `Ran 42 tests` marker
- it now validates the default `bash .forge/init.sh` contract plus the two post-smoke validation
  outputs instead
- future smoke widenings no longer require another validator self-patch just to track test-count
  growth

### 5.19 Batch-61

Theme: **dynamic current-state-shape governance**

What landed:

- governance validation now derives `current_step` and `active_batch` from `STATE.md`
- it no longer assumes the current checkpoint must be `mainline_complete / active_batch none`
- the next active change-request batch is now already covered by the default validator form

### 5.20 Batch-62

Theme: **full STATE checkpoint tuple governance**

What landed:

- governance validation now derives `authoritative_batch_contract` and `expected_report_path`
  from `STATE.md`
- it now validates those checkpoint pointers semantically against `active_batch`
- the current checkpoint tuple is now fully validated rather than partially validated

## 6. Current Verified Baseline

The last expanded runtime self-heal baseline that still matters passed:

```bash
.venv/bin/python -m unittest \
  tests.test_runtime_repair_contract \
  tests.test_runtime_repair_contract_runner \
  tests.test_runtime_repair_remote_agent \
  tests.test_runtime_repair_transport \
  tests.test_runtime_repair_planner \
  tests.test_runtime_repair_executor \
  tests.test_runtime_repair_registry \
  tests.test_export_controller \
  tests.test_incident_controller \
  tests.test_req_mx_01_review_deadlock_self_heal \
  tests.test_req_ex_02_export_misrouting_self_heal \
  tests.test_packet_runtime_repair \
  tests.test_runtime_lane_health \
  tests.test_controller_runner \
  tests.test_controller_runner_packet_repair \
  tests.test_run_execution.RunExecutionServiceTests.test_ensure_repair_dispatch_work_item_seeds_claimable_repair_lane_once \
  tests.test_run_execution.RunExecutionServiceTests.test_executor_fails_repair_work_item_for_unknown_worker_hint \
  tests.test_run_execution.RunExecutionServiceTests.test_executor_fails_repair_work_item_for_unknown_executor_hint \
  tests.test_run_execution.RunExecutionServiceTests.test_executor_fails_repair_work_item_for_unknown_transport_hint \
  tests.test_run_execution.RunExecutionServiceTests.test_retry_later_repair_work_item_respects_retry_after_before_reclaim \
  tests.test_run_execution.RunExecutionServiceTests.test_manual_escalation_repair_work_item_requires_explicit_resume_before_reclaim \
  tests.test_run_execution.RunExecutionServiceTests.test_manual_escalation_repair_item_blocks_reseed_until_resumed
```

Result:

- `Ran 95 tests`
- `OK`

The default Forge v2 resume smoke that now passed is:

```bash
bash .forge/init.sh
```

Result:

- `Ran 42 tests`
- `OK`
- `governance contract validated`
- no FastAPI lifecycle deprecation warning
- no sqlite unclosed-database resource warning
- `smoke warning hygiene validated`

Representative newer workflow/API regressions that passed include:

```bash
.venv/bin/python -m unittest \
  tests.test_run_control_api \
  tests.test_req_ex_02_export_misrouting_self_heal \
  tests.test_api_workflow.ApiWorkflowTests.test_document_surfaces_include_latest_run_runtime_v2_context_for_review_recovery \
  tests.test_api_workflow.ApiWorkflowTests.test_document_surfaces_include_latest_run_runtime_v2_context_for_packet_recovery \
  tests.test_api_workflow.ApiWorkflowTests.test_document_exports_dashboard_lists_export_records \
  tests.test_api_workflow.ApiWorkflowTests.test_document_exports_dashboard_supports_filtering_and_pagination
```

## 7. Current File-Truth / State-Truth Situation

The authoritative resume truth is:

1. `/Users/smy/project/book-agent/.forge/STATE.md`
2. `/Users/smy/project/book-agent/.forge/DECISIONS.md`
3. `/Users/smy/project/book-agent/.forge/log.md`
4. `/Users/smy/project/book-agent/.forge/spec/FEATURES.json`
5. `/Users/smy/project/book-agent/progress.txt`
6. `/Users/smy/project/book-agent/docs/mainline-progress.md`
7. repo truth in `src/` + `tests/`

Current facts:

- `.forge/reports/` contains verified reports through `batch-63`
- `.forge/STATE.md` is currently at:
  - `current_step: mainline_complete`
  - `active_batch: none`
- `F001` through `F029` are passing in `.forge/spec/FEATURES.json`
- no next dependency-closed slice remains in the current inventory

The working tree is still **not clean**. Do not reset or clean it blindly.

## 8. What The Repo Is Waiting On Now

The repo is no longer waiting on product recovery semantics from the 39-42 era. Those are closed.

The repo is not waiting on another frozen product slice right now.

What that means:

- the current `FEATURES.json` inventory is fully green
- Forge v2 stop legality is now explicit rather than implied
- any further work should enter as a `change_request` against the existing `.forge` ledger

## 9. How A New Programmer Should Resume

Treat the next session as `resume`, not `new_run`.

Recommended order:

1. `pwd`
2. read `/Users/smy/project/book-agent/snapshot.md`
3. read `/Users/smy/project/book-agent/.forge/STATE.md`
4. read the recent tail of `/Users/smy/project/book-agent/.forge/log.md`
5. read `/Users/smy/project/book-agent/.forge/DECISIONS.md`
6. read `/Users/smy/project/book-agent/.forge/spec/FEATURES.json`
7. inspect `git status --short`
8. run `bash .forge/init.sh`
9. if deeper confidence is needed, run the 95-test expanded baseline
10. read the newest verified reports starting from `.forge/reports/batch-63-report.md`
11. if new work is needed, start it as a `change_request` rather than pretending an old frozen
    batch still exists

If the smoke baseline fails, the session becomes `recovery`, not new feature work.

## 10. How To Hand Control To Forge v2

### 10.1 Current reality

`forge-v2` exists as a skill/reference set, but the repo still uses `.forge/` as the only live
ledger:

- `/Users/smy/project/book-agent/forge-v2/SKILL.md`
- `/Users/smy/project/book-agent/forge-v2/references/requirement-amplification.md`
- `/Users/smy/project/book-agent/forge-v2/references/long-running-hardening.md`
- `/Users/smy/project/book-agent/forge-v2/references/runtime-artifacts.md`

That means:

- `forge-v2` should resume by reading existing `.forge/`
- it should not create a second live ledger
- it should use `bash .forge/init.sh` as the default smoke gate before continuing

### 10.2 Recommended forge-v2 takeover procedure

1. classify the session as `resume`
2. read this snapshot first
3. read `.forge/STATE.md`, `.forge/log.md`, `.forge/DECISIONS.md`, `.forge/spec/FEATURES.json`
4. run `bash .forge/init.sh`
5. if smoke is green, continue from the active truth or start a `change_request` if the inventory is already complete
6. keep `.forge/` as the only live state directory
7. verify every finished batch and update `.forge` truth before freezing the next slice
8. continue until a real blocker exists

### 10.3 forge-v2 should preserve these hard rules

- single main checkout only
- no second live worktree
- verified batch is not a stop condition
- fork resolution must be decided by mainline proximity + impact
- non-blocking polish goes to backlog, not mainline
- chat is never authoritative state

### 10.4 Remaining work is already delegated

There is no open product batch right now.

The remaining development surface is delegated like this:

- Forge v2 owns future work through `change_request` intake
- the existing `.forge/` ledger stays the only live truth
- the next session should not invent an active batch before branch intake classifies a real change
  request
- if a credible `change_request` appears, Forge v2 should rewrite file truth first, then freeze the
  next dependency-closed batch

## 11. Known Do-Not-Break Constraints

- Do not reopen the Chapter Workbench as the mainline unless it directly blocks self-heal work.
- Do not add more route variants just because they are technically easy.
- Do not create a second truth ledger beside `.forge/`.
- Do not revert unrelated uncommitted work.
- Do not mark repair outcomes successful before validate / publish / finalize / replay semantics are
  actually satisfied.
- Do not let non-default repair decisions collapse back into generic failure.

## 12. Minimal “Where We Are Right Now” Summary

- The project is in the `runtime self-heal closure` phase.
- The runtime already supports structured repair plan -> dispatch -> `REPAIR` work item ->
  worker / adapter / executor / transport routing -> validation / publish / replay.
- Three bounded repair lanes are already proven:
  `review_deadlock`, `export_misrouting`, `packet_runtime_defect`.
- Non-default repair decisions (`retry_later`, `manual_escalation_required`) are already honored in
  scheduling / claim / explicit resume flow.
- The newest completed slices are `batch-43` through `batch-63`.
- Default Forge v2 smoke is now `bash .forge/init.sh` and currently passes with `Ran 42 tests, OK`.
- Default Forge v2 smoke no longer emits the old FastAPI lifecycle or sqlite unclosed-database warnings.
- Default Forge v2 smoke now also treats those warning classes as hard failures.
- The current inventory is complete; any new work should enter through `change_request`.
- Remaining development work is explicitly delegated to Forge v2 `change_request` intake.
- `forge-v2` should keep continuing from the existing `.forge/` ledger, not create a second runtime
  directory.
