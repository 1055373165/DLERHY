# Runtime Protocol

Use this protocol when the user wants the workflow itself, not just a concept note.

## Execution Modes

Always determine one mode first:

- `new_run`
- `resume`
- `change_request`
- `rollback_recovery`

## Serial Control Plane

These steps remain serial:

1. Requirement lock
2. ADR freeze
3. Spike decision
4. Work-graph generation
5. Lane partitioning
6. Merge gate
7. Phase checkpoint

## Selectively Parallel Execution Plane

Only after the serial control plane is stable may work be marked as lane candidates.

Recommended sequence:

1. Lock requirement
2. Freeze decisions
3. Run necessary spikes
4. Build work graph
5. Assign waves
6. Assign lanes
7. Execute lane-local MDUs
8. Serialize merge
9. Run review gate
10. Run checkpoint
11. Update state

Before a lane is declared complete, convert any narrative acceptance target into an executable proof artifact:

- a focused regression
- a threshold-evaluation helper
- or another repeatable acceptance check stored in the repo

Before a phase integration gate is declared complete, aggregate the accepted lane evidence into an explicit integration matrix:

- record the lane-local acceptance artifacts or canonical test IDs
- add at least one cross-lane coherence check or regression
- store the matrix in a dedicated helper, acceptance test, or equivalent repo artifact

Do not rely on operator memory to reconstruct which lane evidence satisfied the gate.

When a lane adds an artifact or capability without replacing an existing contract, also run at least one preserved-contract regression from the adjacent stable path:

- keep the new-path regression and the preserved-contract regression separate
- if the preserved-contract regression fails because the old assertion is stale but the current behavior is already the accepted contract, normalize the baseline in the same MDU instead of misclassifying it as new breakage

When the lane contract includes source guards or fail-closed boundaries, lane closure must also include at least one negative-path regression:

- source guard rejection
- renderer / dependency unavailable failure
- or another explicit boundary condition from the locked contract

## Minimum Output At Each Stage

### Requirement Lock

- `locked_requirement`
- unresolved boundary list

Do not treat requirement lock as complete if it only captures the goal statement.

Before the next MDU is claimed, a control-plane requirement lock should also freeze:

- in-scope input classes
- protected contracts that must not silently change
- the minimum acceptable output shape for the next implementation step
- the explicit ready-for-next-MDU gate

For risk-sensitive document-intelligence work, do not enter implementation after requirement lock alone.

Freeze a baseline contract first:

- explicit risk buckets
- routing semantics for each bucket
- and fail-closed fallback behavior

### ADR Freeze

- key decisions
- non-goals
- reversal conditions

### Work Graph Build

- nodes
- dependencies
- write sets
- contract tags

### Lane Planning

- lane list
- wave grouping
- blocked nodes

### Checkpoint

- accepted nodes
- stale nodes
- next wave
- executable acceptance evidence for any lane being closed
- an explicit integration matrix for any completed phase gate

## MVP Guidance

If the user asks for the smallest viable implementation:

- plan lane-aware execution
- do not promise true concurrent code writing
- keep merge serialized
- keep conflict resolution manual or planner-driven
