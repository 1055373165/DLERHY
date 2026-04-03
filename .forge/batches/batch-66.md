# Forge Batch 66

Batch: `batch-66`
Mainline: `high-fidelity-document-translation-foundation`
Artifact Status: `in_progress`
Frozen at: `2026-04-03 12:05:01 +0800`

## Goal

Persist PDF page/zone extraction intent into canonical truth so mixed-risk PDF decisions stop
living only in document-level routing and block metadata.

## Linked Feature Ids

- `F007`

## Scope

- Extend canonical IR / parse IR persistence so PDF documents record page-level or zone-level
  extraction intent such as `native_text`, `ocr_overlay`, or `hybrid_merge`.
- Preserve the risk reasons or routing evidence that led to each persisted extraction mode.
- Keep the slice sidecar-first; do not attempt full page/line/span normalization.
- Cover the slice with targeted tests that prove mixed-risk or academic-paper PDF metadata is
  persisted into canonical truth rather than only implied by current routing heuristics.

## Owned Files

- `/Users/smy/projects/mygithub/DLEHY/.forge/spec/FEATURES.json`
- `/Users/smy/projects/mygithub/DLEHY/.forge/DECISIONS.md`
- `/Users/smy/projects/mygithub/DLEHY/.forge/STATE.md`
- `/Users/smy/projects/mygithub/DLEHY/.forge/log.md`
- `/Users/smy/projects/mygithub/DLEHY/.forge/batches/batch-66.md`
- `/Users/smy/projects/mygithub/DLEHY/.forge/reports/batch-66-report.md`
- `/Users/smy/projects/mygithub/DLEHY/snapshot.md`
- `/Users/smy/projects/mygithub/DLEHY/progress.txt`
- `/Users/smy/projects/mygithub/DLEHY/docs/mainline-progress.md`
- `/Users/smy/projects/mygithub/DLEHY/src/book_agent/domain/structure/canonical_ir.py`
- `/Users/smy/projects/mygithub/DLEHY/src/book_agent/services/parse_ir.py`
- `/Users/smy/projects/mygithub/DLEHY/src/book_agent/domain/structure/pdf.py`
- `/Users/smy/projects/mygithub/DLEHY/src/book_agent/services/bootstrap.py`
- `/Users/smy/projects/mygithub/DLEHY/tests/test_parse_ir.py`
- `/Users/smy/projects/mygithub/DLEHY/tests/test_pdf_parse_ir_planning.py`

## Dependencies

- batch-65 verified

## Verification

- `bash .forge/init.sh`
- `.venv/bin/python -m unittest tests.test_parse_ir tests.test_pdf_parse_ir_planning`
- `.venv/bin/python -m py_compile src/book_agent/domain/structure/canonical_ir.py src/book_agent/services/parse_ir.py src/book_agent/domain/structure/pdf.py src/book_agent/services/bootstrap.py tests/test_parse_ir.py tests/test_pdf_parse_ir_planning.py`

## Stop Condition

Stop only after:

- PDF page/zone extraction intent is persisted into canonical truth
- targeted verification passes
- `.forge` truth records the next dependency-closed slice or a real blocker

## Expected Report Path

- `/Users/smy/projects/mygithub/DLEHY/.forge/reports/batch-66-report.md`
