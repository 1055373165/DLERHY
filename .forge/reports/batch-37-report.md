# Batch 37 Report

Batch: `batch-37`
Status: `verified`
Completed at: `2026-04-01 01:10:16 +0800`

## Delivered

- The `REPAIR` lane now interprets non-default remote decisions deterministically instead of
  collapsing them into a generic repair failure.
- `manual_escalation_required` now lands as:
  - `RuntimeRepairManualEscalationRequired`
  - terminal REPAIR work-item failure
  - decision-preserving repair dispatch lineage
- `retry_later` now lands as:
  - `RuntimeRepairRetryLater`
  - retryable REPAIR work-item failure
  - decision-preserving repair dispatch lineage
- Export misrouting proves the manual-escalation path.
- Review deadlock proves the retry-later path.

## Verification

- `.venv/bin/python -m unittest tests.test_export_controller.ExportControllerTests.test_recover_export_misrouting_records_manual_escalation_remote_decision tests.test_req_mx_01_review_deadlock_self_heal.ReqMx01ReviewDeadlockSelfHealTests.test_req_mx_01_review_deadlock_self_heal_records_retry_later_remote_decision`
  - `Ran 2 tests, OK`
- Expanded baseline
  - `Ran 87 tests, OK`
