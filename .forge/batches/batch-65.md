# Forge Batch 65

Batch: `batch-65`
Mainline: `high-fidelity-document-translation-foundation`
Artifact Status: `in_progress`
Frozen at: `2026-04-03 11:55:38 +0800`

## Goal

Land the first source-preserving EPUB export slice on top of the new canonical-truth foundation.

This batch should prove that translated EPUB output no longer has to be rebuilt from scratch when
the source package and DOM can be patched safely.

## Linked Feature Ids

- `F006`

## Scope

- Add a document-level source-preserving EPUB export path using `ExportType.ZH_EPUB`.
- Reuse the original EPUB archive/package structure instead of generating a synthetic book spine.
- Patch translatable XHTML content from translated render-block truth while preserving original
  nav, manifest, ids, anchors, and internal links.
- Record document-level export metadata and route evidence for the new export type.
- Cover the slice with targeted tests that prove:
  - translated content is written back into source XHTML
  - TOC/navigation and archive structure survive intact
  - footnote or internal-anchor links are preserved rather than regenerated

## Owned Files

- `/Users/smy/projects/mygithub/DLEHY/.forge/spec/FEATURES.json`
- `/Users/smy/projects/mygithub/DLEHY/.forge/DECISIONS.md`
- `/Users/smy/projects/mygithub/DLEHY/.forge/STATE.md`
- `/Users/smy/projects/mygithub/DLEHY/.forge/log.md`
- `/Users/smy/projects/mygithub/DLEHY/.forge/batches/batch-65.md`
- `/Users/smy/projects/mygithub/DLEHY/.forge/reports/batch-65-report.md`
- `/Users/smy/projects/mygithub/DLEHY/snapshot.md`
- `/Users/smy/projects/mygithub/DLEHY/progress.txt`
- `/Users/smy/projects/mygithub/DLEHY/docs/mainline-progress.md`
- `/Users/smy/projects/mygithub/DLEHY/src/book_agent/services/export.py`
- `/Users/smy/projects/mygithub/DLEHY/src/book_agent/services/export_routing.py`
- `/Users/smy/projects/mygithub/DLEHY/src/book_agent/services/workflows.py`
- `/Users/smy/projects/mygithub/DLEHY/tests/test_source_preserving_epub_export.py`

## Dependencies

- batch-64 verified

## Verification

- `bash .forge/init.sh`
- `.venv/bin/python -m unittest tests.test_parse_ir tests.test_source_preserving_epub_export`
- `.venv/bin/python -m py_compile src/book_agent/domain/models/parse_revision.py src/book_agent/domain/structure/canonical_ir.py src/book_agent/services/parse_ir.py src/book_agent/services/export.py src/book_agent/services/export_routing.py src/book_agent/services/workflows.py tests/test_parse_ir.py tests/test_source_preserving_epub_export.py`

## Stop Condition

Stop only after:

- a source-preserving EPUB export path exists in code and is wired through document export
- targeted verification passes
- `.forge` truth records either the next dependency-closed slice or a real blocker

## Expected Report Path

- `/Users/smy/projects/mygithub/DLEHY/.forge/reports/batch-65-report.md`
