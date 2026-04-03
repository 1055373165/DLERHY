# Batch 44 Report

Batch: `batch-44`
Status: `verified`
Artifact Status: `verified from current repo truth`
Verified at: `2026-04-02 04:10:56 +0800`

## Delivered

- Added a normalized `summarize_runtime_repair_blockage(...)` helper so workflow/API callers can
  read one stable blockage summary instead of re-deriving it from nested runtime payloads.
- Surfaced blockage summary fields on run summary `status_detail_json.runtime_v2`.
- Surfaced the same summary on workflow-facing `runtime_v2_context` payloads used by:
  - document summary
  - export action result
  - export detail
- Preserved recovery-specific fields while preferring the closed-loop export recovery payload over
  stale pending repair metadata when both are present.

## Files Changed

- `/Users/smy/project/book-agent/src/book_agent/services/runtime_repair_blockage.py`
- `/Users/smy/project/book-agent/src/book_agent/services/run_control.py`
- `/Users/smy/project/book-agent/src/book_agent/services/workflows.py`
- `/Users/smy/project/book-agent/tests/test_run_control_api.py`
- `/Users/smy/project/book-agent/tests/test_req_ex_02_export_misrouting_self_heal.py`

## Verification

- `.venv/bin/python -m py_compile src/book_agent/services/runtime_repair_blockage.py src/book_agent/services/run_control.py src/book_agent/services/workflows.py tests/test_run_control_api.py tests/test_req_ex_02_export_misrouting_self_heal.py`
  - `passed`
- `.venv/bin/python -m unittest tests.test_run_control_api tests.test_req_ex_02_export_misrouting_self_heal`
  - `Ran 5 tests, OK`

## Features Flipped

- `F007`

## Scope Notes

- Export dashboard record summaries still do not expose the normalized blockage summary; that is
  the next closest dependency-closed continuation and is frozen as `batch-45`.
