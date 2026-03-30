# Forge Batch 7

objective:
- Move decisive `release-ready` route-first judgment up into a `Lens 选择预判` before the operator actually clicks a specific Operator Lens button.
- Reduce wrong-lens entry by steering reviewer/operator toward the best lane before subqueue-level handling begins.

owned_files:
- /Users/smy/project/book-agent/progress.txt
- /Users/smy/project/book-agent/frontend/src/features/workspace/WorkspacePage.tsx
- /Users/smy/project/book-agent/frontend/src/features/workspace/WorkspacePage.test.tsx
- /Users/smy/project/book-agent/docs/mainline-progress.md

dependencies:
- Forge batch-6 is already verified complete.
- Existing release-ready surfaces already expose:
  - `子队列入口预判`
  - Operator Lens entry routing
  - route-first decisions

acceptance_target:
- In release-ready flow, reviewer/operator gets a higher-level `Lens 选择预判` before choosing a specific Operator Lens lane.
- The cue should make the best lane choice clearer before subqueue entry.

verification_command:
- cd /Users/smy/project/book-agent/frontend && npx vitest run src/features/workspace/WorkspacePage.test.tsx src/app/App.test.tsx
- cd /Users/smy/project/book-agent/frontend && npm run build

stop_condition:
- Route-first release-ready judgment appears before Operator Lens lane selection.
- Tests and build pass.
- `docs/mainline-progress.md` is updated to reflect the new mainline state.

expected_report_path:
- /Users/smy/project/book-agent/.forge/reports/batch-7-report.md
