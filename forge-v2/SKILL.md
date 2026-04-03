---
name: codex-forge-v2
description: >-
  Hardened full-cycle development scaffold for Codex. Use for multi-file, multi-session,
  high-ambiguity or high-stakes work where Codex must first amplify a vague requirement into
  an executable expert-grade spec, then keep making durable progress across many context
  windows with initializer/coding-session discipline, batch verification, and recovery.
---

# Codex Forge v2

Forge v2 is the successor to `forge/`.

It keeps Forge's strengths:

- requirement lock
- decision freeze
- dependency-aware batches
- master-side verification
- liveness recovery
- continue-until-blocked execution

It adds two missing layers:

- requirement amplification before coding, so a vague request becomes an expert-grade spec
- long-running-agent hardening, so progress survives across many context windows without drift

Use Forge v2 when the task is too large, ambiguous, or long-lived for a one-shot coding pass.
Do not use it for tiny one-file fixes, casual exploration, or purely conversational planning.

## What Makes v2 Different

Forge v1 mostly assumed the user prompt could be locked directly into an execution plan.
Forge v2 assumes the opposite: the user prompt is starting evidence, not the true boundary of the
problem.

Forge v2 therefore runs in two layers:

1. amplify the requirement into a specification that a top-tier expert would respect
2. execute that specification through a hardened long-running loop

The result is a framework that improves both thinking quality and execution reliability.

## Read Only What You Need

Read [references/requirement-amplification.md](/Users/smy/project/book-agent/forge-v2/references/requirement-amplification.md)
when the user request is underspecified, high-impact, or strategically important.

Read [references/long-running-hardening.md](/Users/smy/project/book-agent/forge-v2/references/long-running-hardening.md)
when setting up or resuming multi-session execution.

Read [references/branch-intake-governance.md](/Users/smy/project/book-agent/forge-v2/references/branch-intake-governance.md)
when new work is discovered mid-run and the agent must decide whether to absorb it into the
mainline, defer it, or reject it without asking the user.

Read [references/runtime-artifacts.md](/Users/smy/project/book-agent/forge-v2/references/runtime-artifacts.md)
when creating or repairing `.forge/` artifacts.

## Core Non-Negotiables

- The user's words are the input, not the final ontology.
- Do not start implementation against a structurally vague prompt when better problem definition
  would materially change the solution.
- Chat is never authoritative state.
- Every long-running session must start by getting bearings from file truth.
- Work one feature or one dependency-closed batch at a time.
- Discovered branch work must be classified and resolved in file truth, not improvised in chat.
- A feature stays failing until it is verified end-to-end.
- End each slice in a clean, restartable state.
- After verification, continue immediately unless a real blocker exists.

## Explicit Activation Contract

Forge v2 should be treated as explicitly activated when the user asks for any of the following
behaviors, even if the wording is informal:

- "use forge-v2"
- "resume from existing `.forge/`"
- "do not ask unless blocked"
- "you are fully responsible"
- "continue automatically"
- "do not stop after one batch"

Once activated, Forge v2 is no longer a polite advisory scaffold.
It becomes the active execution contract for the turn and for subsequent resumed turns until the
user explicitly pauses, redirects, or ends the autonomous run.

## No-Voluntary-Stop Rule

When Forge v2 is active, a verified batch is never by itself a valid reason to stop the turn.

After finishing a verified batch, the agent must not emit a completion-style closeout just because:

- the slice is neat
- the ledger is updated
- the tests are green
- there is enough information to write a nice summary

Those are checkpoint signals, not stop signals.

The agent may stop only if at least one of the following is true:

1. a real blocker has been recorded in `.forge/STATE.md` and `.forge/log.md`
2. the user explicitly asked to pause, stop, review, or explain instead of continuing execution
3. the current turn was intentionally limited to framework editing, diagnosis, or postmortem
4. there is no next dependency-closed slice, the active takeover continuation scan also found no
   credible next change request from local truth, and that fact has been written back into file
   truth

If none of those are true, Forge v2 must continue.

## Runtime Modes

Classify every run into one of these modes before acting:

1. `new_run`
2. `resume`
3. `change_request`
4. `recovery`

If the mode is unclear, resolve that first.

## Control Model

Forge v2 separates truth by purpose, not by convenience:

- `.forge/spec/SPEC.md` is the authoritative statement of what should exist
- `.forge/spec/FEATURES.json` is the authoritative acceptance inventory
- `.forge/STATE.md` is the authoritative execution snapshot
- `.forge/DECISIONS.md` is the authoritative active constraint set
- `.forge/log.md` is the append-only operational memory
- `.forge/batches/` and `.forge/reports/` are the authoritative batch contracts and results

