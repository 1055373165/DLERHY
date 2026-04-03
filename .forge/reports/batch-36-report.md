# Batch 36 Report

Batch: `batch-36`
Status: `verified`
Completed at: `2026-04-01 00:44:15 +0800`

## Delivered

- Local and remote bounded repair payloads now carry:
  - `repair_agent_decision`
  - `repair_agent_decision_reason`
- Current decision is explicitly `publish_bundle_and_replay`, which makes future remote-agent branch
  handling a contract extension instead of an implicit side effect.
- Runtime repair worker now rejects unsupported decisions deterministically.

## Verification

- `.venv/bin/python -m unittest tests.test_runtime_repair_remote_agent tests.test_runtime_repair_contract_runner tests.test_export_controller.ExportControllerTests.test_recover_export_misrouting_can_execute_through_http_contract_transport_override`
  - `Ran 6 tests, OK`
- Expanded baseline
  - `Ran 85 tests, OK`
