# Export Runtime Incidents

## 2026-03-17 · EPUB image-only cover chapter blocks final export

### Incident

- A persisted EPUB record for `Agentic AI Data Architectures ...` failed in the one-click `translate_full` pipeline during `bilingual_html`.
- The run stopped with `run.terminal_failed_items_present`.
- The direct gate error was:

```text
Chapter fb054484-1ade-5848-a32c-694c884597b6 must pass review before final export; current status is review_required.
```

### Live evidence

- Document id: `1d8ba1ca-de9e-5014-b00e-77b6c3dbb3e4`
- Failed run id: `042a427a-3c42-4c16-b9f7-22a5297a7a9b`
- Review result on the live record:
  - `total_issue_count = 1`
  - `total_action_count = 1`
  - only chapter `fb054484-1ade-5848-a32c-694c884597b6` stayed in `review_required`
- Chapter worklist showed the remaining blocker was a packet-layer `CONTEXT_FAILURE` with planned action `REBUILD_PACKET_THEN_RERUN`.

### Root cause

Two different problems overlapped:

1. The earlier EPUB image fix only suppressed the chapter-brief (`memory` layer) `missing_chapter_title` issue for an image-only untitled cover.
2. Persisted records created before that fix could still contain packet-level `open_questions = ["missing_chapter_title"]`.

Because `ReviewService._packet_context_failure_issues()` treated any packet `open_questions` as a blocking packet issue, review kept recreating an open blocker even though the chapter itself was just a cover image.

There was a second orchestration bug:

1. Review correctly created a planned `REBUILD_PACKET_THEN_RERUN` action.
2. `ExportService._enforce_gate()` raised a generic `ExportGateError` for `review_required`.
3. The gate error did not include the already planned follow-up actions.
4. `DocumentWorkflowService.export_document(auto_execute_followup_on_gate=True)` therefore had nothing to execute and the one-click run hard-failed instead of self-repairing.

### Fix

#### Review-side fix

- Skip packet-layer `missing_chapter_title` context failures when the whole chapter is confirmed to be an image-only untitled cover/frontmatter artifact.
- This intentionally ignores stale persisted sentence/packet flags and trusts the chapter-level artifact shape.

#### Export-gate fix

- When final export is blocked by open blocking review issues, the gate now returns:
  - `chapter_id`
  - `issue_ids`
  - structured planned `followup_actions`
- This lets `auto_execute_followup_on_gate=True` execute the existing planned action instead of stopping with `no_followup_actions`.

### Why this design

- Assumption: image-only cover/frontmatter chapters are source-only artifacts and should not block book-level final export.
- Assumption: if review already persisted a planned fix-up action, export should reuse it instead of forcing a second control-plane round trip.
- Boundary: this skip applies only to `missing_chapter_title` on image-only untitled chapters. It does not suppress real content-bearing untitled chapters.
- Boundary: export auto-followup still depends on review having already created a persisted planned action.

### Validation

- Regression: image-only cover chapter with stale chapter-brief and packet `missing_chapter_title` now reviews cleanly and exports successfully.
- Regression: final export can now auto-execute a planned `REBUILD_PACKET_THEN_RERUN` follow-up for a packet context failure and succeed in the same request.
- Real isolated rerun on the source EPUB succeeded end-to-end:
  - translation packets completed
  - review issues dropped to zero
  - chapter bilingual exports succeeded
  - merged export succeeded

### Recovery for existing failed records

1. Restart the API process so the patched review/export logic is live.
2. Retry the failed run from history, or start a fresh `translate_full` run on the same document.
3. Expected outcome:
   - the stale cover chapter blocker is skipped
   - if any persisted packet blocker remains, export auto-followup executes the planned rebuild/rerun
   - `bilingual_html` and `merged_html` complete without manual intervention
