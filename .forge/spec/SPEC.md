# Runtime Self-Heal Closure Spec

## North Star

Book Agent should behave like a runtime-owned self-healing system rather than a workflow that only
records failures for humans to babysit.

When translation, review, export, or packet-scoped runtime work fails, the system should:

- classify the failure deterministically
- derive a bounded repair plan
- create repair dispatch lineage
- bind that dispatch to a claimable `REPAIR` work item
- route repair execution through worker, adapter, executor, and transport contracts
- interpret non-default repair decisions such as `retry_later` and
  `manual_escalation_required` deterministically
- publish and replay only after verified repair success
- expose repair blockage truth in the control plane so the runtime can tell whether a lane is
  blocked or ready without deep lineage inspection

## Explicit Requirements

1. The runtime must support bounded self-heal for all three proven lanes:
   - review deadlock
   - export misrouting
   - packet runtime defect
2. Repair execution must remain contract-driven:
   - request contract
   - result contract
   - worker selection
   - executor selection
   - transport selection
3. Repair scheduling must honor decision-aware semantics:
   - `publish_bundle_and_replay`
   - `retry_later`
   - `manual_escalation_required`
4. Manual escalation must be resumable explicitly with deterministic lineage and route overrides.
5. Bounded-lane control-plane surfaces must expose repair blockage truth:
   - `backoff_blocked`
   - `manual_escalation_waiting`
   - `ready_to_continue`
6. Forge v2 autonomous execution must classify newly discovered branch work before it can change
   the active mainline.
7. Branch governance must support all of:
   - `mainline_required`
   - `mainline_adjacent`
   - `out_of_band`
8. Accepted, deferred, and rejected branch decisions must be written back to the single live
   `.forge/` ledger so resume never depends on chat reconstruction.
9. Stop legality must be auditable from file truth; a verified batch is a checkpoint, not a stop
   reason.
10. The default Forge v2 init smoke must validate both runtime self-heal behavior and governance
    drift, so resume cannot silently fall back to a weaker autonomy contract.
11. In active takeover mode, a fully green inventory must trigger a continuation scan for the next
    credible `change_request` before the run may stop.
12. When no credible adjacent slice remains, the remaining development surface must be delegated
    explicitly to Forge v2's future `change_request` intake against the same single-ledger
    `.forge/` truth.

## Hidden Requirements

- Resume sessions must not depend on previous chat context.
- The repo must keep a single live `.forge/` ledger; no parallel runtime ledger may be created.
- Published recovery truth must not be accidentally downgraded by transient dispatch updates.
- The next slice must remain obvious from file truth, not from human interpretation.
- Mid-run discovered work must be absorbable without human micromanagement, but also without
  silent scope drift.
- Rejected or deferred branch work must remain visible enough that the next session does not reopen
  the same decision from scratch.
- Governance guarantees should not live only in prose; the default init path should cheaply assert
  that the current ledger and handoff docs still reflect them.
- Inventory completion is not sufficient to end a fully autonomous takeover; the framework must
  search local truth for the next credible adjacent hardening slice first.
- The default init smoke should not emit known framework-noise warnings that obscure real
  regressions, including deprecated lifecycle hooks or sqlite cleanup leakage.
- Those warning-hygiene guarantees must be enforced by the default init path itself rather than by
  ad hoc follow-up probe commands in reports or state notes.
- Governance validation must also prove that this warning-hygiene gate remains wired into the
  default init path, so the guarantee cannot silently fall back to report-only status.
- Governance validation must also prove that the latest verified batch/report artifacts actually
  exist and that the explicit `mainline_complete` checkpoint fields in `.forge/STATE.md` remain
  aligned with that checkpoint.
- Governance validation should derive the latest verified checkpoint markers from `.forge` truth
  itself instead of hardcoding the newest batch/report/feature ids, so each new verified checkpoint
  does not require another validator self-patch just to stay covered.
- Governance validation should also avoid hardcoding the current smoke test count; it should verify
  the default `bash .forge/init.sh` contract and its expected post-smoke validators instead.
- Governance validation should also avoid hardcoding the current checkpoint shape; it should derive
  the active `current_step` and `active_batch` from `.forge/STATE.md` and verify handoff truth
  against those live values.
- Governance validation should also derive and validate the rest of the STATE checkpoint tuple,
  especially `authoritative_batch_contract` and `expected_report_path`, so state pointers remain
  semantically aligned rather than only syntactically present.
- When the current inventory is complete, handoff truth should still say who owns future work:
  Forge v2 must remain the delegated intake path for subsequent change requests instead of leaving
  the repo in an ambiguous "done for now" state.

## Constraints

- Work in the single shared checkout.
- Do not reset or discard unrelated in-flight changes.
- Keep replay bounded to the failed scope.
- Preserve audit lineage across incident, proposal, dispatch, validation, bundle publication, and
  replay.
- Prefer representative runtime verification over purely local confidence.
- Keep framework governance changes inside the same `.forge/` truth model; do not solve autonomy by
  creating parallel planning artifacts.
- Keep governance validation lightweight enough for routine resume use.
- Keep post-completion continuation scanning grounded in local repo truth rather than open-ended
  ideation.

## Chosen Problem Framing

This repo is no longer primarily blocked on UI polish or additional transport variants.

The mainline problem is runtime self-heal closure:

- the control plane already exists
- the repair lane already exists
- the routing contracts already exist
- the remaining work is to make the runtime increasingly self-deciding, observable, resumable, and
  governable when new branch work appears mid-run

