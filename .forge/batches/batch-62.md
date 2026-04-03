# Forge Batch 62

Batch: `batch-62`
Mainline: `runtime-self-heal-mainline`
Artifact Status: `verified`
Frozen at: `2026-04-02 15:01:11 +0800`
Verified at: `2026-04-02 15:01:11 +0800`

## Goal

Validate the full STATE checkpoint tuple so `authoritative_batch_contract` and
`expected_report_path` remain semantically aligned with the current batch shape.

## Linked Feature Ids

- `F028`

## Scope

- Update `.forge/scripts/validate_forge_v2_governance.sh` so it reads
  `authoritative_batch_contract` and `expected_report_path` dynamically from `.forge/STATE.md`.
- Validate those fields contextually against `active_batch`.
- Keep the full default Forge v2 baseline green after adding these stronger checkpoint-pointer
  assertions.

## Owned Files

- `/Users/smy/project/book-agent/.forge/scripts/validate_forge_v2_governance.sh`
- `/Users/smy/project/book-agent/.forge/spec/FEATURES.json`
- `/Users/smy/project/book-agent/.forge/spec/SPEC.md`
- `/Users/smy/project/book-agent/.forge/STATE.md`
- `/Users/smy/project/book-agent/.forge/DECISIONS.md`
- `/Users/smy/project/book-agent/.forge/log.md`

## Dependencies

- batch-61 verified dynamic current-state-shape governance

## Verification

- `bash .forge/scripts/validate_forge_v2_governance.sh`
- `bash .forge/init.sh`

## Stop Condition

Stop only after governance validation semantically validates the full STATE checkpoint tuple and
the full default Forge v2 baseline still passes.

## Expected Report Path

- `/Users/smy/project/book-agent/.forge/reports/batch-62-report.md`
