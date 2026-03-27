# Codex Forge — Runbook

## When To Use Forge

Use Forge when the task:

- spans multiple files
- needs state across sessions
- benefits from batch verification
- carries real rollback or safety risk

Do not use Forge for:

- tiny one-file fixes
- casual experiments
- purely conversational planning

## Recommended Defaults

- Start lean
- Verify aggressively
- Recover early
- Keep batches small when write sets overlap
- Keep one live repo checkout and one live worktree

## Workspace Policy

For personal development, Forge should stay inside a single git working tree.

- Do not create a second worktree just to isolate a feature round.
- Do not keep parallel live worktrees for the same repo by default.
- Prefer a single checkout plus normal commits on the active branch.

Only create another worktree when the user explicitly asks for it.

## Good Default Shape

1. lock requirement
2. freeze 3-5 key decisions
3. create 1-3 immediate batches
4. dispatch first batch
5. verify
6. continue

## Signals To Tighten Control

- repeated test failures
- liveness stalls
- worker-complete-without-report situations
- scope drift
- write-set collisions
- hidden dependency chains

When these appear:

- shrink batch size
- increase verification frequency
- sharpen ownership boundaries

## Signals To Loosen Control

- repeated clean batch landings
- small, isolated write sets
- stable test baseline
- predictable implementation size

When these appear:

- merge adjacent tiny batches
- reduce ceremony in planning
- keep operator interruptions minimal

## The Main Anti-Patterns

- waiting without checking file truth
- dispatching a worker without registering an active-worker harvest path
- writing long plans no one executes
- re-reading the whole world every batch
- preserving framework ritual after it stops adding value
- allowing workers to mutate global state without master verification

## Migration Note

Forge replaces the old `autopilot/` directory in the main repo.

It does not automatically rewrite historical `.autopilot/` state in archived or isolated
workspaces. Those remain valid historical artifacts for runs already in progress or already
completed.

## Supervisor

For unattended execution, run the real Forge supervisor:

```bash
book-agent forge-supervisor --workspace /path/to/workspace loop
```

See [SUPERVISOR.md](/Users/smy/project/book-agent/forge/SUPERVISOR.md) for the state contract and
harvest/recovery behavior.