## Chosen Solution Topology

The active topology is:

1. runtime lane health detects a bounded failure
2. incident triage classifies the failure
3. runtime repair planner emits a bounded repair plan
4. incident controller seeds repair dispatch plus claimable `REPAIR` work item
5. runtime repair execution runs through worker / adapter / executor / transport contracts
6. validation and bundle publication happen only after successful repair execution
7. replay stays bounded to the failed scope
8. control-plane surfaces mirror repair blockage truth back to runtime-facing callers
9. after every verified slice, Forge v2 classifies any newly discovered branch work, rewrites the
   single live ledger if needed, and only then continues or stops legally

## User And Operator Flows

### Runtime-Owned Happy Path

1. A bounded lane fails.
2. The runtime opens or updates an incident.
3. A repair proposal and repair dispatch are created.
4. The `REPAIR` lane executes a bounded repair.
5. Validation passes, the bundle is published, and bounded replay occurs.
6. The control plane shows published recovery state.

### Retry-Later Path

1. Repair execution returns `retry_later`.
2. The dispatch records retry timing and blockage state.
3. The scheduler/claim layer leaves the work item non-claimable until backoff elapses.
4. The control plane shows `backoff_blocked`.
5. Once backoff elapses or a resume occurs, the lane becomes `ready_to_continue`.

### Manual Escalation Path

1. Repair execution returns `manual_escalation_required`.
2. The work item becomes terminal and non-claimable by default.
3. The control plane shows `manual_escalation_waiting`.
4. A human explicitly resumes the dispatch, optionally with routing overrides.
5. The lane returns to `ready_to_continue`.

### Branch Intake Path

1. A verified slice exposes new work or a new requirement edge.
2. Forge v2 classifies it as `mainline_required`, `mainline_adjacent`, or `out_of_band`.
3. If it is `mainline_required`, the active mainline and acceptance truth are rewritten before the
   next dispatch.
4. If it is `mainline_adjacent`, the branch is recorded and allowed to compete through the fork
   rule.
5. If it is `out_of_band`, the branch is explicitly deferred or rejected in file truth.

### Stop-Legality Path

1. A slice reaches verified state.
2. Forge v2 resolves any discovered branch candidates into file truth.
3. Forge v2 determines whether a next dependency-closed slice exists.
4. The run may stop only for a real blocker, an explicit user decision, an explicit framework turn
   boundary, or an explicit no-next-slice state recorded in `.forge/`.

## Edge Cases And Failure Modes

- malformed remote repair result contracts must fail deterministically
- unknown worker, executor, or transport hints must fail inside the bounded repair lifecycle
- published recovery state must remain stable even while transient dispatch state changes
- resumed repairs must refresh request-contract input bundles before re-entering the lane
- duplicate reseed must remain blocked for manual-escalation terminal repair items
- newly discovered branch work must not be left only in a summary or chat transcript
- a clean checkpoint must not become an illegal soft stop simply because the next slice is obvious

## Non-Goals

- broad UI polish unrelated to runtime self-heal closure
- decorative framework ceremony
- opening a second live execution ledger
- widening replay beyond the failed bounded scope

## Success Criteria

The system is considered successful when:

- the three bounded repair lanes remain verified
- decision-aware scheduling and explicit resume semantics remain verified
- bounded-lane control-plane surfaces expose blockage truth without deep lineage inspection
- Forge v2 resume sessions can restart from `.forge/STATE.md`, `.forge/spec/SPEC.md`,
  `.forge/spec/FEATURES.json`, and `.forge/init.sh` without relying on chat memory
- newly discovered branch work is classified and reflected in file truth before it changes the run
- legal stops are explainable from file truth alone
- the default init smoke validates that the active governance contract still exists across skill,
  spec, decision, and handoff artifacts
- a fully green inventory still triggers a continuation scan for the next credible change request
  before stop legality may be claimed
- the default init smoke remains free of the previously known FastAPI lifecycle and sqlite
  unclosed-database warning noise
- the default init smoke fails as soon as those known warning classes reappear
- governance validation proves that the warning-hygiene gate and its forbidden-warning patterns are
  still part of the default init contract
- governance validation proves the active latest verified checkpoint still exists on disk and that
  `.forge/STATE.md` remains aligned to the explicit mainline-complete checkpoint state
- governance validation discovers the active latest verified checkpoint dynamically from `.forge`
  truth instead of depending on stale hardcoded marker ids
- governance validation does not depend on a stale hardcoded `Ran N tests` marker for the default
  smoke surface
- file truth explicitly delegates any remaining or future development work to Forge v2's
  `change_request` protocol once the current inventory is complete
- governance validation does not depend on a stale hardcoded `mainline_complete / active_batch none`
  assumption for the current checkpoint shape
- governance validation validates the full STATE checkpoint tuple, not just a subset of the visible
  state fields

## Initial Delivery Strategy

1. Preserve the verified runtime self-heal mainline through batch-42.
2. Bootstrap Forge v2 spec/features/init artifacts from verified truth.
3. Continue by selecting the next failing feature from `FEATURES.json`.
4. Keep each subsequent slice dependency-closed, verified, and ledger-backed.
5. When new branch work appears, classify it, rewrite the ledger transactionally, and only then
   continue or stop.
6. Keep the default init smoke current enough to validate both runtime behavior and governance.
7. After inventory completion in active takeover mode, scan local truth for the next credible
   change request before declaring the run complete.
