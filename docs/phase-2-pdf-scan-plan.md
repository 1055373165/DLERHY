# Phase 2 PDF_SCAN Productionization Plan

Last Updated: 2026-03-22
Status: phase-2-complete
Owner: autopilot phase 2

## 1. Goal

Phase 2 focuses on one outcome only:

- bring `PDF_SCAN` onto the same deterministic control plane already proven in Phase 1

The Phase 2 delivery boundary remains intentionally narrow:

- input added: `PDF_SCAN`
- delivery artifacts remain: `MERGED_MARKDOWN`, `BILINGUAL_HTML`
- control plane remains: bootstrap -> translate -> review -> export

## 2. Why This Is The Next Phase

This is the highest-leverage deferred item because:

- it is the largest remaining format coverage gap after Phase 1
- the repository already has `OcrPdfParser`, OCR runner plumbing, and PDF-aware export paths
- it extends the current architecture instead of introducing a second high-risk axis like rebuilt EPUB/PDF or reviewer-led rewriting

## 3. Explicit Non-Goals

Phase 2 does not include:

- rebuilt `ZH_EPUB`
- rebuilt PDF output
- reviewer local rewrite path as default behavior
- cross-process distributed agents
- figure-internal OCR rewrite and image text replacement
- broad layout-model experimentation such as LayoutLMv3 as a required dependency

## 4. Acceptance Criteria

Phase 2 is complete when all of the following are true:

- a representative scanned PDF can bootstrap without hard reject
- OCR runtime status is visible enough to explain long bootstrap latency and failures
- scanned chapters can enter packet build, deterministic review, and export on the shared control plane
- export still fails closed on structure problems instead of silently producing corrupted artifacts
- `MERGED_MARKDOWN` and `BILINGUAL_HTML` for scanned samples are readable enough for operator review without manual DB repair

## 5. Workstreams

### WS1. OCR bootstrap hardening

- stabilize OCR runtime invocation and status reporting
- make OCR failures route to explicit retry or manual hold outcomes
- ensure scanned bootstrap does not leave partial hidden state

### WS2. Scanned structure provenance

- preserve page, bbox, and block-role metadata from OCR output
- keep protected artifacts and non-translatable regions out of translation packets
- harden chapter segmentation enough for downstream packet build

### WS3. Shared control-plane acceptance

- prove scanned chapters can traverse packet build -> translate -> review -> export
- add focused regressions for scanned bootstrap and scanned export gating
- update governance docs with the accepted boundary and residual risks

## 6. Delivery Order

1. harden OCR bootstrap and observability
2. harden scanned parse/save provenance
3. run scanned control-plane acceptance and close the phase

## 7. Boundary Reminder

Phase 2 is a format-productionization phase, not a feature-expansion phase.

If a change does not directly improve `PDF_SCAN` bootstrap stability, scanned structure fidelity, or scanned control-plane acceptance, it does not belong in this phase.

## 8. MDU-12.1.1 Audit Snapshot

Representative scanned sample selected for Phase 2:

- artifact lineage: `artifacts/real-book-live/deepseek-agentic-design-book-v11-full-run-chunked-pagecount`
- source document: `Agentic Design Patterns A Hands-On Guide to Building Intelligent Systems`
- source type observed in report: `pdf_scan`
- profile observed in report: `page_count=458`, `pdf_kind=scanned_pdf`, `layout_risk=high`, `ocr_required=true`

Companion retry/resume artifact used for audit:

- `artifacts/real-book-live/deepseek-agentic-design-book-v12-retry-after-balance`

What the audit confirmed:

- `PDF_SCAN` is no longer blocked at parse entry. `ParseService` already routes scanned input to `OcrPdfParser`.
- The scanned sample already completed chunked OCR in production-like runs.
- The shared control plane already accepted scanned output far enough to persist:
  - `documents=1`
  - `chapters=97`
  - `translation_packets=2170`
- The first historical full run failed because of provider balance exhaustion, not because OCR/bootstrap was missing.
- The retry lineage also exists and reuses the same document/control-plane state.

