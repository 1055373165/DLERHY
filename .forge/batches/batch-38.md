# Forge Batch 38

Batch: `batch-38`
Mainline: `runtime-self-heal-mainline`
Frozen at: `2026-04-01 01:10:16 +0800`

## Goal

Promote decision-aware repair outcomes into explicit runtime lineage so the system records not only
 which non-default decision occurred, but what should happen next and when.

## Scope

- Extend repair dispatch lineage with:
  - `next_action`
  - `retryable`
  - `retry_after_seconds`
  - `next_retry_after`
- Keep decision-aware work-item lifecycle from batch-37 intact.
- Prove retry-later lineage on the packet runtime defect lane in addition to review deadlock.

## Verification

- Add targeted lineage tests for review deadlock and packet runtime defect retry-later paths.
- Re-run the expanded runtime self-heal baseline.
