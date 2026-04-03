# Batch 56 Report

Batch: `batch-56`
Status: `verified`
Artifact Status: `verified from current repo truth`
Verified at: `2026-04-02 13:22:41 +0800`

## Delivered

- `.forge/init.sh` now captures the runtime smoke output into a temp log and validates warning
  hygiene before governance validation runs.
- Added `.forge/scripts/validate_init_warning_hygiene.sh` to fail on the two previously removed
  warning classes:
  - FastAPI lifecycle `on_event` deprecation warnings
  - sqlite `ResourceWarning: unclosed database`
- Updated the governance validator so handoff truth checks target the latest checkpoint rather than
  stale batch-52/F018 markers.

## Files Changed

- `/Users/smy/project/book-agent/.forge/init.sh`
- `/Users/smy/project/book-agent/.forge/scripts/validate_init_warning_hygiene.sh`
- `/Users/smy/project/book-agent/.forge/scripts/validate_forge_v2_governance.sh`
- `/Users/smy/project/book-agent/.forge/spec/FEATURES.json`
- `/Users/smy/project/book-agent/.forge/spec/SPEC.md`
- `/Users/smy/project/book-agent/.forge/DECISIONS.md`
- `/Users/smy/project/book-agent/.forge/STATE.md`
- `/Users/smy/project/book-agent/.forge/log.md`

## Verification

- `bash .forge/init.sh`
  - `Ran 42 tests, OK`
  - `[forge-v2:init] smoke warning hygiene validated`
  - `[forge-v2:init] governance contract validated`
- `tmp=$(mktemp); printf 'on_event is deprecated\n' > "$tmp"; if bash .forge/scripts/validate_init_warning_hygiene.sh "$tmp"; then echo unexpected_pass; else echo expected_fail; fi; rm -f "$tmp"`
  - `expected_fail`

## Features Flipped

- `F022`

## Scope Notes

- This batch closes the gap between “warning-free according to side probes” and “warning-free as
  part of the default autonomous takeover baseline”.
