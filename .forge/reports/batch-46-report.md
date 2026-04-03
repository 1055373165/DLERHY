# Batch 46 Report

Batch: `batch-46`
Status: `verified`
Artifact Status: `verified from current repo truth`
Verified at: `2026-04-02 04:17:21 +0800`

## Delivered

- Generalized latest-run workflow context so non-export bounded recovery can surface through
  document-level APIs.
- Added `latest_run_runtime_v2_context` to document history entries.
- Proved document summary and history now expose normalized blockage summary for review-deadlock
  recovery rather than dropping it behind export-only assumptions.

## Files Changed

- `/Users/smy/project/book-agent/src/book_agent/schemas/workflow.py`
- `/Users/smy/project/book-agent/src/book_agent/services/workflows.py`
- `/Users/smy/project/book-agent/src/book_agent/app/api/routes/documents.py`
- `/Users/smy/project/book-agent/tests/test_api_workflow.py`

## Verification

- `.venv/bin/python -m py_compile src/book_agent/schemas/workflow.py src/book_agent/services/workflows.py src/book_agent/app/api/routes/documents.py tests/test_api_workflow.py`
  - `passed`
- `.venv/bin/python -m unittest tests.test_api_workflow.ApiWorkflowTests.test_document_surfaces_include_latest_run_runtime_v2_context_for_review_recovery tests.test_api_workflow.ApiWorkflowTests.test_document_history_includes_latest_run_stage_and_progress tests.test_api_workflow.ApiWorkflowTests.test_document_exports_dashboard_lists_export_records tests.test_api_workflow.ApiWorkflowTests.test_document_exports_dashboard_supports_filtering_and_pagination`
  - `Ran 4 tests, OK`
- `.venv/bin/python -m unittest tests.test_req_ex_02_export_misrouting_self_heal`
  - `Ran 1 test, OK`
- `bash .forge/init.sh`
  - `Ran 33 tests, OK`

## Features Flipped

- `F010`

## Scope Notes

- The default Forge v2 smoke baseline still does not include these newer workflow parity tests;
  that baseline hardening is frozen as `batch-47`.
