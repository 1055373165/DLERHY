# Forge Batch 64

Batch: `batch-64`
Mainline: `high-fidelity-document-translation-foundation`
Artifact Status: `in_progress`
Frozen at: `2026-04-03 11:40:59 +0800`

## Goal

Land the first dependency-closed canonical-truth slice:

- rewrite `.forge` truth to the new high-fidelity document translation mainline
- introduce parse revision persistence and canonical IR skeleton
- wire execution provenance back to parse revision and canonical node identity
- keep the repo restartable under Forge v2

## Linked Feature Ids

- `F001`
- `F002`
- `F003`
- `F004`
- `F005`

## Scope

- Rewrite `.forge/spec/SPEC.md`, `.forge/spec/FEATURES.json`, `.forge/DECISIONS.md`, and
  `.forge/STATE.md` to the new mainline.
- Update `snapshot.md`, `progress.txt`, and `docs/mainline-progress.md` so handoff truth matches
  the new batch.
- Add parse revision ORM models and migration.
- Add canonical IR types plus a minimal parse IR service that materializes a sidecar artifact.
- Add a parse IR repository.
- Wire parse revision and canonical node provenance into projected block and sentence metadata if
  that can be done without reopening unrelated parser architecture.
- Add targeted tests and compile checks.

## Owned Files

- `/Users/smy/projects/mygithub/DLEHY/.forge/spec/SPEC.md`
- `/Users/smy/projects/mygithub/DLEHY/.forge/spec/FEATURES.json`
- `/Users/smy/projects/mygithub/DLEHY/.forge/DECISIONS.md`
- `/Users/smy/projects/mygithub/DLEHY/.forge/STATE.md`
- `/Users/smy/projects/mygithub/DLEHY/.forge/log.md`
- `/Users/smy/projects/mygithub/DLEHY/.forge/batches/batch-64.md`
- `/Users/smy/projects/mygithub/DLEHY/.forge/reports/batch-64-report.md`
- `/Users/smy/projects/mygithub/DLEHY/snapshot.md`
- `/Users/smy/projects/mygithub/DLEHY/progress.txt`
- `/Users/smy/projects/mygithub/DLEHY/docs/mainline-progress.md`
- `/Users/smy/projects/mygithub/DLEHY/src/book_agent/domain/enums.py`
- `/Users/smy/projects/mygithub/DLEHY/src/book_agent/domain/models/__init__.py`
- `/Users/smy/projects/mygithub/DLEHY/src/book_agent/domain/models/document.py`
- `/Users/smy/projects/mygithub/DLEHY/src/book_agent/domain/models/parse.py`
- `/Users/smy/projects/mygithub/DLEHY/src/book_agent/domain/structure/canonical_ir.py`
- `/Users/smy/projects/mygithub/DLEHY/src/book_agent/services/parse_ir.py`
- `/Users/smy/projects/mygithub/DLEHY/src/book_agent/services/bootstrap.py`
- `/Users/smy/projects/mygithub/DLEHY/src/book_agent/infra/repositories/__init__.py`
- `/Users/smy/projects/mygithub/DLEHY/src/book_agent/infra/repositories/bootstrap.py`
- `/Users/smy/projects/mygithub/DLEHY/src/book_agent/infra/repositories/parse_ir.py`
- `/Users/smy/projects/mygithub/DLEHY/tests/test_parse_ir.py`
- `/Users/smy/projects/mygithub/DLEHY/alembic/versions/20260403_0016_document_parse_revisions.py`

## Dependencies

- batch-63 verified explicit Forge v2 delegation

## Verification

- `bash .forge/init.sh`
- `.venv/bin/python -m unittest tests.test_parse_ir`
- `.venv/bin/python -m py_compile src/book_agent/domain/models/parse.py src/book_agent/domain/structure/canonical_ir.py src/book_agent/services/parse_ir.py src/book_agent/services/bootstrap.py src/book_agent/infra/repositories/parse_ir.py tests/test_parse_ir.py`

## Stop Condition

Stop only after:

- `.forge` truth and handoff docs point at the new mainline
- parse revision / canonical IR skeleton exists in code
- targeted verification passes
- the run either freezes the next dependency-closed slice or records a real blocker

## Expected Report Path

- `/Users/smy/projects/mygithub/DLEHY/.forge/reports/batch-64-report.md`
