# Forge v2 — Runtime Artifacts

Forge v2 preserves Forge's `.forge/` single-directory truth model while adding a dedicated spec
surface for requirement amplification.

Use these artifacts.
Do not create parallel ledgers unless they change control behavior materially.

## Directory Layout

```text
.forge/
  STATE.md
  DECISIONS.md
  log.md
  init.sh
  spec/
    SPEC.md
    FEATURES.json
  batches/
    batch-{N}.md
    batch-{N}-v2.md
  reports/
    batch-{N}-report.md
```

## `.forge/spec/SPEC.md`

Purpose:
authoritative narrative specification of the target system or change.

Recommended sections:

- north star
- explicit requirements
- hidden requirements
- constraints
- chosen problem framing
- chosen solution topology
- user or operator flows
- edge cases and failure modes
- non-goals
- success criteria
- initial delivery strategy

This file should be strong enough that a new session can understand what "great" looks like
without rereading the full chat history.

## `.forge/spec/FEATURES.json`

Purpose:
authoritative acceptance inventory for execution.

Suggested schema:

```json
[
  {
    "id": "F001",
    "category": "functional",
    "priority": "P0",
    "title": "New chat creates a fresh conversation",
    "description": "User can start a new conversation from the primary interface.",
    "verification": [
      "Open the main interface",
      "Click the New Chat button",
      "Confirm a new conversation is created",
      "Confirm the welcome state appears",
      "Confirm the conversation appears in the sidebar"
    ],
    "dependencies": [],
    "passes": false
  }
]
```

Rules:

- all items start with `"passes": false`
- ids stay stable
- verification text should not be casually rewritten during execution
- flip pass status only after verification
- record evidence in the batch report, not by bloating this file

## `.forge/STATE.md`

Purpose:
authoritative execution snapshot.

Required fields:

- mode
- current_step
- active_batch
- active_batch_contract
- expected_report
- active_feature_ids
- completed_items
- failed_items
- last_verified_test_baseline
- last_update_time

Recommended active worker section when available:

- worker id
- worker nickname
- model
- reasoning setting
- dispatch time
- last harvest check

Keep this file short and current.
It is the answer to "where are we right now."
Keep the required field names stable when an existing Forge supervisor or surrounding tooling
already depends on them.

## `.forge/DECISIONS.md`

Purpose:
authoritative active constraints and architectural decisions.

Keep only live decisions such as:

- architecture boundaries
- ownership boundaries
- test strategy
- rollback boundary
- safety contract

Do not turn this into a history essay.
Historical detail belongs in `.forge/log.md`.

## `.forge/log.md`

Purpose:
append-only operational memory and handoff trail.

Every meaningful entry should capture:

- timestamp
- event
- evidence or reason
- consequence
- next action

Typical events:

- requirement lock updated
- feature inventory refreshed
- batch dispatched
- verification passed
- recovery triggered
- mainline rewritten
- change request absorbed

This file plays the role of durable session memory.

## `.forge/init.sh`

Purpose:
cheap environment bring-up and baseline verification.

Good `init.sh` behavior:

- idempotent
- fast enough to run every session
- fails loudly
- starts required local services when needed
- optionally runs a smoke path or prints exact next verification commands

If the repo does not need runtime bring-up, `init.sh` can be omitted.
Do not create it just for ceremony.

## `.forge/batches/batch-{N}.md`

Purpose:
authoritative contract for one dependency-closed slice.

Each batch should contain:

- objective
- linked feature ids
- owned files
- dependencies
- exact verification command or steps
- stop condition
- expected report path

When recovering, version the contract instead of silently rewriting history:

- `batch-7-v2.md`
- `batch-7-v3.md`

Only one version may be authoritative at a time.

## `.forge/reports/batch-{N}-report.md`

Purpose:
authoritative record of what was actually delivered and verified.

Each report should include:

- completed items
- files changed
- exact verification commands
- evidence summary
- feature ids flipped to passing
- scope deviations
- newly discovered work or blockers

Do not claim success without concrete evidence.

## Minimal Duplication Rule

If a fact can be recovered cheaply from:

- repo truth
- `FEATURES.json`
- the active batch contract
- the latest report

then do not copy it into three other places.

Forge v2 is hardened, not bloated.
