# Forge Batch 43

Batch: `batch-43`
Mainline: `runtime-self-heal-mainline`
Artifact Status: `frozen`
Frozen at: `2026-04-02 03:14:39 +0800`

## Goal

Bootstrap the missing Forge v2 runtime artifacts from the already verified batch-42 runtime
self-heal truth so future resume sessions can start from spec/features/init file truth instead of
reconstructing intent from chat or ad hoc handoff notes.

## Linked Feature Ids

- `F001`
- `F002`
- `F003`
- `F004`
- `F005`
- `F006`
- `F008`

## Scope

- Create `.forge/spec/SPEC.md` as the authoritative Forge v2 narrative spec for the current runtime
  self-heal mainline.
- Create `.forge/spec/FEATURES.json` as the machine-stable acceptance inventory seeded from current
  verified repo truth, while leaving the closest next unresolved capability failing.
- Create `.forge/init.sh` as the idempotent resume-time smoke path for the verified runtime
  controller/self-heal slice.
- Keep the existing `.forge/` single-ledger truth model intact.

## Owned Files

- `/Users/smy/project/book-agent/.forge/batches/batch-43.md`
- `/Users/smy/project/book-agent/.forge/spec/SPEC.md`
- `/Users/smy/project/book-agent/.forge/spec/FEATURES.json`
- `/Users/smy/project/book-agent/.forge/init.sh`

## Dependencies

- Verified batch-42 truth in `.forge/STATE.md`
- Verified controller/runtime self-heal baseline from `.forge/reports/batch-42-report.md`

## Verification

- `bash .forge/init.sh`
- `python - <<'PY' ...` or equivalent file-truth check proving:
  - `.forge/spec/SPEC.md` exists
  - `.forge/spec/FEATURES.json` is valid JSON
  - `FEATURES.json` contains both passing current capabilities and at least one explicit failing
    next capability

## Stop Condition

Stop this batch only after the Forge v2 artifacts exist, the smoke path passes, and the feature
inventory leaves one authoritative next failing target for the following slice.

## Expected Report Path

- `/Users/smy/project/book-agent/.forge/reports/batch-43-report.md`
