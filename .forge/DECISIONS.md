# Forge Decisions

1. Workspace decision
- Stay in the single main repo checkout on `main`.
- Do not create a second live worktree.

2. Mainline decision
- Current mainline is `Memory Governance + Chapter Workbench productization`.
- Runtime V2 control-plane work is treated as stable unless a real blocker appears.

3. Scope control decision
- Prioritize changes that directly improve reviewer/operator closed-loop efficiency.
- Push non-blocking polish into `docs/optimization-backlog.md`, not the mainline.

4. Current write-set decision
- Prefer staying inside:
  - `frontend/src/features/workspace/WorkspacePage.tsx`
  - `frontend/src/features/workspace/WorkspacePage.test.tsx`
  - `docs/mainline-progress.md`
- Expand only if blocked by a real dependency.

5. Verification decision
- Every batch must pass:
  - `cd /Users/smy/project/book-agent/frontend && npx vitest run src/features/workspace/WorkspacePage.test.tsx src/app/App.test.tsx`
  - `cd /Users/smy/project/book-agent/frontend && npm run build`

6. Immediate next-slice decision
- The next dependency-closed slice is `release-ready pre-entry routing`.
- Goal: let the verified route-first layer influence subqueue choice before entry, so reviewer/operator can avoid entering the wrong release-ready lane in the first place.
