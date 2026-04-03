# Forge Batch 37

Batch: `batch-37`
Mainline: `runtime-self-heal-mainline`
Frozen at: `2026-04-01 00:44:15 +0800`

## Goal

Teach the `REPAIR` lane to interpret explicit remote repair decisions beyond
 `publish_bundle_and_replay`, starting with deterministic support for manual escalation / retry
 decisions instead of treating every non-default decision as a generic failure.

## Scope

- Keep the explicit decision contract introduced in batch-36.
- Add bounded runtime handling for remote decisions such as:
  - `manual_escalation_required`
  - `retry_later`
- Preserve deterministic work-item lifecycle, audit lineage, and bounded replay semantics.

## Verification

- Add executor / worker / lane tests for non-default remote repair decisions.
- Re-run the expanded runtime self-heal baseline.
