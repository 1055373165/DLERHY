# Forge Batch 57

Batch: `batch-57`
Mainline: `runtime-self-heal-mainline`
Artifact Status: `verified`
Frozen at: `2026-04-02 13:35:40 +0800`
Verified at: `2026-04-02 13:35:40 +0800`

## Goal

Protect the warning-hygiene gate from governance drift by proving the default init path still
wires it in and the validator still blocks the known warning classes.

## Linked Feature Ids

- `F023`

## Scope

- Extend `.forge/scripts/validate_forge_v2_governance.sh` so it asserts `.forge/init.sh` still
  captures smoke output and invokes the warning-hygiene validator.
- Extend governance validation so it also asserts the warning-hygiene validator still forbids the
  FastAPI lifecycle and sqlite unclosed-database warning patterns.
- Keep the default Forge v2 baseline green after the new governance assertions land.

## Owned Files

- `/Users/smy/project/book-agent/.forge/scripts/validate_forge_v2_governance.sh`
- `/Users/smy/project/book-agent/.forge/spec/FEATURES.json`
- `/Users/smy/project/book-agent/.forge/spec/SPEC.md`
- `/Users/smy/project/book-agent/.forge/STATE.md`
- `/Users/smy/project/book-agent/.forge/DECISIONS.md`
- `/Users/smy/project/book-agent/.forge/log.md`

## Dependencies

- batch-56 verified default warning-hygiene gate

## Verification

- `bash .forge/scripts/validate_forge_v2_governance.sh`
- `bash .forge/init.sh`

## Stop Condition

Stop only after governance validation proves the warning-hygiene wiring remains present and the
full default Forge v2 baseline still passes.

## Expected Report Path

- `/Users/smy/project/book-agent/.forge/reports/batch-57-report.md`
