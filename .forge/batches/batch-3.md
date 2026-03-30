# Forge Batch 3

objective:
- Reduce scan cost in decisive `release-ready` views.
- Collapse supporting pressure / confidence / drift detail by default when route-first judgment is already clear.

owned_files:
- /Users/smy/project/book-agent/frontend/src/features/workspace/WorkspacePage.tsx
- /Users/smy/project/book-agent/frontend/src/features/workspace/WorkspacePage.test.tsx
- /Users/smy/project/book-agent/docs/mainline-progress.md

dependencies:
- Forge batch-2 is already verified complete.
- Existing release-ready surfaces already expose:
  - lane health
  - routing cue
  - pressure / confidence / drift detail

acceptance_target:
- In decisive release-ready views, reviewer/operator sees route-first decisions before supporting cards.
- Supporting cards remain available on demand, but no longer dominate the default scan path.

verification_command:
- cd /Users/smy/project/book-agent/frontend && npx vitest run src/features/workspace/WorkspacePage.test.tsx src/app/App.test.tsx
- cd /Users/smy/project/book-agent/frontend && npm run build

stop_condition:
- Route-first decision remains visible in queue rail / Operator Lens / Session Digest.
- Supporting signals collapse by default in decisive release-ready views and can still be expanded.
- Tests and build pass.
- `docs/mainline-progress.md` is updated to reflect the new mainline state.

expected_report_path:
- /Users/smy/project/book-agent/.forge/reports/batch-3-report.md
