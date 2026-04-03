# Forge Batch 60

Batch: `batch-60`
Mainline: `runtime-self-heal-mainline`
Artifact Status: `verified`
Frozen at: `2026-04-02 14:31:52 +0800`
Verified at: `2026-04-02 14:31:52 +0800`

## Goal

Remove the fixed smoke-count dependency from governance validation so future smoke widenings do
not require another validator self-patch just to track `Ran N tests`.

## Linked Feature Ids

- `F026`

## Scope

- Update `.forge/scripts/validate_forge_v2_governance.sh` so handoff-truth assertions depend on
  the default `bash .forge/init.sh` contract and its post-smoke validation markers instead of a
  fixed test-count string.
- Preserve the existing latest-checkpoint and latest-feature dynamic checks.
- Keep the full default Forge v2 baseline green after removing the fixed smoke-count dependency.

## Owned Files

- `/Users/smy/project/book-agent/.forge/scripts/validate_forge_v2_governance.sh`
- `/Users/smy/project/book-agent/.forge/spec/FEATURES.json`
- `/Users/smy/project/book-agent/.forge/spec/SPEC.md`
- `/Users/smy/project/book-agent/.forge/STATE.md`
- `/Users/smy/project/book-agent/.forge/DECISIONS.md`
- `/Users/smy/project/book-agent/.forge/log.md`

## Dependencies

- batch-59 verified dynamic latest-checkpoint governance

## Verification

- `bash .forge/scripts/validate_forge_v2_governance.sh`
- `bash .forge/init.sh`

## Stop Condition

Stop only after governance validation no longer depends on a fixed smoke-count marker and the full
default Forge v2 baseline still passes.

## Expected Report Path

- `/Users/smy/project/book-agent/.forge/reports/batch-60-report.md`
