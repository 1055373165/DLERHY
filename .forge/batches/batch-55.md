# Forge Batch 55

Batch: `batch-55`
Mainline: `runtime-self-heal-mainline`
Artifact Status: `verified`
Frozen at: `2026-04-02 12:51:32 +0800`
Verified at: `2026-04-02 12:51:32 +0800`

## Goal

Remove sqlite `ResourceWarning: unclosed database` noise from the default Forge v2 smoke so
warning probes stay focused on real runtime regressions instead of cleanup leakage.

## Linked Feature Ids

- `F021`

## Scope

- Ensure sqlite backfill helpers close connections and cursors deterministically.
- Ensure representative runtime/controller smoke tests dispose SQLAlchemy engines explicitly.
- Keep the default Forge v2 smoke green while eliminating the resource warning probe hit.

## Owned Files

- `/Users/smy/project/book-agent/src/book_agent/infra/db/legacy_backfill.py`
- `/Users/smy/project/book-agent/src/book_agent/infra/db/sqlite_schema_backfill.py`
- `/Users/smy/project/book-agent/tests/test_incident_controller.py`
- `/Users/smy/project/book-agent/tests/test_export_controller.py`
- `/Users/smy/project/book-agent/tests/test_req_mx_01_review_deadlock_self_heal.py`
- `/Users/smy/project/book-agent/tests/test_packet_runtime_repair.py`
- `/Users/smy/project/book-agent/.forge/spec/FEATURES.json`
- `/Users/smy/project/book-agent/.forge/STATE.md`
- `/Users/smy/project/book-agent/.forge/DECISIONS.md`
- `/Users/smy/project/book-agent/.forge/log.md`

## Dependencies

- batch-54 verified lifecycle warning cleanup

## Verification

- `bash .forge/init.sh`
- `if bash .forge/init.sh 2>&1 | rg -q "ResourceWarning: unclosed database"; then echo found; else echo not_found; fi`

## Stop Condition

Stop only after the default Forge v2 smoke still passes and no longer emits the sqlite unclosed
database resource warning.

## Expected Report Path

- `/Users/smy/project/book-agent/.forge/reports/batch-55-report.md`
