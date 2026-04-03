# Forge Batch 47

Batch: `batch-47`
Mainline: `runtime-self-heal-mainline`
Artifact Status: `frozen`
Frozen at: `2026-04-02 04:17:21 +0800`

## Goal

Widen `.forge/init.sh` so the default Forge v2 resume smoke baseline verifies the recently landed
workflow blockage parity surfaces instead of only the older controller/runtime slice.

## Linked Feature Ids

- `F011`

## Scope

- Add representative workflow blockage parity tests to `.forge/init.sh`.
- Preserve the original runtime self-heal controller smoke coverage.
- Keep the init path idempotent and lightweight enough for routine resume use.

## Owned Files

- `/Users/smy/project/book-agent/.forge/init.sh`
- `/Users/smy/project/book-agent/.forge/spec/FEATURES.json`
- `/Users/smy/project/book-agent/.forge/STATE.md`
- `/Users/smy/project/book-agent/.forge/DECISIONS.md`
- `/Users/smy/project/book-agent/.forge/log.md`

## Dependencies

- batch-43 verified Forge v2 bootstrap
- batch-46 verified workflow blockage parity surfaces

## Verification

- `bash .forge/init.sh`

## Stop Condition

Stop only after the widened init baseline passes and explicitly covers the workflow blockage parity
surfaces added in batches 44-46 without dropping the original controller/runtime smoke.

## Expected Report Path

- `/Users/smy/project/book-agent/.forge/reports/batch-47-report.md`
