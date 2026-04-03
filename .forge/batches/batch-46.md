# Forge Batch 46

Batch: `batch-46`
Mainline: `runtime-self-heal-mainline`
Artifact Status: `frozen`
Frozen at: `2026-04-02 04:14:27 +0800`

## Goal

Project latest-run bounded-lane blockage summary into document-level workflow surfaces so document
summary and history callers can read review deadlock, packet runtime defect, or export recovery
truth without drilling into export-only lineage assumptions.

## Linked Feature Ids

- `F010`

## Scope

- Generalize `_runtime_v2_context_for_run(...)` so it can surface non-export bounded recovery
  payloads.
- Add latest-run runtime v2 context to document history entries.
- Keep document summary aligned with the same normalized blockage summary and source precedence.
- Prove review-deadlock or packet-runtime-defect blockage summary is visible from document-level API
  surfaces.

## Owned Files

- `/Users/smy/project/book-agent/src/book_agent/services/workflows.py`
- `/Users/smy/project/book-agent/src/book_agent/schemas/workflow.py`
- `/Users/smy/project/book-agent/src/book_agent/app/api/routes/documents.py`
- `/Users/smy/project/book-agent/tests/test_api_workflow.py`

## Dependencies

- batch-44 verified runtime repair blockage normalization
- batch-45 verified export dashboard parity

## Verification

- `.venv/bin/python -m unittest tests.test_api_workflow`

## Stop Condition

Stop only after document summary and/or history payloads expose normalized latest-run blockage
summary for a non-export bounded lane, and representative API tests prove callers can read that
truth without export-specific lineage assumptions.

## Expected Report Path

- `/Users/smy/project/book-agent/.forge/reports/batch-46-report.md`
