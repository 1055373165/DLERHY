# Batch 55 Report

Batch: `batch-55`
Status: `verified`
Artifact Status: `verified from current repo truth`
Verified at: `2026-04-02 12:51:32 +0800`

## Delivered

- Closed sqlite connection/cursor leakage in legacy backfill and schema-backfill helpers.
- Added explicit SQLAlchemy engine disposal to the representative controller/runtime smoke tests
  that previously left cross-module cleanup noise behind.
- Removed `ResourceWarning: unclosed database` from the default Forge v2 smoke without regressing
  the now-clean lifespan-based startup path.

## Files Changed

- `/Users/smy/project/book-agent/src/book_agent/infra/db/legacy_backfill.py`
- `/Users/smy/project/book-agent/src/book_agent/infra/db/sqlite_schema_backfill.py`
- `/Users/smy/project/book-agent/tests/test_incident_controller.py`
- `/Users/smy/project/book-agent/tests/test_export_controller.py`
- `/Users/smy/project/book-agent/tests/test_req_mx_01_review_deadlock_self_heal.py`
- `/Users/smy/project/book-agent/tests/test_packet_runtime_repair.py`

## Verification

- `bash .forge/init.sh`
  - `Ran 42 tests, OK`
  - `[forge-v2:init] governance contract validated`
- `if bash .forge/init.sh 2>&1 | rg -q "ResourceWarning: unclosed database"; then echo found; else echo not_found; fi`
  - `not_found`
- `if bash .forge/init.sh 2>&1 | rg -q "on_event is deprecated"; then echo found; else echo not_found; fi`
  - `not_found`

## Features Flipped

- `F021`

## Scope Notes

- The continuation scan after batch-54 selected this as the next credible adjacent cleanup because
  the resource warning was still visible in the default smoke path even after the lifecycle
  warning was removed.
