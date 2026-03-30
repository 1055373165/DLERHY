# Forge Batch 6 Report

completed_items:
- Moved decisive `release-ready` route-first judgment into a pre-entry cue before lane selection.
- Added an explicit pre-entry decision card in the Operator Lens area.
- Preserved existing route-first / lane-health / expand-on-demand behavior after entry.

files_changed:
- /Users/smy/project/book-agent/frontend/src/features/workspace/WorkspacePage.tsx
- /Users/smy/project/book-agent/frontend/src/features/workspace/WorkspacePage.test.tsx
- /Users/smy/project/book-agent/docs/mainline-progress.md

verification_commands:
- cd /Users/smy/project/book-agent/frontend && npx vitest run src/features/workspace/WorkspacePage.test.tsx src/app/App.test.tsx
  - result: 16 passed
- cd /Users/smy/project/book-agent/frontend && npm run build
  - result: passed

output_evidence:
- `子队列入口预判` now appears before release-ready subqueue selection.
- Reviewer/operator can trigger `按预判切到 night-shift · 放行候选` before manually entering the release-ready lane.
- Existing release-ready route-first behavior remains green after entry.

scope_deviations:
- none

blockers_or_discovered_work:
- Batch 6 confirmed the next useful slice is pushing route-first guidance up to operator-lens choice itself, not adding more in-lane cards.
