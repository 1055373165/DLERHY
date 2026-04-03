# Forge Batch 34

Batch: `batch-34`
Mainline: `runtime-self-heal-mainline`
Frozen at: `2026-03-31 23:16:03 +0800`

## Goal

Introduce the first truly independent remote repair-agent adapter path that consumes the explicit
 request/result contracts without assuming the remote side is just another invocation of the local
 `runtime_repair_runner`.

## Scope

- Add a remote/agent-facing adapter path that is validated purely by contract, not by local worker
  registry assumptions.
- Keep deterministic `REPAIR` work-item lifecycle semantics intact.
- Preserve bounded replay, validation, bundle publication, and audit lineage.

## Verification

- Extend targeted tests around remote executor selection and contract validation.
- Re-run the current 64-test runtime self-heal baseline.
