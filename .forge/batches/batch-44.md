# Forge Batch 44

Batch: `batch-44`
Mainline: `runtime-self-heal-mainline`
Artifact Status: `frozen`
Frozen at: `2026-04-02 03:14:39 +0800`

## Goal

Project runtime repair blockage from raw runtime state into workflow/API-friendly summaries so
callers can read blockage state directly from run and export contexts without digging through
controller-specific nested recovery objects.

## Linked Feature Ids

- `F007`

## Scope

- Normalize runtime repair blockage summary on run-level runtime v2 state.
- Normalize runtime repair blockage summary on export-facing `runtime_v2_context`.
- Reuse the already verified blockage projection from batch-42 instead of inventing a second schema.
- Preserve existing payload compatibility while adding the clearer summary fields.

## Owned Files

- `/Users/smy/project/book-agent/src/book_agent/services/runtime_repair_blockage.py`
- `/Users/smy/project/book-agent/src/book_agent/services/run_control.py`
- `/Users/smy/project/book-agent/src/book_agent/services/workflows.py`
- `/Users/smy/project/book-agent/tests/test_run_control_api.py`
- `/Users/smy/project/book-agent/tests/test_req_ex_02_export_misrouting_self_heal.py`

## Dependencies

- batch-42 verified blockage projection
- batch-43 verified Forge v2 feature inventory

## Verification

- `.venv/bin/python -m unittest tests.test_run_control_api tests.test_req_ex_02_export_misrouting_self_heal`

## Stop Condition

Stop only after run/export payloads expose blockage summary fields and the representative API tests
prove callers no longer need deep nested lineage inspection for the exposed export-facing runtime
surface.

## Expected Report Path

- `/Users/smy/project/book-agent/.forge/reports/batch-44-report.md`
