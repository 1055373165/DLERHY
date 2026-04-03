# Batch 45 Report

Batch: `batch-45`
Status: `verified`
Artifact Status: `verified from current repo truth`
Verified at: `2026-04-02 04:14:27 +0800`

## Delivered

- Added `runtime_v2_context` to export dashboard record payloads.
- Reused the already normalized blockage summary from batch-44 instead of creating a second
  list-specific schema.
- Proved export dashboard callers can read the same blocked/ready truth as export detail and
  document summary without fetching detail first.

## Files Changed

- `/Users/smy/project/book-agent/src/book_agent/schemas/workflow.py`
- `/Users/smy/project/book-agent/src/book_agent/services/workflows.py`
- `/Users/smy/project/book-agent/src/book_agent/app/api/routes/documents.py`
- `/Users/smy/project/book-agent/tests/test_req_ex_02_export_misrouting_self_heal.py`

## Verification

- `.venv/bin/python -m py_compile src/book_agent/schemas/workflow.py src/book_agent/services/workflows.py src/book_agent/app/api/routes/documents.py tests/test_req_ex_02_export_misrouting_self_heal.py`
  - `passed`
- `.venv/bin/python -m unittest tests.test_req_ex_02_export_misrouting_self_heal tests.test_api_workflow.ApiWorkflowTests.test_document_exports_dashboard_lists_export_records tests.test_api_workflow.ApiWorkflowTests.test_document_exports_dashboard_supports_filtering_and_pagination`
  - `Ran 3 tests, OK`
- `bash .forge/init.sh`
  - `Ran 33 tests, OK`

## Features Flipped

- `F009`

## Scope Notes

- Latest-run workflow context is still export-centric; document-level workflow surfaces may still
  drop review-deadlock or packet-runtime-defect bounded recovery. That is frozen as `batch-46`.
