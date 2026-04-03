# Forge Batch 40

Batch: `batch-40`
Mainline: `runtime-self-heal-mainline`
Artifact Status: `reconstructed from verified repo truth`
Frozen at: `2026-04-02 02:40:00 +0800`

## Goal

Add an explicit resume boundary for blocked repair dispatch so manual-escalation and retry-later
 outcomes can be resumed deliberately, with route overrides and refreshed request-contract input,
 instead of only remaining recorded in lineage.

## Scope

- Add `IncidentController.resume_repair_dispatch(...)`.
- Allow deterministic resume lineage for blocked repair dispatch.
- Allow bounded dispatch overrides during resume:
  - execution mode
  - worker / executor / transport hint + contract version
  - validation command
  - bundle revision name
- Refresh the `REPAIR` work-item request bundle before the resumed item re-enters the lane.

## Verification

- Add targeted controller / dispatch tests for:
  - resume lineage
  - route override persistence
  - request-contract refresh on resume
- Keep the repair scheduling semantics from batch-39 intact.
