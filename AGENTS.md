# AGENTS.md

English→Chinese book and web-page translation product (FastAPI + SQLAlchemy + PostgreSQL backend in `src/book_agent`, React/TS frontend in `frontend/`).

**Start with [`docs/HANDOFF.md`](docs/HANDOFF.md)**. It has the product decisions, the code map, current state, the open work and the pitfalls. The pilot quality gate design is in `docs/agent-upgrade/08-pilot-quality-gate.md`. `docs/architecture/*` predates 2026-09-20; where it disagrees with the code or with HANDOFF.md, the code wins.

## Always
- Run services with `./book-agent start|stop|restart|status|logs`: backend on 8999, frontend on 4173, PostgreSQL on 55432. Before restarting, check that no run is `queued` or `running`.
- Run tests one file per process: `uv run --extra pdf python -m pytest -q -p no:warnings tests/<file>.py`, or the whole suite with `scripts/run_tests_per_file.sh`.
  - Always pass `--extra pdf`; without it, uv removes playwright.
  - Changes to enums, migrations or agent tools also need `BOOK_AGENT_RUN_PG_TESTS=1` (SQLite hides Postgres-only failures).
- Any PDF-parsing change also runs `BOOK_AGENT_RUN_CORPUS_TESTS=1 tests/test_parse_health_corpus.py` (the PDFs live on `/Volumes/touch`), the PDF test files and the goldens.
  - Regenerate goldens (`BOOK_AGENT_UPDATE_GOLDEN=1`) only for intended output changes, and explain each change.
- Frontend: `cd frontend && npx tsc --noEmit -p . && npx vitest run`.
- Before calling translation output done, open the reading copy (`artifacts/exports/<doc>/merged-document.html`), screenshot it and look at it. A green QA report is not proof.

## Never
- Commit PDFs, EPUBs or other book content (copyright).
- `git add -A` / `git add .`, `git stash`, or rewriting git history. Commit per logical step on a branch, staging explicit paths.
- Push to `origin`, or delete user documents or databases, without asking the user. Experiment on a copy DB: `CREATE DATABASE … TEMPLATE book_agent`.
- Change code from inside a running translation (systemic defects go through a branch and review), or let the frontend derive run or stage state itself (read `GET /v1/documents/{id}/overview`).
- Choose a payment provider, quote model prices from memory, or remove the 书库 tab.

User-facing text is Chinese; code, identifiers and commit messages are English.
