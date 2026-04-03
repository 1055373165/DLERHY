# Forge Batch 45

Batch: `batch-45`
Mainline: `runtime-self-heal-mainline`
Artifact Status: `frozen`
Frozen at: `2026-04-02 04:10:56 +0800`

## Goal

Project the normalized runtime repair blockage summary onto export dashboard record payloads so
callers can inspect blocked/ready state without fetching export detail first.

## Linked Feature Ids

- `F009`

## Scope

- Add record-level `runtime_v2_context` exposure on export dashboard summaries.
- Reuse the already normalized blockage summary from batch-44 rather than inventing a list-only
  schema.
- Keep export detail and document summary payloads unchanged while adding dashboard parity.
- Prove the dashboard record matches the export detail/runtime summary for the same export.

## Owned Files

- `/Users/smy/project/book-agent/src/book_agent/services/workflows.py`
- `/Users/smy/project/book-agent/src/book_agent/schemas/workflow.py`
- `/Users/smy/project/book-agent/src/book_agent/app/api/routes/documents.py`
- `/Users/smy/project/book-agent/tests/test_req_ex_02_export_misrouting_self_heal.py`

## Dependencies

- batch-44 verified workflow/API blockage summary normalization

## Verification

- `.venv/bin/python -m unittest tests.test_req_ex_02_export_misrouting_self_heal`

## Stop Condition

Stop only after export dashboard record payloads expose `runtime_v2_context` with normalized repair
blockage summary and the representative export self-heal API test proves callers can read the same
blocked/ready truth from dashboard, detail, and document summary surfaces.

## Expected Report Path

- `/Users/smy/project/book-agent/.forge/reports/batch-45-report.md`
