# Forge Batch 3 Report

completed_items:
- Collapsed supporting release-ready signals by default when lane health is already decisive.
- Kept route-first judgment visible in:
  - queue rail `放行链总览`
  - `Operator Lens`
  - `Session Digest`
- Preserved on-demand access to supporting pressure / confidence / drift detail through an explicit expand action.

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
- `当前路线建议` remains the primary actionable card in queue rail.
- `Operator 路线建议` remains the primary actionable card in `Operator Lens`.
- `Release-ready 路线建议` remains visible in `Session Digest` when the release-ready batch summary is active.
- `支持信号已收拢` now appears before lower-level supporting cards in decisive release-ready views.
- Reviewer/operator can explicitly expand support signals when they need pressure / confidence / drift detail.

scope_deviations:
- none

blockers_or_discovered_work:
- Batch 3 confirmed the next useful slice is moving route-first release-ready judgment closer to lane entry, not adding more middle-detail cards.
