# Forge Batch 54

Batch: `batch-54`
Mainline: `runtime-self-heal-mainline`
Artifact Status: `verified`
Frozen at: `2026-04-02 12:37:33 +0800`
Verified at: `2026-04-02 12:51:32 +0800`

## Goal

Remove FastAPI lifecycle deprecation warnings from the default Forge v2 smoke by replacing
deprecated `on_event` hooks with a lifespan-managed app lifecycle.

## Linked Feature Ids

- `F020`

## Scope

- Replace `startup` / `shutdown` `on_event` hooks in `src/book_agent/app/main.py`.
- Preserve document run executor startup/shutdown behavior.
- Route request-time database bootstrap through the same app-state initialization path as lifespan startup.
- If appropriate, clean up app-held engine lifecycle during shutdown.

## Owned Files

- `/Users/smy/project/book-agent/src/book_agent/app/main.py`
- `/Users/smy/project/book-agent/src/book_agent/app/api/deps.py`
- `/Users/smy/project/book-agent/src/book_agent/app/runtime/document_run_executor.py`
- `/Users/smy/project/book-agent/.forge/spec/FEATURES.json`
- `/Users/smy/project/book-agent/.forge/STATE.md`
- `/Users/smy/project/book-agent/.forge/DECISIONS.md`
- `/Users/smy/project/book-agent/.forge/log.md`

## Dependencies

- batch-52 verified governance smoke hardening
- batch-53 verified post-completion continuation fix

## Verification

- `bash .forge/init.sh`
- `if bash .forge/init.sh 2>&1 | rg -q "on_event is deprecated"; then echo found; else echo not_found; fi`

## Stop Condition

Stop only after the default Forge v2 smoke still passes and no longer emits the FastAPI
`on_event` deprecation warning.

## Expected Report Path

- `/Users/smy/project/book-agent/.forge/reports/batch-54-report.md`
