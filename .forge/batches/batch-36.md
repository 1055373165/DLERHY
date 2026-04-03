# Forge Batch 36

Batch: `batch-36`
Mainline: `runtime-self-heal-mainline`
Frozen at: `2026-04-01 00:44:15 +0800`

## Goal

Introduce explicit remote/local repair decision semantics so repair results say not only who ran the
 fix, but what decision the repair agent made.

## Scope

- Add explicit `repair_agent_decision` / `repair_agent_decision_reason` to bounded repair payloads.
- Keep current runtime behavior deterministic by only accepting `publish_bundle_and_replay`.
- Preserve bounded publish/replay lifecycle semantics.

## Verification

- Extend contract-runner and lane assertions for decision semantics.
- Re-run the expanded runtime self-heal baseline.
