# Forge Decisions

1. Workspace decision
- Stay in the single shared checkout on `main`.
- Do not create a second live worktree.

2. Mainline decision
- The active mainline is now `high-fidelity document translation foundation`.
- Runtime self-heal closure is preserved as verified prior work, not the current delivery target.

3. Problem-framing decision
- Preserve the current translation execution IR.
- Add a Canonical Document IR layer above it instead of bloating `Block.source_span_json` into a
  pseudo-schema.

4. Storage decision
- Use sidecar-first persistence for the first canonical IR slice:
  - database revision record
  - canonical IR artifact on disk
- Do not start with a fully normalized page/line/span relational schema.

5. Export strategy decision
- EPUB’s long-term primary direction is source-preserving patch export.
- PDF’s near-term truthful product promise is high-quality Chinese reading edition, not
  page-faithful facsimile.

6. Next-slice decision
- With `F006` now green, the closest dependency-closed next slice is `F007`.
- Reason:
  - EPUB now has a truthful source-preserving path
  - mixed-risk PDF extraction intent still lives mostly in routing heuristics and block metadata,
    not in canonical truth
- Therefore batch-66 is `pdf page/zone extraction intent persistence`.

7. Current write-set decision
- Prefer staying inside:
  - `.forge/spec/FEATURES.json`
  - `.forge/DECISIONS.md`
  - `.forge/STATE.md`
  - `.forge/log.md`
  - `.forge/batches/batch-65.md`
  - `.forge/reports/batch-65-report.md`
  - `.forge/batches/batch-66.md`
  - `.forge/reports/batch-66-report.md`
  - `snapshot.md`
  - `progress.txt`
  - `docs/mainline-progress.md`
  - `src/book_agent/domain/structure/canonical_ir.py`
  - `src/book_agent/services/parse_ir.py`
  - `src/book_agent/domain/structure/pdf.py`
  - `src/book_agent/services/bootstrap.py`
  - `tests/test_parse_ir.py`
  - `tests/test_pdf_parse_ir_planning.py`
- Expand only if blocked by a real dependency.

8. Verification decision
- Batch-66 must pass:
  - `bash .forge/init.sh`
  - `.venv/bin/python -m unittest tests.test_parse_ir tests.test_pdf_parse_ir_planning`
  - `.venv/bin/python -m py_compile src/book_agent/domain/structure/canonical_ir.py src/book_agent/services/parse_ir.py src/book_agent/domain/structure/pdf.py src/book_agent/services/bootstrap.py tests/test_parse_ir.py tests/test_pdf_parse_ir_planning.py`

9. Branch-governance decision
- While this mainline is active, newly discovered work must still be classified as exactly one of:
  - `mainline_required`
  - `mainline_adjacent`
  - `out_of_band`
- Accepted, deferred, and rejected branches must be written back to file truth.

10. Continuation decision
- A verified batch is not a stop condition by itself.
- Forge v2 must perform a continuation scan after batch-64.
- Stop-legality must still be proven from file truth:
  - real blocker
  - explicit user pause
  - no next dependency-closed slice remains after continuation scan