Do not duplicate the same live fact across multiple files unless the duplication changes execution.

## New Run Protocol

For a `new_run`, do this in order:

1. Run the six-stage requirement amplification protocol from
   [references/requirement-amplification.md](/Users/smy/project/book-agent/forge-v2/references/requirement-amplification.md).
2. Write `.forge/spec/SPEC.md` as the executable, expert-grade spec.
3. Write `.forge/spec/FEATURES.json` as the exhaustive acceptance inventory. Every item starts with
   `"passes": false`.
4. Freeze only the decisions that truly constrain implementation in `.forge/DECISIONS.md`.
5. Create or refresh `.forge/init.sh` if the repo needs environment bring-up, smoke testing, or
   common bootstrapping.
6. Initialize `.forge/STATE.md` and `.forge/log.md`.
7. Freeze the smallest useful dependency-closed first batch.
8. Execute, verify, update truth, and continue until blocked.

If structural ambiguity remains after amplification, stop only to ask the narrowest question that
would materially change architecture, sequencing, or safety.

## Resume Protocol

For a `resume`, do not improvise. Start by getting bearings:

1. run `pwd`
2. read `.forge/STATE.md`
3. read the recent tail of `.forge/log.md`
4. read `.forge/DECISIONS.md`
5. read `.forge/spec/FEATURES.json`
6. inspect recent git history
7. run `.forge/init.sh` if present
8. run a short baseline smoke test before starting new work

Do not begin a new feature if the repo is already broken. Fix the baseline first, record the
recovery, then continue.

After bearings are established, the resume loop is:

1. identify the active batch or next failing feature from file truth
2. execute one dependency-closed slice
3. verify it
4. update `.forge/` truth
5. immediately choose the next slice or record a blocker
6. continue in the same turn unless a valid stop condition exists

## Change Request Protocol

For a `change_request`, do not patch the plan blindly.

Re-run the affected requirement-amplification stages, then update:

- `.forge/spec/SPEC.md` when the target state changed
- `.forge/spec/FEATURES.json` when acceptance changed
- `.forge/DECISIONS.md` when constraints or architecture changed
- current or future batch contracts when sequencing changed

If the new request invalidates the current mainline, rewrite the mainline explicitly before
dispatching more work.

When active takeover has already completed the current inventory, `change_request` is the default
continuation mode, not a special exception.

## Discovered-Work Branch Intake Protocol

When Forge v2 is active, new work discovered during execution is not free-form inspiration.
It is a governed branch candidate.

Treat every discovered branch candidate as exactly one of:

1. `mainline_required`
2. `mainline_adjacent`
3. `out_of_band`

Use these meanings:

- `mainline_required`
  - if not handled now, the current mainline would become incorrect, unverifiable, unrecoverable,
    or falsely "complete"
- `mainline_adjacent`
  - useful and nearby, but not required to truthfully complete the current mainline
- `out_of_band`
  - not close enough to the active mainline, or not justified strongly enough to interrupt it

Default behavior:

- absorb `mainline_required` immediately
- record `mainline_adjacent` explicitly, then let the fork rule decide when it competes for the
  next slice
- reject or defer `out_of_band` explicitly; do not leave it as chat residue

Never leave branch handling implicit.

## Branch-Decision Ledger Transaction

If a branch candidate changes execution, write that change back to the single live `.forge/`
ledger as one coherent transaction.

Update the minimum necessary artifacts:

- `.forge/spec/SPEC.md` when target-state semantics changed
- `.forge/spec/FEATURES.json` when acceptance semantics changed
- `.forge/DECISIONS.md` when control logic or constraints changed
- `.forge/STATE.md` when the active path changed
- `.forge/log.md` for every accepted, deferred, or rejected branch decision
- current or next batch contracts when sequencing changed

Accepted, deferred, and rejected branches must all be visible from file truth.
Do not make the next session rediscover the same decision from chat alone.

## Active Takeover Continuation After Inventory Completion

In active takeover mode, completing the current `FEATURES.json` inventory is not by itself a valid
reason to stop the autonomous run.

When the inventory is fully green, Forge v2 must immediately perform a continuation scan from
local truth:

1. inspect repo warnings, failing or noisy baseline signals, nearby backlog/handoff artifacts, and
   adjacent hardening gaps
2. classify any credible next work using the branch intake protocol
3. if a credible `change_request` exists, rewrite spec/features/decisions/state and freeze the next
   batch
4. only if no credible next change request exists may file truth say the run can stop

Inventory completion is therefore a checkpoint in takeover mode, not automatic permission to idle.

## Recovery Protocol

For `recovery`, inspect file truth first.

Recovery is mandatory when any of these are true:

