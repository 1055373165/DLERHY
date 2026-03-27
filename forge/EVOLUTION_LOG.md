# Codex Forge — Evolution Log

## 2026-03-27 — Initial Cut

- Origin:
  - Derived from real-world use of the old `autopilot/` framework.
- What was retained:
  - requirement lock
  - ADR freeze
  - batch contracts
  - delivery reports
  - master verification
  - liveness recovery
- What was removed or simplified:
  - excessive phase ritual
  - spike-by-default behavior
  - redundant state surfaces
  - passive waiting during stale execution
- Expected benefit:
  - faster execution
  - lower control-plane overhead
  - clearer truth model
  - easier framework self-improvement

## 2026-03-27 — Cutover Deferred Until Active Round Completes

- Trigger:
  - The repo now has a new `forge/` framework, but the active Runtime V2 round is still running
    inside an isolated workspace on its own `autopilot/` copy.
- Decision:
  - Do not force a mid-round migration.
  - Let the current round finish on its historical framework.
  - Cut the repo's default execution flow over to Forge only after that round closes cleanly.
- Why:
  - Mid-round framework swaps are high-risk and would blur auditability.
  - The active round already has valid state, batches, reports, and recovery logic.
- Follow-up:
  - Use [MIGRATION_PLAN.md](/Users/smy/project/book-agent/forge/MIGRATION_PLAN.md) as the cutover checklist.

## 2026-03-27 — Worker Completion Must Trigger Harvest, Not Operator Nudging

- Trigger:
  - In the active isolated Runtime V2 round, a recovered worker finished and the UI showed it as
    done / awaiting instruction, but master did not immediately harvest the result.
  - The run paused even though the delivery report later appeared.
- Root cause:
  - The framework had liveness recovery for stale execution, but it did not explicitly require
    master to register active worker metadata and immediately harvest completion signals.
- Protocol change:
  - Added a dispatch-and-harvest contract to Forge.
  - Required `.forge/STATE.md` to record the authoritative batch contract, expected report path,
    and active worker slot.
  - Clarified that worker completion is only a trigger; report/file truth remains authoritative.
- Expected benefit:
  - Prevent batches from pausing after background workers finish.
  - Let Forge keep moving without operator nudging.
  - Make batch completion handling deterministic and automation-friendly.

## 2026-03-27 — Single Worktree Default For Personal Development

- Trigger:
  - Running a separate backport worktree added coordination overhead without providing real conflict
    isolation for a solo developer workflow.
- Protocol change:
  - Forge now defaults to a single live repo checkout and a single live worktree.
  - Additional worktrees are exception-only and require an explicit user request.
  - Normal feature delivery should happen directly inside the active repo checkout.
- Expected benefit:
  - Less branch and path juggling.
  - Faster execution and verification loops.
  - A clearer single source of local truth during autonomous development.
