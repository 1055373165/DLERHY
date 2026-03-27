---
name: parallel-autopilot
description: Turn one development requirement into a natural-language, lane-aware development workflow with requirement lock, ADR freeze, work-graph planning, selectively parallel lane scheduling, and state artifacts. Use when designing, upgrading, or operating an autopilot-style software delivery workflow without relying on slash commands or blind full parallelism.
---

# Parallel Autopilot

Use this skill when the user wants a development workflow to be automatically taken over from a natural-language requirement, especially when:

- upgrading a command-driven autopilot flow into a natural-language skill
- introducing selectively parallel development without turning everything into swarm chaos
- preserving ADR, progress tracking, rollback, change requests, and bug-driven evolution
- planning or operating lane-aware software delivery

Do not use this skill for ordinary feature implementation unless the user is explicitly asking for workflow/orchestration design or for an autopilot-style development takeover.

## Core Position

This skill is not "make everything parallel."

It assumes:

- the control plane stays serial
- only the execution plane may become selectively parallel
- requirement lock, ADR freeze, merge, rollback decisions, and phase checkpoints remain serialized
- structured state is the source of truth; Markdown is the operator view
- for personal development, one repo checkout and one worktree are the default

Do not propose or create multiple live worktrees unless the user explicitly asks for that setup.

## What To Produce

Default outputs should include:

- a locked requirement
- an ADR baseline
- a work graph
- lane and wave allocation
- state artifact definitions
- merge / review / checkpoint rules
- rollback and change-request behavior

When the user wants implementation planning, produce:

- `PROGRESS.md` updates
- `DECISIONS.md` updates
- `WORK_GRAPH.json` schema or instance
- `LANE_STATE.json` schema or instance
- optional `MERGE_QUEUE.json` and `CONTRACT_MAP.json` if the scope justifies them

## Operating Mode

Always classify the request into one of four modes before designing anything:

1. `new_run`
2. `resume`
3. `change_request`
4. `rollback_recovery`

If the mode is unclear, resolve it first. Do not start lane planning without it.

## Workflow

Follow this sequence:

1. Lock the requirement
2. Freeze key decisions
3. Run only necessary spikes
4. Build the work graph
5. Partition work into waves and lanes
6. Define merge and review gates
7. Define state artifacts
8. Define resume / change / rollback behavior
9. Only then discuss execution order

Never start from "how many agents should run in parallel?"

## Evolution Rule

This skill is expected to evolve from real use, not from theory alone.

When a run exposes:

- repeated operator pain
- missing protocol guidance
- missing state artifacts
- ambiguous lane boundaries
- merge / rollback friction
- recurring manual glue work

do not leave the fix only in local execution notes.

Harden the skill itself by updating one or more of:

- `SKILL.md`
- reference protocols
- templates
- publishable skill-package docs when they exist in the repo

Treat this as part of bug-driven evolution, not optional cleanup.

## Parallelism Rules

Use `lane` as the primary scheduling unit.

Do not schedule work in parallel if it shares any of the following:

- file write set
- module ownership
- schema ownership
- public interface ownership
- state-protocol ownership
- test harness ownership where changes can invalidate each other

Treat these as serial by default:

- requirement lock
- ADR freeze
- work-graph generation
- lane partitioning
- contract upgrades
- merge
- rollback decisions
- phase checkpoints

If true parallel execution is requested, first judge whether the current scope only supports parallel-aware planning. If so, say that explicitly and keep the MVP narrow.

## State Artifacts

Keep `SKILL.md` lean. Read the reference files only as needed:

- For end-to-end lifecycle order, read [references/runtime-protocol.md](references/runtime-protocol.md)
- For state files and truth sources, read [references/state-artifacts.md](references/state-artifacts.md)
- For lane partitioning and conflict rules, read [references/lane-policy.md](references/lane-policy.md)
- For resume, change request, rollback, and bug-driven evolution, read [references/change-and-rollback.md](references/change-and-rollback.md)

Use the templates when the user wants concrete artifacts:

- [templates/WORK_GRAPH.example.json](templates/WORK_GRAPH.example.json)
- [templates/LANE_STATE.example.json](templates/LANE_STATE.example.json)

## MVP Boundary

If the user asks for a first deliverable, default to this MVP:

- natural-language intake
- requirement lock
- ADR freeze
- work-graph build
- lane-aware planning
- lane state tracking
- serialized merge / checkpoint model

Do not default to:

- real concurrent code writing
- autonomous multi-branch merge orchestration
- broad conflict auto-resolution

## Quality Bar

Reject low-quality outputs such as:

- "add a parallel switch"
- "make each MDU parallel"
- vague swarm recommendations
- state-light designs with no truth source
- designs that skip rollback blast radius
- designs that do not specify what must stay serial
- runs that discover reusable workflow pain but do not feed it back into the skill

The final design must be implementable, not inspirational.
