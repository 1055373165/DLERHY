# Forge Batch 30

Batch: `batch-30`
Mainline: `runtime-self-heal-mainline`
Frozen at: `2026-03-31 23:09:35 +0800`

## Goal

Prove the new `packet_runtime_defect` lane honors the same executor/transport routing matrix already
established for review deadlock and export misrouting.

## Scope

- Extend packet repair planner coverage for executor and transport overrides.
- Prove packet repair can execute through:
  - `agent_backed + python_subprocess_repair_executor`
  - `configured_command_repair_transport`
  - `http_repair_transport`
- Keep replay bounded to packet scope while preserving the deterministic `REPAIR` work-item
  lifecycle.

## Verification

- `.venv/bin/python -m unittest tests.test_runtime_repair_planner tests.test_packet_runtime_repair`
- `.venv/bin/python -m py_compile tests/test_runtime_repair_planner.py tests/test_packet_runtime_repair.py`
