# State Artifacts

Structured state is the source of truth. Markdown is the operator mirror.

## Required In MVP

### `PROGRESS.md`

Human-readable execution summary.

Add these fields:

- current wave
- active lanes
- blocked lanes
- current integration gate

### `DECISIONS.md`

ADR ledger. Do not turn it into scheduler state.

### `WORK_GRAPH.json`

Scheduler truth source.

Minimum fields:

- `nodes`
- `edges`
- `waves`
- `contracts`
- `write_sets`

### `LANE_STATE.json`

Runtime lane lifecycle store.

Minimum fields:

- `lane_id`
- `wave_id`
- `status`
- `depends_on`
- `write_set`
- `contract_tags`
- `blocked_by`
- `current_mdu`
- `checkpoint_ref`

### `RUN_CONTEXT.md`

Operator-facing current snapshot:

- current mode
- current wave
- current lane
- current blockers
- next action

### Lane-Scoped Contract Doc

When a lane starts with a contract-locking MDU, add one lane-scoped document under `docs/`.

It should lock:

- the lane objective
- the artifact contract
- non-goals
- gate rules
- the immediate next implementation consequence

## Recommended After MVP

### `MERGE_QUEUE.json`

Use when merges need explicit queueing, gate status, and ordering.

### `CONTRACT_MAP.json`

Use when multiple lanes consume shared interfaces and version broadcasts matter.

## Consistency Rule

Always update structured state first, then mirror summary changes into Markdown.

Never allow `PROGRESS.md` to drift from `WORK_GRAPH.json` / `LANE_STATE.json`.

## Phase Completion Handoff Rule

When a phase checkpoint is closed and no next phase is locked yet:

- do not leave a stale `current_mdu` or stale active lane in operator-facing mirrors
- explicitly mark the run as waiting for the next goal / phase lock
- `current_wave_id` may be `null` in `LANE_STATE.json` once no wave is active
- `RUN_CONTEXT.md` should point to the next control-plane action, not the last completed MDU

Do not make operators infer “phase complete” from scattered done statuses.

## Same-Goal Planning Handoff Rule

When a kickoff / baseline phase is complete but the overall goal is still the same:

- do not collapse the operator mirrors into a generic “waiting for next goal lock” state
- explicitly point to the next planning action under the same locked goal
- keep `current_wave_id = null` until a real implementation wave is created

Finishing a baseline phase is not the same thing as abandoning the goal it locked.

## Implementation Entry Handoff Rule

When an implementation-planning phase is complete and the next step is a real executable lane:

- do not stop at a frozen contract with no claimable entry
- create the next `wave_id`
- create a ready-to-claim lane entry in `LANE_STATE.json`
- point `current_mdu` to the first executable implementation MDU
- update operator mirrors so the next action is the real lane claim, not more planning prose

Use `ready` lane state before the first claim if execution has not started yet.

## Phase Reopen Reset Rule

When the next phase is being reopened but lane planning has not started yet:

- do not keep the previous phase's `recommended_claim_order` as if it still applied
- clear or replace stale lane entries in `LANE_STATE.json` unless they are explicitly re-declared for the new phase
- point `RUN_CONTEXT.md` to the new control-plane entry MDU, not to the last closed checkpoint

Historical lane evidence belongs in `WORK_GRAPH.json`, dedicated docs, or acceptance artifacts, not in a stale live lane list.

## Resume Compatibility Rule

When resuming from older runs or historical artifacts:

- explicitly identify artifact-generation gaps
- do not assume all reports or state files carry the latest fields
- classify missing state as `legacy artifact generation`, not as implicit success or implicit absence

If compatibility gaps matter for execution, write them into the current run baseline instead of hiding them in operator memory.
