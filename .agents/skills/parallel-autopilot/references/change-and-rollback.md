# Change Request And Rollback

## Resume

On resume, read:

- `PROGRESS.md`
- `DECISIONS.md`
- `WORK_GRAPH.json`
- `LANE_STATE.json`

Resume output must identify:

- current mode
- current wave
- active lane
- blocked lanes
- next executable nodes

## Change Request

A change request must not be silently folded into the current lane.

Always:

1. pause current planning/execution context
2. identify impacted nodes
3. preserve unaffected nodes
4. invalidate affected subgraph
5. rebuild waves and lanes
6. update state artifacts

## Rollback

Rollback in MVP means rollback of plan validity, not automatic code reversal.

Always output:

- rollback trigger
- rollback start point
- invalidated nodes
- preserved nodes
- re-entry point

## Bug-Driven Evolution

Every bug must produce:

1. local fix
2. root-cause classification
3. workflow or protocol hardening

Every recurring workflow pain discovered while using the skill must also produce:

1. a local workaround or current-run fix
2. a skill-gap classification
3. a patch to the reusable skill itself

Do not treat “pain but not a bug” as exempt from evolution.

Root-cause classes:

- implementation gap
- dependency analysis gap
- contract ownership gap
- state model gap
- checkpoint coverage gap

Skill-gap classes:

- intake gap
- lane policy gap
- state artifact gap
- merge gate gap
- rollback / change-request gap
- operator ergonomics gap

Do not leave bug fixes isolated from framework evolution.
Do not leave repeated operator pain isolated from skill evolution.
