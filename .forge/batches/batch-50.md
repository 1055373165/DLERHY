# Forge Batch 50

Batch: `batch-50`
Mainline: `runtime-self-heal-mainline`
Artifact Status: `frozen`
Frozen at: `2026-04-02 04:25:40 +0800`

## Goal

Widen `.forge/init.sh` so the default Forge v2 smoke also protects the explicit packet latest-run
workflow parity regression.

## Linked Feature Ids

- `F014`

## Scope

- Add the packet latest-run workflow parity API regression to `.forge/init.sh`.
- Preserve the already widened review-side workflow parity smoke.
- Keep the init baseline practical for resume use.

## Owned Files

- `/Users/smy/project/book-agent/.forge/init.sh`
- `/Users/smy/project/book-agent/.forge/spec/FEATURES.json`
- `/Users/smy/project/book-agent/.forge/STATE.md`
- `/Users/smy/project/book-agent/.forge/DECISIONS.md`
- `/Users/smy/project/book-agent/.forge/log.md`

## Dependencies

- batch-47 verified widened init smoke
- batch-49 verified packet latest-run workflow parity coverage

## Verification

- `bash .forge/init.sh`

## Stop Condition

Stop only after the default init smoke covers the packet latest-run workflow parity regression and
still passes end-to-end.

## Expected Report Path

- `/Users/smy/project/book-agent/.forge/reports/batch-50-report.md`
