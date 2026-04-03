# Book Agent High-Fidelity Translation Snapshot

Last Updated: 2026-04-03 12:05:01 +0800
Workspace: `/Users/smy/projects/mygithub/DLEHY`
Branch: `main`
Worktree Policy: single live worktree only

## Current Mainline

This repo is now in a Forge v2 `change_request` run.

The active mainline is no longer runtime self-heal closure. That line remains verified historical
baseline. The active mainline is:

`high-fidelity document translation foundation`

The immediate goal is to introduce Canonical Document IR and parse revision truth so future parser,
translator, and exporter work share the same source of truth.

## Active Batch

- latest passing feature id: `F006`
- current_step: `batch-66_in_progress`
- active_batch: `batch-66`
- authoritative_batch_contract: `.forge/batches/batch-66.md`
- expected_report_path: `.forge/reports/batch-66-report.md`

## Why This Change Request Exists

The user explicitly delegated future development to Forge v2 and redirected the mainline toward
the high-fidelity translation architecture described in:

- `docs/high-fidelity-document-translation-incremental-plan.md`

That means the next credible change_request is not “more runtime self-heal hardening.” It is:

- parse revision persistence
- canonical IR sidecar truth
- projection provenance back into execution artifacts

## Verified Batch-64 Outcome

Batch-64 is now verified:

- `.forge` truth is aligned to the new mainline
- parse revision models and sidecar persistence exist
- canonical IR schema + parse IR service exist
- repository support exists
- block/sentence provenance now carries parse revision and canonical node ids

## Verified Batch-65 Outcome

Batch-65 is now verified:

- `zh_epub` document-level export exists
- translated XHTML is patched back into the original EPUB archive
- nav, anchors, ids, and internal links are preserved
- targeted export tests and the default Forge v2 baseline passed

## Current Batch-66 Goal

Batch-66 owns the next closest dependency-closed slice:

- persist PDF page or zone extraction intent into canonical truth
- carry extraction-mode risk reasons into parse IR sidecars
- make mixed-risk PDF routing restartable from canonical truth instead of only block metadata

## Verified Resume Baseline

- `bash .forge/init.sh`
- smoke warning hygiene validated
- governance contract validated

## Forge v2 Contract

This run remains under active Forge v2 takeover:

- branch work must still be classified as `mainline_required`, `mainline_adjacent`, or
  `out_of_band`
- continuation scan is mandatory after each verified slice
- stop-legality must be proven from file truth, not chat
