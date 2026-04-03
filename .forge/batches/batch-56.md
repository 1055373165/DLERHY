# Forge Batch 56

Batch: `batch-56`
Mainline: `runtime-self-heal-mainline`
Artifact Status: `verified`
Frozen at: `2026-04-02 13:22:41 +0800`
Verified at: `2026-04-02 13:22:41 +0800`

## Goal

Promote the previously ad hoc warning-negative probes into the default Forge v2 init path so the
smoke baseline fails immediately if known framework-noise warnings return.

## Linked Feature Ids

- `F022`

## Scope

- Capture default smoke output inside `.forge/init.sh`.
- Validate that the smoke output does not contain the previously removed FastAPI lifecycle or
  sqlite unclosed-database warnings.
- Keep governance validation aligned with the latest handoff truth after this new warning-hygiene
  gate lands.

## Owned Files

- `/Users/smy/project/book-agent/.forge/init.sh`
- `/Users/smy/project/book-agent/.forge/scripts/validate_init_warning_hygiene.sh`
- `/Users/smy/project/book-agent/.forge/scripts/validate_forge_v2_governance.sh`
- `/Users/smy/project/book-agent/.forge/spec/FEATURES.json`
- `/Users/smy/project/book-agent/.forge/spec/SPEC.md`
- `/Users/smy/project/book-agent/.forge/STATE.md`
- `/Users/smy/project/book-agent/.forge/DECISIONS.md`
- `/Users/smy/project/book-agent/.forge/log.md`

## Dependencies

- batch-54 verified lifecycle warning cleanup
- batch-55 verified sqlite resource-warning cleanup

## Verification

- `bash .forge/init.sh`
- `tmp=$(mktemp); printf 'on_event is deprecated\n' > "$tmp"; if bash .forge/scripts/validate_init_warning_hygiene.sh "$tmp"; then echo unexpected_pass; else echo expected_fail; fi; rm -f "$tmp"`

## Stop Condition

Stop only after the default Forge v2 smoke itself enforces warning hygiene and the validator
proves it will reject a forbidden-warning log.

## Expected Report Path

- `/Users/smy/project/book-agent/.forge/reports/batch-56-report.md`
