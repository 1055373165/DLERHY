# Forge Batch 51

Batch: `batch-51`
Mainline: `runtime-self-heal-mainline`
Artifact Status: `frozen`
Frozen at: `2026-04-02 08:08:39 +0800`

## Goal

Formalize Forge v2 branch-intake governance and stop-legality rules so autonomous continuation no
longer depends on ad hoc agent judgment when new work appears mid-run.

## Linked Feature Ids

- `F015`
- `F016`
- `F017`

## Scope

- Define discovered-branch classes and admission rules in `forge-v2`.
- Define the single-ledger transaction required for accepted, deferred, and rejected branches.
- Define stop-legality audit rules for post-verification continuation.
- Reflect the new governance contract in the current `.forge` executable spec and decision set.
- Align the human-facing handoff docs with the resulting `mainline_complete` file truth.

## Owned Files

- `/Users/smy/project/book-agent/forge-v2/SKILL.md`
- `/Users/smy/project/book-agent/forge-v2/references/branch-intake-governance.md`
- `/Users/smy/project/book-agent/forge-v2/references/long-running-hardening.md`
- `/Users/smy/project/book-agent/forge-v2/references/requirement-amplification.md`
- `/Users/smy/project/book-agent/.forge/spec/SPEC.md`
- `/Users/smy/project/book-agent/.forge/spec/FEATURES.json`
- `/Users/smy/project/book-agent/.forge/DECISIONS.md`
- `/Users/smy/project/book-agent/.forge/STATE.md`
- `/Users/smy/project/book-agent/.forge/log.md`
- `/Users/smy/project/book-agent/progress.txt`
- `/Users/smy/project/book-agent/snapshot.md`
- `/Users/smy/project/book-agent/docs/mainline-progress.md`

## Dependencies

- batch-50 verified default init smoke parity
- existing Forge v2 no-voluntary-stop and post-verification loop hardening

## Verification

- `python3 -m json.tool .forge/spec/FEATURES.json`
- `rg -n "mainline_required|mainline_adjacent|out_of_band|branch intake|stop-legality|no next dependency-closed slice remains" forge-v2/SKILL.md forge-v2/references/branch-intake-governance.md forge-v2/references/long-running-hardening.md forge-v2/references/requirement-amplification.md .forge/spec/SPEC.md .forge/DECISIONS.md`
- `rg -n "mainline_complete|F017|Ran 42 tests|change_request|batch-51|batch-50" snapshot.md progress.txt docs/mainline-progress.md`

## Stop Condition

Stop only after branch-intake classes, ledger transaction rules, and stop-legality audit rules are
all present in both Forge v2 and the current `.forge` execution truth.

## Expected Report Path

- `/Users/smy/project/book-agent/.forge/reports/batch-51-report.md`