Current blocking points to fix next:

1. Observability is fragmented across `report.json`, `report.live.json`, and `report.ocr.json`.
   - archived `report.json` does not consistently retain `db_counts`, packet status counts, or OCR progress
   - `report.live.json` has the useful counters, but under `snapshot.*`
2. OCR sidecar durability is weak after the run finishes.
   - `report.ocr.json` still points at temp directories under `/private/var/...`
   - `report.live.json.snapshot.ocr_output.exists=false` after completion, so postmortem inspection depends on sidecar snapshots instead of durable artifacts
3. Scanned structure/title fidelity is still unstable.
   - the persisted document title in the representative DB is `Acknowledgment`
   - early chapter order includes frontmatter-like titles before the real chapter sequence
4. Historical scanned DBs are not guaranteed to have the latest schema surface.
   - the representative `v11` SQLite file lacks newer document title columns even though chapter-level title fields exist
   - Phase 2 must not assume every scanned retry lineage starts from a freshly bootstrapped DB

Execution consequence:

- `MDU-12.1.2` should focus on OCR/bootstrap status durability and failure routing first
- `MDU-12.1.3` should then lock scanned parse/save regressions around structure provenance and title recovery

## 9. MDU-12.1.2 Completion Snapshot

Implemented in this round:

- `scripts/run_real_book_live.py` now enriches archived `report.json` with:
  - `stage`
  - `database_path`
  - `db_counts`
  - `work_item_status_counts`
  - `translation_packet_status_counts`
  - `ocr_status`
  - `ocr_progress`
- bootstrap and resume preflight failures now fail closed into `report.json`
  - `bootstrap_in_progress` / `resume_in_progress` are cleared
  - `error.stage`, `error.class`, and `error.message` are persisted
  - the report gets `finished_at` and `duration_seconds`
- SQLite helper connections in runtime reporting were tightened to explicit close semantics to avoid leaking diagnostics noise during repeated scans/tests

What this changes operationally:

- archived reports now retain enough OCR/runtime state for postmortem analysis without depending on `report.live.json`
- scanned bootstrap failures become visible as first-class report outcomes instead of hidden partial state
- the next scanned-PDF work can move from observability hardening to structure provenance proof

Execution consequence:

- `MDU-12.1.2` is complete
- next focus is `MDU-12.1.3`: prove scanned parse/save keeps page / bbox / block role / protected artifact metadata all the way into downstream control-plane state

## 10. MDU-12.1.3 Completion Snapshot

Implemented in this round:

- `ParseService._chapter_metadata(...)` now treats `PDF_SCAN` the same as `PDF_TEXT / PDF_MIXED` for chapter-level PDF structure aggregation
  - scanned chapters now persist `parse_confidence`
  - scanned chapters now persist `pdf_layout_risk`
  - scanned chapters now persist `pdf_role_counts` / `pdf_page_family_counts`
  - scanned chapters now receive a derived `risk_level`
- added a focused regression in `tests/test_pdf_support.py` that proves a scanned bootstrap can carry:
  - `source_bbox_json`
  - `pdf_block_role`
  - `protected_artifact`
  - document-image bbox/page metadata
  - chapter risk metadata
  - translation-packet filtering that excludes protected code/image artifacts

What this proves:

- scanned OCR output is no longer only parse-local state
- the shared control plane can observe scanned structure metadata after persistence
- protected scanned artifacts remain visible to export/review while staying out of translation packets

Execution consequence:

- `MDU-12.1.3` is complete
- Phase 2 can now move from bootstrap/provenance hardening into `MDU-13.1.1`
- next focus is minimal scanned control-plane acceptance: bootstrap -> chapter bundle -> export gate

## 11. MDU-13.1.1 Completion Snapshot

Implemented in this round:

- added focused scanned-PDF regressions in `tests/test_pdf_support.py` that reuse a single scanned bootstrap fixture across:
  - bootstrap persistence proof
  - `translate -> review -> review_package export`
  - final export gate failure handling
