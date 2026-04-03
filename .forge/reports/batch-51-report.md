# Batch 51 Report

Batch: `batch-51`
Status: `verified`
Artifact Status: `verified from current repo truth`
Verified at: `2026-04-02 08:08:39 +0800`

## Delivered

- Added explicit branch-intake governance to Forge v2 with three branch classes:
  - `mainline_required`
  - `mainline_adjacent`
  - `out_of_band`
- Added a single-ledger transaction rule so accepted, deferred, and rejected branches all leave
  visible traces in `.forge/`.
- Added stop-legality audit rules so verified batches must resolve discovered branch work and then
  either continue, record a blocker, or explicitly record that no next dependency-closed slice
  remains.
- Reflected the same governance model in the current `.forge` spec, feature inventory, and
  decision set.
- Reconciled `snapshot.md`, `progress.txt`, and `docs/mainline-progress.md` to the new
  `mainline_complete` truth so the next resume does not inherit stale `batch-48` guidance.

## Files Changed

- `/Users/smy/project/book-agent/forge-v2/SKILL.md`
- `/Users/smy/project/book-agent/forge-v2/references/branch-intake-governance.md`
- `/Users/smy/project/book-agent/forge-v2/references/long-running-hardening.md`
- `/Users/smy/project/book-agent/forge-v2/references/requirement-amplification.md`
- `/Users/smy/project/book-agent/.forge/spec/SPEC.md`
- `/Users/smy/project/book-agent/.forge/spec/FEATURES.json`
- `/Users/smy/project/book-agent/.forge/DECISIONS.md`
- `/Users/smy/project/book-agent/.forge/STATE.md`
- `/Users/smy/project/book-agent/progress.txt`
- `/Users/smy/project/book-agent/snapshot.md`
- `/Users/smy/project/book-agent/docs/mainline-progress.md`

## Verification

- `python3 -m json.tool .forge/spec/FEATURES.json`
  - `valid JSON`
- `rg -n "mainline_required|mainline_adjacent|out_of_band|branch intake|stop-legality|no next dependency-closed slice remains" forge-v2/SKILL.md forge-v2/references/branch-intake-governance.md forge-v2/references/long-running-hardening.md forge-v2/references/requirement-amplification.md .forge/spec/SPEC.md .forge/DECISIONS.md`
  - `matched framework-governance and stop-legality contract text across all target artifacts`
- `rg -n "mainline_complete|F017|Ran 42 tests|change_request|batch-51|batch-50" snapshot.md progress.txt docs/mainline-progress.md`
  - `matched refreshed handoff docs against the new mainline-complete truth`

## Features Flipped

- `F015`
- `F016`
- `F017`

## Scope Notes

- The current `FEATURES.json` inventory is now fully green.
- Any further work should begin as a new change request rather than pretending an unfinished
  dependency-closed slice still exists.
