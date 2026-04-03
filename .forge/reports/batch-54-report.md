# Batch 54 Report

Batch: `batch-54`
Status: `verified`
Artifact Status: `verified from current repo truth`
Verified at: `2026-04-02 12:51:32 +0800`

## Delivered

- Replaced deprecated FastAPI lifecycle hooks with a lifespan-managed app lifecycle.
- Unified eager startup and first-request database bootstrap behind the same app-state
  `ensure_database_state` entrypoint.
- Preserved runtime executor startup behavior while removing the `on_event` deprecation warning
  from the default Forge v2 smoke.

## Files Changed

- `/Users/smy/project/book-agent/src/book_agent/app/main.py`
- `/Users/smy/project/book-agent/src/book_agent/app/api/deps.py`
- `/Users/smy/project/book-agent/src/book_agent/app/runtime/document_run_executor.py`

## Verification

- `.venv/bin/python -m unittest tests.test_req_ex_02_export_misrouting_self_heal`
  - `Ran 1 test, OK`
- `bash .forge/init.sh`
  - `Ran 42 tests, OK`
  - `[forge-v2:init] governance contract validated`
- `if bash .forge/init.sh 2>&1 | rg -q "on_event is deprecated"; then echo found; else echo not_found; fi`
  - `not_found`

## Features Flipped

- `F020`

## Scope Notes

- The lazy-init regression on `REQ-EX-02` was closed by routing request-time session bootstrap
  through the same database initialization path used by the lifespan startup.
