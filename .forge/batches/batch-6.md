# Forge Batch 6

objective:
- Move decisive `release-ready` route-first judgment into a pre-entry cue before the lane is actually selected.
- Reduce wrong-lane scans by letting reviewer/operator act before entering a release-ready subqueue.

owned_files:
- /Users/smy/project/book-agent/frontend/src/features/workspace/WorkspacePage.tsx
- /Users/smy/project/book-agent/frontend/src/features/workspace/WorkspacePage.test.tsx
- /Users/smy/project/book-agent/docs/mainline-progress.md

dependencies:
- Forge batch-5 is already verified complete.
- Existing release-ready surfaces already expose:
  - Operator Lens entry routing
  - route-first decisions
  - collapsed support signals

acceptance_target:
- In release-ready Operator Lens flow, reviewer/operator gets a pre-entry route cue before selecting the subqueue.
- The pre-entry cue should reduce unnecessary lane entry and lane switching.

verification_command:
- cd /Users/smy/project/book-agent/frontend && npx vitest run src/features/workspace/WorkspacePage.test.tsx src/app/App.test.tsx
- cd /Users/smy/project/book-agent/frontend && npm run build

stop_condition:
- Route-first release-ready judgment appears before subqueue entry.
- Tests and build pass.
- `docs/mainline-progress.md` is updated to reflect the new mainline state.

expected_report_path:
- /Users/smy/project/book-agent/.forge/reports/batch-6-report.md
