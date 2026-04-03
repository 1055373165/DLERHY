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

## 2026-03-31 — Fork Resolution Is Now A Hard Rule

- Trigger:
  - A real run drifted into a secondary direction because Forge had “mainline correction” and
    “framework evolution”, but it did not yet have an explicit mandatory rule for resolving
    competing next directions.
- Root cause:
  - Dynamic mainline adjustment was possible in practice, but it was not encoded as a required
    control step when requirement or implementation forks appeared.
  - That meant a fork could survive as chat guidance instead of immediately mutating the live
    mainline artifacts.
- Protocol change:
  - Added an explicit fork resolution rule to Forge.
  - Forge must now compare competing directions against the active mainline, choose the closest
    one as the next slice, and then score the other direction for mainline impact.
  - If the non-chosen direction has high impact, Forge must rewrite the mainline immediately.
  - If not, Forge must move it to backlog or a later batch.
  - This resolution must be written to decisions, log, and progress artifacts before the next
    dispatch.
- Expected benefit:
  - Prevent silent drift into a secondary lane.
  - Make mainline mutation explicit instead of implicit.
  - Keep Forge adaptive without becoming ambiguous.

## 2026-03-31 — Verified Batch Is Not A Stop Condition

- Trigger:
  - Real runs repeatedly paused after a verified batch even when the next dependency-closed slice
    was already obvious and no real blocker existed.
- Root cause:
  - Forge emphasized verification and progress visibility, but it did not state strongly enough
    that a successful batch summary is not itself a stop signal.
  - That left room for human-style “checkpoint pauses” inside an autonomous loop.
- Protocol change:
  - Added an explicit continue-until-blocked rule.
  - After verification, Forge must either freeze and execute the next slice or record a concrete
    blocker.
  - “Nice place to report progress” is now explicitly invalid as a stop reason.
- Expected benefit:
  - Preserve real autonomous momentum.
  - Reduce unnecessary operator nudging between clean slices.
  - Make the control loop behave more like a real supervisor and less like a chat habit.
