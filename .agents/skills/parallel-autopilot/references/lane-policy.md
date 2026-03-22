# Lane Policy

## Why Lanes

Do not use phase-level or MDU-level parallelism as the primary unit.

- phases are too coarse
- MDUs are too fine
- lanes are the right unit for a controlled slice of work

## Lane Definition

A lane is a bounded execution container with:

- one objective
- explicit dependencies
- explicit write set
- explicit contract tags
- one integration gate

For quality / intelligence lanes, a contract-lock MDU is not optional.

Before scaffold implementation begins, freeze:

- benchmark samples or focused regression families
- allowed automatic interventions
- protected contracts that must not silently change
- explicit non-goals that prevent the lane from expanding into freeform behavior

## Parallel Candidate Rules

Two tasks may be placed in separate lanes only if they do not share:

- file writes
- module ownership
- schema ownership
- public interface ownership
- state protocol ownership
- unstable shared test scaffolding

## Must-Serialize Rules

Always serialize:

- requirement lock
- ADR freeze
- work-graph generation
- lane partitioning
- contract upgrades
- merge
- rollback decisions
- phase checkpoints

## False Parallelism Smells

Treat the plan as bad if:

- two lanes touch the same shared module
- one lane changes an interface while another consumes it
- one lane rewrites shared test helpers while another depends on them
- lane definitions only differ by file names, not by ownership boundaries

## MVP Recommendation

For MVP:

- lanes may be planned as parallel candidates
- actual execution can remain serial under one controller
- the goal is safe orchestration, not artificial concurrency

## Single-Lane-First Planning Rule

Lane-aware planning is allowed to conclude that the first implementation wave should not be split yet.

Choose single-lane-first when:

- the first implementation still shares one unstable contract chain
- the write set spans parser/bootstrap/review/export re-entry
- acceptance evidence depends on one focused fail-closed path rather than independent module slices

Do not force placeholder parallel lanes just to preserve the appearance of parallel autopilot.

Before the first implementation lane opens:

- freeze the exact owned write set, not just the objective
- list protected downstream consumers that are explicitly out of lane
- freeze acceptance evidence families that prove the lane is safe to open

If downstream gates are still protected contracts, treat them as consumers first, not as automatic co-owners of the first lane.

For qualitative lanes such as review quality, naturalness, summarization quality, or ranking intelligence:

- do not jump from high-level intent straight into implementation
- first lock the benchmark set, evaluation boundary, and intervention model
- only then enter the scaffold MDU
- the first scaffold should prefer additive observability and narrow guidance on existing paths
- do not start with schema expansion or freeform rewrite power unless the locked contract explicitly requires them
- before lane closure, consolidate the frozen benchmarks into a dedicated acceptance artifact instead of leaving the evidence scattered across legacy tests
