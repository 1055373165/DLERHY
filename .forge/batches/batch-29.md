# Forge Batch 29

Batch: `batch-29`
Mainline: `runtime-self-heal-mainline`
Frozen at: `2026-03-31 21:24:41 +0800`

## Goal

Turn `packet_runtime_defect` into the next bounded repair lane, so repeated packet-level translation
runtime defects can move from lane-health + chapter-hold scaffolding into the same deterministic
`incident -> repair dispatch -> REPAIR work item -> validate/publish -> packet replay` loop already
proven by review deadlock and export misrouting.

## Why This Branch Won

- By the fork rule, `more bounded repair lanes` is closer to the autonomous self-heal mainline than
  `more transport variants on the same two lanes`.
- The remaining branch still has high impact and stays on the active path, but packet repair has the
  nearest implementation scaffolding today:
  - packet lane health already exists
  - translation failures already classify to `RuntimeIncidentKind.PACKET_RUNTIME_DEFECT`
  - RecoveryMatrix already models translation-family retry and chapter-hold escalation
  - chapter-hold currently marks exhaustion, so packet repair is the natural next self-heal step

## Scope

- Extend runtime repair planning with a `packet_runtime_defect` repair plan.
- Teach packet controller to open/update a bounded packet runtime defect incident before chapter-hold
  exhaustion and schedule repair dispatch through the existing incident controller.
- Bind the resulting repair dispatch to the existing `REPAIR` work-item lifecycle and keep replay
  bounded to packet scope.
- Prove packet repair publishes a bundle, rebinds the affected packet scope, and replays only the
  failed packet.

## Verification

- Add packet-repair focused tests alongside the existing runtime self-heal baseline.
- Re-run the existing runtime self-heal unittest baseline and py_compile gate after the packet lane
  is wired in.
