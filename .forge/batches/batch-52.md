# Forge Batch 52

Batch: `batch-52`
Mainline: `runtime-self-heal-mainline`
Artifact Status: `verified`
Frozen at: `2026-04-02 12:27:34 +0800`

## Goal

Teach the default Forge v2 init smoke to validate governance drift as well as runtime self-heal
behavior.

## Linked Feature Ids

- `F018`

## Scope

- Add a lightweight governance validation script under `.forge/scripts/`.
- Extend `.forge/init.sh` to run that validation after the runtime self-heal smoke baseline.
- Keep the validation cheap enough for routine resume use.
- Reflect the new change-request slice in the executable spec and live ledger.

## Owned Files

- `/Users/smy/project/book-agent/.forge/scripts/validate_forge_v2_governance.sh`
- `/Users/smy/project/book-agent/.forge/init.sh`
- `/Users/smy/project/book-agent/.forge/spec/SPEC.md`
- `/Users/smy/project/book-agent/.forge/spec/FEATURES.json`
- `/Users/smy/project/book-agent/.forge/DECISIONS.md`
- `/Users/smy/project/book-agent/.forge/STATE.md`
- `/Users/smy/project/book-agent/.forge/log.md`
- `/Users/smy/project/book-agent/progress.txt`
- `/Users/smy/project/book-agent/snapshot.md`
- `/Users/smy/project/book-agent/docs/mainline-progress.md`

## Dependencies

- batch-50 verified default smoke baseline
- batch-51 verified governance contract

## Verification

- `bash .forge/init.sh`

## Stop Condition

Stop only after the default init smoke validates both runtime behavior and the active governance
contract end-to-end.

## Expected Report Path

- `/Users/smy/project/book-agent/.forge/reports/batch-52-report.md`