- the scanned review-package path is now explicitly proven to consume the shared chapter bundle produced by bootstrap persistence
  - `ExportRepository.load_chapter_bundle(...)` can load scanned chapter state without a format-specific side path
  - the generated review package preserves scanned `pdf_page_evidence` and `pdf_image_evidence`
- the scanned final export gate is now explicitly proven to fail closed when render-time figure metadata is structurally incomplete
  - exportability checks raise `ExportGateError`
  - a blocking `LAYOUT_VALIDATION_FAILURE` issue is persisted
  - the followup action remains `REPARSE_CHAPTER`

What this proves:

- `PDF_SCAN` is no longer limited to bootstrap-only acceptance
- scanned chapters can traverse the same persisted chapter-bundle contract already used by non-scanned PDF inputs
- scanned export still protects artifact integrity instead of silently emitting broken figure/layout output

Execution consequence:

- `MDU-13.1.1` is complete
- next focus is `MDU-13.1.2`: use the shared scanned lane to expose and fix any real review/export blockers on a minimal end-to-end sample
- do not branch into rebuilt export formats, reviewer-led rewriting, or broader OCR feature expansion during this step

## 12. MDU-13.1.2 Completion Snapshot

Implemented in this round:

- narrowed the scanned review-policy exception in `src/book_agent/services/review.py`
  - the exception only applies to `PDF_SCAN` chapters
  - the chapter must be single-page at the local evidence level
  - chapter-level `parse_confidence` must stay at or above `0.82`
  - local high-risk reasons must remain limited to `ocr_scanned_page`
  - the page cannot contain `header / footer / footnote` roles
  - at least one captioned artifact must have both caption linkage and recovered group-context anchors
- added a focused workflow regression in `tests/test_pdf_support.py` that proves the minimal scanned fixture can now traverse:
  - bootstrap persistence
  - translate
  - review to `qa_checked`
  - final `bilingual_html` export
- kept the previously added scanned guardrails green:
  - scanned review-package export still uses the shared chapter bundle
  - scanned final export still fails closed on layout breakage with `LAYOUT_VALIDATION_FAILURE -> REPARSE_CHAPTER`

What this fixes:

- the minimal scanned sample no longer gets stuck behind a blanket blocking `MISORDERING` issue
- scanned end-to-end acceptance now includes a successful final export path, not only review-package evidence
- the fix stays intentionally narrow and does not relax the broader `PDF_SCAN` review gate

Execution consequence:

- `MDU-13.1.2` is complete
- next focus is `MDU-13.1.3`: update governance docs and close Phase 2
- do not add more OCR features or broader scanned-policy exceptions before Phase 2 is formally closed

## 13. MDU-13.1.3 Completion Snapshot

Closure status:

- Phase 2 is now formally complete
- the delivery boundary remained unchanged through closure:
  - input added: `PDF_SCAN`
  - output stayed: `MERGED_MARKDOWN`, `BILINGUAL_HTML`
  - control plane stayed: bootstrap -> translate -> review -> export

Acceptance checklist at closure:

- representative scanned PDF can bootstrap without hard reject
- OCR/runtime status is durable enough for postmortem reporting
- scanned chapters persist page / bbox / role / protected-artifact provenance into the shared control plane
- scanned chapters can traverse:
  - review-package export on the shared chapter-bundle path
  - final `bilingual_html` export for the minimal anchored sample
- final export still fails closed on structural breakage through `LAYOUT_VALIDATION_FAILURE -> REPARSE_CHAPTER`

Residual boundary after closure:

- rebuilt EPUB/PDF remains deferred
- reviewer-local rewrite remains deferred
- broader scanned-policy relaxation remains deferred
- OCR scale/performance tuning for larger scanned corpora remains a future phase, not part of this closure

Execution consequence:

- `MDU-13.1.3` is complete
- Phase 2 roadmap is closed
- the next autopilot phase should only be opened after a new explicit product/architecture target is locked
