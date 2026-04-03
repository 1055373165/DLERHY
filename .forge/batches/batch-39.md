# Forge Batch 39

Batch: `batch-39`
Mainline: `runtime-self-heal-mainline`
Frozen at: `2026-04-01 01:10:16 +0800`

## Goal

Teach repair scheduling and claimability to honor decision-aware backoff and escalation guidance,
 so `retry_later` and manual-escalation outcomes affect what the runtime does next instead of only
 being recorded in dispatch lineage.

## Scope

- Use `retry_after_seconds / next_retry_after` to make retry-later outcomes backoff-aware at the
  REPAIR scheduling / claim layer.
- Keep manual-escalation outcomes non-claimable until explicitly resumed or overridden.
- Preserve deterministic audit lineage and bounded replay semantics.

## Verification

- Add scheduling / claimability tests for repair work-items carrying retry-later and
  manual-escalation guidance.
- Re-run the expanded runtime self-heal baseline.