- the active report is missing and no owned-file movement is visible
- the worker or session finished but the expected report never landed
- the repo was left in a broken or ambiguous state
- the active batch contract no longer matches reality

When recovering:

1. inspect repo truth, report truth, and recent log entries
2. decide whether work actually started
3. version the active batch contract if needed
4. write one authoritative next path
5. record the recovery in `.forge/log.md`
6. continue instead of idling

Never leave two equally valid next paths active at once.

## Batch Rule

Every batch must be:

- dependency-closed
- bounded by owned files
- linked to one or more explicit feature ids
- independently verifiable
- small enough to leave the repo clean at the end of the slice

Do not batch by aesthetic symmetry. Batch by dependency, write-set separation, and verification
boundary.

## Verification Rule

Worker self-report is never enough.

A batch is complete only when:

1. the expected report exists
2. repo truth matches the claimed owned-file result
3. representative verification passes
4. any affected feature status in `FEATURES.json` is updated only after verification
5. `.forge/STATE.md` and `.forge/log.md` reflect the verified reality

When user-facing behavior is involved, prefer end-to-end verification over code-only confidence.

## Clean Slice Rule

At the end of each verified slice, leave behind:

- a passing or intentionally documented baseline
- a report with concrete evidence
- updated state and feature truth
- a clear next target for the following session

Prefer a durable checkpoint such as a clean local commit when that fits the repo and user context.
If a commit would be inappropriate, record equivalent handoff quality in `.forge/log.md` and the
batch report. Do not leave undocumented half-finished work as the handoff.

## Continue-Until-Blocked Rule

After a verified batch, Forge v2 must immediately do one of two things:

1. freeze and execute the next dependency-closed slice
2. enter a concrete blocked state with the blocker recorded

These are valid blockers:

- missing permission that cannot be worked around
- structural ambiguity with real solution consequences
- unresolved safety boundary
- missing external dependency or credential that cannot be substituted

These are not valid blockers:

- a nice progress-reporting moment
- a habit of pausing after a clean slice
- wanting reassurance when no real decision is needed
- framework ceremony that does not change the next action

In active takeover mode, a final answer is forbidden unless the agent first performs a stop check
against the No-Voluntary-Stop Rule and can point to the matching file-truth evidence.

If the next slice is clear, the correct action is not "report success".
The correct action is "freeze the next slice and keep going."

## Mandatory Post-Verification Loop

After every verified batch, run this exact loop:

1. reconcile repo truth with `.forge/STATE.md`, `.forge/log.md`, and the batch report
2. classify any newly discovered branch work and write the decision to file truth
3. apply the fork rule to determine the closest next dependency-closed slice
4. either:
   - freeze that slice and continue execution, or
   - write an explicit blocked state with a concrete blocker, or
   - if the inventory is complete, run the takeover continuation scan and freeze the next
     `change_request`, or
   - write explicitly that no next dependency-closed slice or credible next change request remains
5. only then send a user update

User updates are visibility only.
They do not implicitly end the autonomous loop.

## Final-Answer Gate

Before sending any completion-style final answer while Forge v2 is active, the agent must be able
to answer "yes" to at least one of these:

- "Did I record a real blocker in file truth?"
- "Did the user explicitly ask me to stop or pause?"
- "Was this turn explicitly about diagnosis/framework editing instead of continuing product work?"
- "Did I explicitly write that there is no next dependency-closed slice and no credible next
   change request?"

If the answer to all four is "no", sending a final answer would violate Forge v2.
Keep going instead.

## Stop-Legality Audit

When Forge v2 stops, the workspace should be able to answer:

- why execution stopped
- whether the stop was legal
- what the next session should do

If file truth cannot answer those three questions, the stop was not hardened enough.

Legal stop reasons are only:

1. real blocker recorded in `.forge/STATE.md` and `.forge/log.md`
2. explicit user pause/stop/review request
3. explicit framework-editing or diagnosis turn boundary
4. explicit file-truth statement that no next dependency-closed slice and no credible next change
   request remain after the takeover continuation scan

Anything else is an illegal soft stop, even if the summary looked tidy.

## Style Of Thought

Use narrative paragraphs for reasoning and specification work.
Use structured lists or tables only when the content is naturally structured, such as:

- explicit requirements
- constraints
- alternative comparisons
- acceptance criteria
- batch contracts
- verification matrices

The framework is not trying to sound smart. It is trying to define the problem so precisely that
execution becomes hard to get wrong.

## Non-Goals

Forge v2 is not:

- a swarm fantasy
- a ceremony generator
- a substitute for engineering judgment
- a license to keep coding against a bad spec

It is a practical scaffold for turning vague requests into precise specs, then landing the work
reliably across many sessions.
