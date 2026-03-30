# Forge Batch 2

objective:
- Make release-ready `lane health` a stronger top-line routing cue for reviewer/operator.
- Surface a single route-first recommendation before supporting pressure / confidence / drift detail.

owned_files:
- /Users/smy/project/book-agent/frontend/src/features/workspace/WorkspacePage.tsx
- /Users/smy/project/book-agent/frontend/src/features/workspace/WorkspacePage.test.tsx
- /Users/smy/project/book-agent/docs/mainline-progress.md

dependencies:
- Forge batch-1 is already verified complete.
- Existing release-ready surfaces already expose:
  - lane health
  - pressure suggestion
  - confidence
  - drift
  - exit strategy

acceptance_target:
- In release-ready view, reviewer/operator sees a stronger top-line route cue in queue rail / Operator Lens / Session Digest.
- The route cue must say what to do next before the reviewer scans lower-level support cards.

verification_command:
- cd /Users/smy/project/book-agent/frontend && npx vitest run src/features/workspace/WorkspacePage.test.tsx src/app/App.test.tsx
- cd /Users/smy/project/book-agent/frontend && npm run build

stop_condition:
- A stronger route-first cue exists in release-ready view.
- Tests and build pass.
- `docs/mainline-progress.md` is updated to reflect the new mainline state.

expected_report_path:
- /Users/smy/project/book-agent/.forge/reports/batch-2-report.md
