# Forge Batch 5

objective:
- Move decisive `release-ready` route-first judgment into the Operator Lens entry layer.
- Let reviewer/operator act at lane entry instead of after reading mid-page release-ready cards.

owned_files:
- /Users/smy/project/book-agent/frontend/src/features/workspace/WorkspacePage.tsx
- /Users/smy/project/book-agent/frontend/src/features/workspace/WorkspacePage.test.tsx
- /Users/smy/project/book-agent/docs/mainline-progress.md

dependencies:
- Forge batch-4 is already verified complete.
- Existing release-ready surfaces already expose:
  - lane health
  - routing cue
  - collapsed support signals

acceptance_target:
- In release-ready Operator Lens flow, reviewer/operator can act from an explicit lane-entry decision card.
- The entry card should reduce the need to scan deeper workbench cards before deciding to stay or switch lanes.

verification_command:
- cd /Users/smy/project/book-agent/frontend && npx vitest run src/features/workspace/WorkspacePage.test.tsx src/app/App.test.tsx
- cd /Users/smy/project/book-agent/frontend && npm run build

stop_condition:
- Route-first release-ready judgment is visible at Operator Lens entry.
- Tests and build pass.
- `docs/mainline-progress.md` is updated to reflect the new mainline state.

expected_report_path:
- /Users/smy/project/book-agent/.forge/reports/batch-5-report.md
