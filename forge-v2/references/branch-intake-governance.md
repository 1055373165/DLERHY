# Forge v2 — Branch Intake Governance

This reference governs what happens when new work appears mid-run.

The important distinction is:
not every discovered idea deserves to become the new mainline, but every discovered idea that
changes execution must leave a trace in file truth.

## 1. Why This Exists

Autonomous agents fail here in two opposite ways:

- they ask the user about every branch and lose autonomy
- they absorb every branch and lose the mainline

Forge v2 needs a third behavior:
governed autonomy.

The agent should decide by default, but only through a repeatable intake protocol.

## 2. Branch Classes

Classify discovered work as exactly one of these:

### `mainline_required`

Use this when not doing the work now would make the current run dishonest or unsafe.

Typical signals:

- the current acceptance inventory is missing a requirement exposed by verified repo truth
- the current batch cannot be truthfully verified without the branch
- resume, recovery, or stop-legality would become unreliable if the branch is ignored
- continuing without the branch would create false completion

### `mainline_adjacent`

Use this when the work is close, useful, and likely to matter soon, but the current mainline can
still be completed truthfully without it.

Typical signals:

- it hardens or broadens the mainline but is not required for the current slice to be valid
- it is the likely next fork-rule winner once the current slice closes
- it improves coverage or operator clarity without changing the correctness of the active batch

### `out_of_band`

Use this when the work should not interrupt the active mainline.

Typical signals:

- it belongs to another product thread
- it is optimization or polish without current correctness impact
- it increases scope without strong proximity to the current north star

## 3. Admission Rule

Handle the classes like this:

- `mainline_required`
  - absorb now
  - rewrite the affected spec and acceptance truth
  - version the current or next batch if sequencing changed
- `mainline_adjacent`
  - record it explicitly
  - keep the current batch/mainline unless the fork rule says it now wins
- `out_of_band`
  - reject or defer explicitly
  - record the reason so the next session does not reopen the same debate from scratch

Default to staying on the active mainline.
Only `mainline_required` is allowed to interrupt immediately without a new human decision.

## 4. Ledger Transaction Rule

Branch handling is not complete until the single live `.forge/` ledger reflects it.

Use this transaction matrix:

- target-state semantics changed
  - update `.forge/spec/SPEC.md`
- acceptance semantics changed
  - update `.forge/spec/FEATURES.json`
- control logic, constraints, or branch policy changed
  - update `.forge/DECISIONS.md`
- active path, mode, or next action changed
  - update `.forge/STATE.md`
- any branch decision happened
  - append it to `.forge/log.md`
- sequencing changed
  - version the current batch or freeze a new one

Do not scatter half-updates across files.
When practical, the branch decision should read like one coherent state transition.

## 5. Rejection Rule

Rejected or deferred branches are still first-class outputs.

At minimum, record:

- what was discovered
- how it was classified
- why it was not absorbed now
- what should happen on a future revisit, if anything

Silence is not a rejection.
If the branch only exists in chat, the next resume will waste time rediscovering it.

## 6. Stop-Legality Link

Branch governance and stop governance are coupled.

After a verified batch, the agent must ask:

1. did this slice discover any branch candidate that changes execution truth?
2. if yes, did I classify and record it?
3. after that classification, is there still a next dependency-closed slice?

Only then can the agent decide whether to continue, block, or declare the current mainline closed.

If the answer to (3) is "no" but active takeover is still in effect, the agent must do one more
thing before stopping:

4. run a local-truth continuation scan for the next credible `change_request`

Examples of valid continuation-scan inputs:

- baseline warnings that now show up in the default smoke path
- nearby hardening gaps visible in handoff docs or `.forge/DECISIONS.md`
- operational drift between the current governance contract and the resume baseline

Only if that continuation scan finds no credible next change request may the run truly close.

## 7. Anti-Patterns

These are protocol failures:

- "I noticed a new requirement, but I'll leave it in the summary"
- "This seems related, so I silently expanded the mainline"
- "This is probably not needed, so I simply ignored it"
- "The batch is green, so I stopped before resolving the newly discovered fork"

The right behavior is slower by a few minutes and faster by many sessions.
