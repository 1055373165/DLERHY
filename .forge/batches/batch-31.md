# Forge Batch 31

Batch: `batch-31`
Mainline: `runtime-self-heal-mainline`
Frozen at: `2026-03-31 23:09:35 +0800`

## Goal

Lift packet repair routing parity into the real automatic reconcile path, so `ControllerRunner`
 plus the `REPAIR` lane can execute packet repair through remote/agent-backed routing without manual
 controller entry points.

## Scope

- Add file-backed controller-runner integration coverage for packet runtime defect repair.
- Prove the automatic path can execute through:
  - `http_repair_transport`
  - `configured_command_repair_transport`
  - `agent_backed + python_subprocess_repair_executor`
- Keep repair bounded to packet scope and preserve deterministic work-item lineage.

## Verification

- `.venv/bin/python -m unittest tests.test_packet_runtime_repair tests.test_controller_runner_packet_repair`
- `.venv/bin/python -m py_compile tests/test_packet_runtime_repair.py tests/test_controller_runner_packet_repair.py`
