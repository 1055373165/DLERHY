# Forge v2 — Long-Running Hardening

Forge v2 absorbs the key lessons from Anthropic's long-running agent harness work and maps them
onto Forge's file-truth execution model.

The central problem is simple:
an agent working across many context windows repeatedly starts fresh, so progress decays unless the
workspace itself teaches the next session what matters.

Forge v2 solves that with a small set of hard rules.

## 1. Separate Initialization From Incremental Execution

Do not use the exact same mental contract for the first session and the fiftieth session.

Initialization is responsible for:

- amplifying the requirement
- writing the executable spec
- creating the acceptance inventory
- creating or refreshing `.forge/init.sh`
- establishing the first clean execution baseline

Incremental coding sessions are responsible for:

- getting bearings quickly
- choosing one failing feature or one small batch
- making concrete progress
- verifying the result
- leaving durable artifacts for the next session

This can happen with one model and one toolset.
What changes is the session contract.

## 2. Make Completion Hard To Fake

Long-running agents fail in two common ways:

- they try to do too much in one session
- they decide the project is done too early

Forge v2 counters both by maintaining `.forge/spec/FEATURES.json` as a comprehensive acceptance
inventory.

Rules for `FEATURES.json`:

- it should be more exhaustive than the user prompt
- every feature starts with `"passes": false`
- feature semantics should remain stable across sessions
- the implementation loop should only flip pass status after verification
- do not delete hard tests to create the appearance of progress

The feature inventory is the anti-victory-lock.
It prevents vague feelings of progress from becoming fake completion.

## 2.1 Make "Nice Stopping Points" Illegal

Another common failure mode is not fake completion, but fake suspension:

- a clean slice lands
- the ledger is updated
- the agent writes a polished summary
- execution stops even though the next slice is obvious

In a long-running autonomous loop, this is still a control failure.

Rules:

- a verified batch is a checkpoint, not a stop signal
- a nice summary is not a blocker
- "I could ask the user now" is not a blocker unless the question has real structural consequences
- after verification, the agent must either continue or record a concrete blocked state in file truth

This is the anti-premature-close rule.
It exists because conversational models naturally want to end on tidy milestones.

## 2.2 Govern Discovered Branches Instead Of Improvising Them

Long-running agents also drift when new work appears mid-run.

If the agent asks the human about every branch, autonomy collapses.
If the agent absorbs every branch, the mainline dissolves.

Forge v2 therefore requires branch intake governance:

- classify discovered work as `mainline_required`, `mainline_adjacent`, or `out_of_band`
- absorb only what is truly required for an honest mainline
- record deferred or rejected branches in file truth
- never leave a fork resolved only in chat

This keeps the run adaptive without becoming scope soup.

## 2.3 Make Stops Auditable

The workspace should be able to explain every stop from file truth alone.

A hardened stop answers:

- why the run stopped
- whether the stop was legal
- what the next session should do

If those answers are missing, the stop was under-specified even if the code change itself was
correct.

In active takeover mode, another question is required before stopping:

- did the agent scan local truth for the next credible change request after the current inventory
  completed?

Without that scan, "the inventory is green" is not yet a hardened stop.

## 3. Start Every Session By Getting Bearings

Every resumed session should do a short deterministic startup ritual:

1. run `pwd`
2. read `.forge/STATE.md`
3. read recent `.forge/log.md`
4. inspect recent git history
5. read `.forge/spec/FEATURES.json`
6. run `.forge/init.sh` if present
7. run a baseline smoke test
8. only then choose the next failing feature or active batch

Do not trust memory.
Do not trust the previous chat window.
Trust the workspace.

## 4. Leave A Clean Handoff

A good session does not just make changes.
It leaves the next session an orderly situation.

The required handoff quality is:

- the repo is in a clean or intentionally explained state
- progress is recorded in `.forge/log.md`
- the batch report records exact evidence
- state and feature truth are updated
- the next likely target is obvious

Prefer a durable checkpoint such as a local commit when that helps preserve a known-good state.
If a commit is not appropriate, the report and log must provide equivalent recovery quality.

## 5. Run Baseline Tests Before New Work

Do not stack fresh work on top of an already broken baseline.

`.forge/init.sh` should make it cheap to:

- start required services
- restore dev dependencies or background processes when needed
- run a fast smoke path

If the smoke path fails, the current job is recovery, not new feature work.

## 6. End-To-End Proof Beats Local Confidence

A feature is not done because the code "looks right."
A feature is not done because unit tests happen to pass.

When behavior matters, verify it the way a user or operator would experience it.
Use browser automation, CLI flows, API calls, or other realistic end-to-end checks whenever the
surface requires them.

Only after that verification may a feature flip to passing.

## 7. Recovery Is A First-Class Part Of The Loop

Long-running work will stall.
That is not exceptional.

What matters is how quickly the framework notices and corrects it.

Enter recovery when:

- the active report is missing
- no owned-file movement is visible
- the worker finished but the handoff did not land
- the repo baseline is broken at session start

Recovery should produce:

- one authoritative next path
- a versioned batch contract if reality changed
- a log entry
- a resumed execution path instead of passive waiting

## 8. Keep Mutable Artifacts Machine-Friendly

Forge v2 uses Markdown for narrative reasoning and JSON for machine-stable acceptance state.

Use Markdown where the model benefits from prose:

- the spec
- decisions
- logs
- reports

Use JSON where the model should resist casual rewriting:

- feature inventory

This keeps the thinking rich without making the durable state easy to corrupt.

## Mapping Anthropic's Harness Ideas Into Forge v2

- initializer agent -> `new_run` initialization contract
- coding agent -> `resume` execution contract
- `claude-progress.txt` -> `.forge/log.md`
- feature list JSON -> `.forge/spec/FEATURES.json`
- `init.sh` -> `.forge/init.sh`
- incremental feature work -> one feature or one small batch at a time
- clean handoff -> report + log + state + clean baseline
- early false completion defense -> exhaustive failing feature inventory

Forge v2 keeps Forge's verification and recovery discipline, but now the workspace also teaches the
next session how to restart correctly.
