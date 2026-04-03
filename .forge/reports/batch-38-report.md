# Batch 38 Report

Batch: `batch-38`
Status: `verified`
Completed at: `2026-04-01 01:10:16 +0800`

## Delivered

- Repair dispatch lineage now records explicit follow-up guidance:
  - `next_action`
  - `retryable`
  - `retry_after_seconds`
  - `next_retry_after`
- `manual_escalation_required` now persists `next_action=manual_escalation`.
- `retry_later` now persists `next_action=retry_repair_lane` plus bounded retry-after metadata when
  the remote repair result provides it.
- Packet runtime defect now proves the retry-later path alongside review deadlock, so all three
  bounded repair lanes share decision-aware lineage semantics.

## Verification

- `.venv/bin/python -m unittest tests.test_export_controller.ExportControllerTests.test_recover_export_misrouting_records_manual_escalation_remote_decision tests.test_req_mx_01_review_deadlock_self_heal.ReqMx01ReviewDeadlockSelfHealTests.test_req_mx_01_review_deadlock_self_heal_records_retry_later_remote_decision tests.test_packet_runtime_repair.PacketRuntimeRepairTests.test_packet_runtime_defect_repair_records_retry_later_remote_decision`
  - `Ran 3 tests, OK`
- Expanded baseline
  - `Ran 88 tests, OK`
