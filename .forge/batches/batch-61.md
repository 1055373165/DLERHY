# Forge Batch 61

Batch: `batch-61`
Mainline: `runtime-self-heal-mainline`
Artifact Status: `verified`
Frozen at: `2026-04-02 14:41:54 +0800`
Verified at: `2026-04-02 14:41:54 +0800`

## Goal

Remove the hardcoded current-checkpoint-shape assumption from governance validation by deriving
`current_step` and `active_batch` directly from `.forge/STATE.md`.

## Linked Feature Ids

- `F027`

## Scope

- Update `.forge/scripts/validate_forge_v2_governance.sh` so it reads `current_step` and
  `active_batch` dynamically from `.forge/STATE.md`.
- Update handoff-truth assertions to depend on those dynamic state values instead of
  `mainline_complete / active_batch none`.
- Keep the full default Forge v2 baseline green after removing the hardcoded state-shape
  dependency.

## Owned Files

- `/Users/smy/project/book-agent/.forge/scripts/validate_forge_v2_governance.sh`
- `/Users/smy/project/book-agent/.forge/spec/FEATURES.json`
- `/Users/smy/project/book-agent/.forge/spec/SPEC.md`
- `/Users/smy/project/book-agent/.forge/STATE.md`
- `/Users/smy/project/book-agent/.forge/DECISIONS.md`
- `/Users/smy/project/book-agent/.forge/log.md`

## Dependencies

- batch-60 verified dynamic smoke-contract governance

## Verification

- `bash .forge/scripts/validate_forge_v2_governance.sh`
- `bash .forge/init.sh`

## Stop Condition

Stop only after governance validation derives the current checkpoint shape from STATE truth and the
full default Forge v2 baseline still passes.

## Expected Report Path

- `/Users/smy/project/book-agent/.forge/reports/batch-61-report.md`
