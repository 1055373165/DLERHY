---
name: codex-forge
description: >-
  Lightweight full-cycle development scaffold for Codex. Designed for long-running
  feature work without autopilot-style ceremony overload. Keeps requirement lock,
  ADR freeze, batch execution, verification, and liveness recovery, but uses a
  single truth source and faster dispatch/repair loops.
---

# Codex Forge

Codex Forge is the successor to the old `autopilot/` framework.

It keeps the parts that proved valuable in real runs:

- requirement lock
- architecture and ADR freeze
- dependency-aware batching
- master-side verification
- explicit test gates
- liveness recovery
- framework self-evolution

It removes the parts that repeatedly slowed execution:

- over-segmented phase ceremony
- multiple partially redundant truth sources
- passive waiting during stale execution
- pre-implementation spikes by default
- broad protocol overhead for small or medium batches

## Core Position

This framework is optimized for Codex operating inside a shared repo, not for a hypothetical
workflow engine.

The control loop is:

1. Lock the requirement.
2. Freeze the few decisions that actually constrain implementation.
3. Build the thinnest useful plan.
4. Dispatch a dependency-closed batch.
5. Verify independently.
6. Adapt immediately if reality diverges.
7. Checkpoint by working slice, not by ceremony.

## Truth Model

Forge uses one live state directory:

- `.forge/STATE.md` — authoritative run state
- `.forge/DECISIONS.md` — active ADRs and constraints
- `.forge/batches/` — batch contracts
- `.forge/reports/` — delivery reports
- `.forge/log.md` — append-only session and recovery log

Do not create a second live state ledger.

The repo code plus `.forge/` are the truth. Chat is not the truth.

## Workspace Rule

Forge defaults to a single live git working tree for personal development.

- Do not create a second worktree by default.
- Do not split the same active run across two local worktrees.
- Prefer working directly in the main repo checkout, even for long-running feature work.
- If temporary isolation is needed, use normal commits and branches inside the same checkout first.

Creating an additional worktree is an exception-only move and requires an explicit user request.

## Dispatch And Harvest Contract

Dispatch is not complete when the worker is spawned.

After dispatching a batch, Forge must record in `.forge/STATE.md`:

- the authoritative batch contract path
- the expected report path
- the active worker id and nickname when available
- the worker model and reasoning setting when available
- the dispatch time
- the last harvest check time

After that, master must do one of two things immediately:

1. block on the active worker with a completion wait, or
2. run a harvest tick loop that keeps checking worker status plus file truth

Worker UI state or completion notification is only a trigger.
The report path and owned-file truth remain authoritative.

## Continue-Until-Blocked Rule

Forge must not stop after a successful batch just because a good reporting checkpoint exists.

After a batch is verified, Forge must immediately do one of the following:

1. freeze the next dependency-closed batch and continue execution, or
2. enter an explicit blocked state with a concrete blocker

Valid blockers are narrow and real:

- missing permission that cannot be worked around
- ambiguous requirement with structural consequences
- unresolved safety boundary
- missing external dependency or credential that cannot be inferred or substituted

These are not valid blockers:

- a convenient status-reporting moment
- "waiting for user confirmation" when no real decision is needed
- finishing one neat slice and pausing by habit
- framework ceremony that does not change the next action

If the next slice is clear, Forge must keep going.

User updates are progress visibility, not implicit stop signs.

## Working Modes

Classify every run into one of four modes:

1. `new_run`
2. `resume`
3. `change_request`
4. `recovery`

If the mode is unclear, resolve that first.

## Default Lifecycle

### Step 1 — Requirement Lock

Write a concise locked requirement:

- what must be delivered
- what is explicitly out of scope
- acceptance cases
- failure boundaries

Stop only if the requirement is ambiguous in a way that would cause structural waste.

### Step 2 — Decision Freeze

Freeze only the decisions that matter:

- architecture boundary
- ownership boundary
- test strategy
- rollback boundary
- safety contract

Do not generate decorative ADRs.

### Step 3 — Lean Plan

Decompose into the minimum number of independently verifiable batches.

Batch by:

- dependency order
- write-set separation
- verification boundary

Do not batch by aesthetic symmetry.

### Step 4 — Execute

Each batch must have:

- owned files
- verification command
- acceptance target
- explicit stop condition

### Step 5 — Verify

Never trust worker self-report alone.

Master must independently verify at least:

- key file existence
- one representative test command
- state ledger reconciliation

### Step 6 — Adapt

If execution reality differs from the plan:

- split
- merge
- rescope
- backtrack

Adapt immediately. Do not leave the fix in chat notes only.

## Branch Resolution Rule

Requirement or implementation forks are first-class runtime events.

When Forge sees two plausible next directions:

1. compare both directions against the active mainline
2. choose the one that is closest to the mainline as the next delivery slice
3. evaluate the non-chosen direction for mainline impact
4. if that impact is high, update the mainline immediately
5. if that impact is not high, move it to backlog or a later slice
6. record the resolution in `.forge/DECISIONS.md`, `.forge/log.md`, and the mainline progress doc
7. continue dispatch from the updated mainline instead of waiting for human nudging

This is a hard rule, not an optional style preference.

Forge must never leave a fork unresolved in chat alone.

If a competing direction is important enough to change the real delivery path, Forge must mutate the
mainline and continue from that new truth.

## Liveness Rule

Forge must never idle in an “executing” state without evidence of progress.

If a batch is running and all of the following are true:

- no report exists
- no owned-file movement is visible
- no checkpoint/log update appears

then the run is stale and must enter recovery immediately.

Recovery output must produce:

- a new batch contract version
- a log entry
- a single authoritative path forward

If a worker is shown as completed or "awaiting instruction" but the report is still missing,
Forge must not idle. That is a stale completion and must enter harvest-or-recovery immediately.

## Test Rule

Tests are part of execution, not part of self-congratulation.

Every batch must define:

- what gets tested
- exact command
- expected evidence

If a batch cannot be automatically tested, the manual verification steps must be short,
specific, and independently checkable.

## Framework Evolution Rule

When the framework itself causes delay, ambiguity, or empty looping:

1. record the pain
2. update Forge itself
3. continue the run with the improved protocol

Do not preserve a bad framework just because it is already written.

## Non-Goals

Forge is not:

- a workflow engine
- a swarm orchestrator
- a substitute for engineering judgment
- a ceremony generator

It is a practical control scaffold for getting real development done end-to-end.
