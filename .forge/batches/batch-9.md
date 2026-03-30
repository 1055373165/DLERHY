# Forge Batch 9

objective:
- Turn the new summary-level release-ready guidance into an actionable high-level route cue.
- Let reviewer/operator enter the right lane directly from `Active Scope / Session Digest`, without first scrolling down to the Operator Lens controls.

owned_files:
- /Users/smy/project/book-agent/progress.txt
- /Users/smy/project/book-agent/frontend/src/features/workspace/WorkspacePage.tsx
- /Users/smy/project/book-agent/frontend/src/features/workspace/WorkspacePage.test.tsx
- /Users/smy/project/book-agent/docs/mainline-progress.md

dependencies:
- Forge batch-8 is already verified complete.
- Existing release-ready surfaces already expose:
  - high-level summary guidance in `Active Scope / Session Digest`
  - `Lens 选择预判`
  - `子队列入口预判`
  - Operator Lens entry routing

acceptance_target:
- In release-ready flow, reviewer/operator can execute the summary-level recommendation directly from `Active Scope / Session Digest`.
- The cue should reduce the need to scroll to Operator Lens before taking the first routing action.

verification_command:
- cd /Users/smy/project/book-agent/frontend && npx vitest run src/features/workspace/WorkspacePage.test.tsx src/app/App.test.tsx
- cd /Users/smy/project/book-agent/frontend && npm run build

stop_condition:
- Summary-level route cue is actionable before Operator Lens controls.
- Tests and build pass.
- `docs/mainline-progress.md` and `progress.txt` are updated to reflect the new mainline state.

expected_report_path:
- /Users/smy/project/book-agent/.forge/reports/batch-9-report.md
