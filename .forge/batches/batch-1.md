# Forge Batch 1

objective:
- Build the next `release-ready lane health` slice for Chapter Workbench.
- Convert the current pressure / confidence / drift signals into a more direct lane-health readout for reviewer/operator decision-making.

owned_files:
- /Users/smy/project/book-agent/frontend/src/features/workspace/WorkspacePage.tsx
- /Users/smy/project/book-agent/frontend/src/features/workspace/WorkspacePage.test.tsx
- /Users/smy/project/book-agent/docs/mainline-progress.md

dependencies:
- Existing release-ready lane signals are already present and verified:
  - pressure
  - stay-or-switch judgment
  - confidence
  - drift
- No backend dependency is required for this batch.

acceptance_target:
- In release-ready view, reviewer/operator can read a higher-level lane-health summary without scanning multiple cards first.
- The new readout must reinforce the current mainline, not reopen unrelated UI surfaces.

verification_command:
- cd /Users/smy/project/book-agent/frontend && npx vitest run src/features/workspace/WorkspacePage.test.tsx src/app/App.test.tsx
- cd /Users/smy/project/book-agent/frontend && npm run build

stop_condition:
- A single, clearer lane-health readout exists in the release-ready operator surface.
- Tests and build pass.
- `docs/mainline-progress.md` is updated to reflect the new mainline state.

expected_report_path:
- /Users/smy/project/book-agent/.forge/reports/batch-1-report.md
