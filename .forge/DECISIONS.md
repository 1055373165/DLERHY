# Forge Decisions

1. Workspace decision
- Stay in the single main repo checkout on `main`.
- Do not create a second live worktree.

2. Mainline decision
- Current mainline is `Runtime self-heal closure`.
- Runtime V2 control-plane work is stable enough; the next focus is incident/repair execution, not more reviewer surface polish.

3. Scope control decision
- Prioritize changes that directly improve runtime self-heal closure.
- Push non-blocking polish into `docs/optimization-backlog.md`, not the mainline.

3.1 Fork resolution decision
- When two plausible next directions appear, choose the one closest to the active mainline.
- Score the other direction for mainline impact before dispatching the next slice.
- If that impact is high, rewrite the mainline and keep it on the active path.
- If that impact is not high, move it to backlog or a later batch.
- Do not leave a fork resolved only in chat.

3.2 Continuation decision
- A verified batch is not a stop condition by itself.
- If the next dependency-closed slice is clear, Forge must freeze and continue automatically.
- Only a real blocker or a real human decision can pause the run.

4. Current write-set decision
- Prefer staying inside:
  - `src/book_agent/services/runtime_repair_planner.py`
  - `src/book_agent/services/runtime_repair_contract.py`
  - `src/book_agent/services/runtime_repair_worker.py`
  - `src/book_agent/services/runtime_repair_registry.py`
  - `src/book_agent/services/runtime_repair_agent_adapter.py`
  - `src/book_agent/services/runtime_repair_executor.py`
  - `src/book_agent/services/runtime_repair_transport.py`
  - `src/book_agent/tools/runtime_repair_runner.py`
  - `src/book_agent/app/runtime/controller_runner.py`
  - `src/book_agent/app/runtime/controllers/incident_controller.py`
  - `src/book_agent/app/runtime/controllers/export_controller.py`
  - `src/book_agent/app/runtime/controllers/packet_controller.py`
  - `src/book_agent/app/runtime/controllers/review_controller.py`
  - `src/book_agent/services/run_execution.py`
  - `src/book_agent/infra/repositories/run_control.py`
  - `src/book_agent/app/runtime/document_run_executor.py`
  - `src/book_agent/services/workflows.py`
  - `tests/test_runtime_repair_contract.py`
  - `tests/test_runtime_repair_planner.py`
  - `tests/test_runtime_repair_registry.py`
  - `tests/test_runtime_repair_transport.py`
  - `tests/test_packet_runtime_repair.py`
  - `tests/test_controller_runner.py`
  - `tests/test_controller_runner_packet_repair.py`
  - `tests/test_export_controller.py`
  - `tests/test_incident_controller.py`
  - `tests/test_run_execution.py`
  - `tests/test_req_mx_01_review_deadlock_self_heal.py`
  - `tests/test_req_ex_02_export_misrouting_self_heal.py`
  - `docs/mainline-progress.md`
  - `progress.txt`
- Expand only if blocked by a real dependency.

5. Verification decision
- Every batch must pass:
  - `.venv/bin/python -m unittest tests.test_runtime_repair_transport tests.test_runtime_repair_executor tests.test_runtime_repair_registry tests.test_runtime_repair_planner tests.test_export_controller tests.test_incident_controller tests.test_req_mx_01_review_deadlock_self_heal tests.test_req_ex_02_export_misrouting_self_heal tests.test_run_execution.RunExecutionServiceTests.test_ensure_repair_dispatch_work_item_seeds_claimable_repair_lane_once tests.test_run_execution.RunExecutionServiceTests.test_executor_fails_repair_work_item_for_unknown_worker_hint tests.test_run_execution.RunExecutionServiceTests.test_executor_fails_repair_work_item_for_unknown_executor_hint tests.test_run_execution.RunExecutionServiceTests.test_executor_fails_repair_work_item_for_unknown_transport_hint`
  - `.venv/bin/python -m py_compile src/book_agent/services/runtime_repair_agent_adapter.py src/book_agent/services/runtime_repair_executor.py src/book_agent/services/runtime_repair_registry.py src/book_agent/services/runtime_repair_transport.py src/book_agent/services/runtime_repair_planner.py src/book_agent/services/runtime_repair_worker.py src/book_agent/services/run_execution.py src/book_agent/app/runtime/controllers/incident_controller.py src/book_agent/app/runtime/controllers/export_controller.py src/book_agent/app/runtime/controllers/review_controller.py src/book_agent/app/runtime/document_run_executor.py src/book_agent/services/workflows.py src/book_agent/tools/runtime_repair_runner.py tests/test_runtime_repair_transport.py tests/test_runtime_repair_executor.py tests/test_runtime_repair_registry.py tests/test_runtime_repair_planner.py tests/test_export_controller.py tests/test_incident_controller.py tests/test_req_mx_01_review_deadlock_self_heal.py tests/test_req_ex_02_export_misrouting_self_heal.py tests/test_run_execution.py`

6. Immediate next-slice decision
- By the fork rule, the next branch should favor `more bounded repair lanes` over `more transport variants`.
- Reason: lane coverage is now closer to the autonomous self-heal mainline than adding a fourth transport to the same two lanes.
- Selected next lane: `packet_runtime_defect`.
- Why this lane: packet lane health already exists, translation-family incident triage already maps to `PACKET_RUNTIME_DEFECT`, and current chapter-hold exhaustion marks the exact place where packet repair should step in before broad scope escalation.
- Keep remote executor / transport broadening on the active path because its mainline impact is still high.
- The next dependency-closed slice is therefore `packet_runtime_defect incident -> repair dispatch -> REPAIR lane -> bounded packet replay`.

7. Packet lane closure decision
- `packet_runtime_defect` is now considered a bounded repair lane on the active mainline.
- It has all of the following, not just planner scaffolding:
  - incident -> proposal -> repair dispatch
  - claimable `REPAIR` work-item
  - bounded packet replay
  - direct routing parity across in-process / agent-backed / configured-command / HTTP
  - controller-runner automatic-path parity across agent-backed / configured-command / HTTP

8. Next fork decision
- With three bounded repair lanes now proven, the closest next branch is no longer “more lane coverage”.
- By the fork rule, the next branch should favor `remote / agent-facing repair executor` over
  additional route variants or front-end/operator work.
- Reason:
  - packet repair already closes the nearest lane-coverage gap
  - explicit request/result contracts now exist
  - the remaining highest-impact gap to the user’s stated goal is letting a genuinely independent
    repair agent consume that contract, rather than assuming the remote side is another local
    registry-backed runner

9. Contract decision
- `runtime_repair_contract.py` is now the source of truth for external repair-agent payload shape.
- Future remote executor work must build on this explicit request/result contract instead of adding
  more ad hoc keys directly to transports or runners.

10. Result validation decision
- Remote and transport-backed repair execution may no longer “best-effort” arbitrary JSON back into
  the runtime.
- A remote repair response must satisfy the explicit repair result contract version, otherwise the
  executor boundary must fail deterministically instead of letting malformed payloads enter the
  repair lifecycle as fake successes.

11. Remote provenance decision
- Remote contract execution now has to preserve execution provenance in the result contract.
- The runtime should be able to tell:
  - which remote execution handled the repair
  - when that execution started and completed
  - which endpoint actually served the request

12. Remote decision decision
- Remote/local repair payloads now carry an explicit `repair_agent_decision`.
- Current accepted decision is only `publish_bundle_and_replay`.
- By the fork rule, the next closest slice is not another transport variant; it is teaching the
  `REPAIR` lane to interpret additional explicit decisions such as manual escalation or retry.

13. Decision-aware REPAIR lane decision
- The `REPAIR` lane now interprets non-default remote decisions deterministically.
- `manual_escalation_required` is now a distinct terminal repair outcome, not a generic failure.
- `retry_later` is now a distinct retryable repair outcome, not a generic failure.

14. Decision-aware lineage decision
- Repair dispatch lineage must no longer stop at `status=failed` for every non-happy-path result.
- Dispatch state now carries:
  - `status`
  - `next_action`
  - `retryable`
  - `retry_after_seconds`
  - `next_retry_after`
- Packet runtime defect is now also covered by the retry-later path, so decision-aware lineage is
  proven across all three bounded repair lanes.

15. Immediate next-slice decision
- By the fork rule, the next closest branch is no longer “more decision vocabulary”.
- The next closest branch is `repair backoff / escalation-aware scheduling`.
- Reason:
  - runtime now understands non-default remote decisions
  - dispatch lineage now records what should happen next
  - the remaining highest-impact gap is to make the scheduler and claim layer actually honor
    `retry_later` / manual-escalation guidance instead of merely recording it

16. Resume handoff decision
- `snapshot.md` is now the primary human-readable resume artifact for the current run.
- `forge-v2` must resume from existing `.forge/` truth rather than creating a second live runtime
  ledger.
- The current repo state should be treated as `resume` with artifact reconciliation already
  completed through batches 39-41 and batch-42 frozen.

17. Immediate next-slice decision
- The active next slice is `batch-42: bounded-lane repair blockage projection`.
- Reason:
  - scheduling and explicit resume semantics are already proven
  - the next highest-value gap is to surface repair blockage as bounded-lane control-plane truth
  - this is closer to autonomous runtime self-heal than adding new route variants or revisiting UI

18. Bounded-lane blockage projection decision
- `repair_dispatch` now carries a unified `repair_blockage` projection.
- Bounded-lane control-plane surfaces now mirror that blockage truth instead of forcing callers to
  inspect deep repair lineage:
  - review deadlock recovery surface
  - packet runtime defect recovery surface
  - export pending repair surface
- The projection must distinguish at least:
  - `backoff_blocked`
  - `manual_escalation_waiting`
  - `ready_to_continue`
- Batch-42 is now considered closed and verified from repo truth; the next slice is not frozen yet
  and should be chosen by reapplying the fork rule from this verified state.

19. Forge v2 bootstrap decision
- The repo was resumed under Forge v2 without `.forge/spec/SPEC.md`, `.forge/spec/FEATURES.json`,
  or `.forge/init.sh`.
- The closest next slice after batch-42 was therefore not new product behavior, but bootstrapping
  those missing Forge v2 artifacts from verified runtime self-heal truth.
- Batch-43 is now closed and verified, and the feature inventory leaves `F007` as the next
  authoritative failing feature.

20. Next-slice decision
- The active next slice is `batch-44: workflow/API-friendly runtime repair blockage summaries`.
- Reason:
  - batch-42 already proved blockage projection inside controller/control-plane surfaces
  - export and run-facing payloads still require callers to inspect nested runtime objects rather
    than one normalized blockage summary
  - this is the closest dependency-closed step from verified batch-42 truth and explicit failing
    feature `F007`

21. Workflow/API blockage summary decision
- Batch-44 is now considered closed and verified.
- Normalized runtime repair blockage summary is now part of:
  - run summary `status_detail_json.runtime_v2`
  - document summary `runtime_v2_context`
  - export action result `runtime_v2_context`
  - export detail `runtime_v2_context`
- The summary intentionally prefers the closed-loop export recovery payload over stale pending
  repair metadata when both exist, so user-facing callers read the latest bounded-lane truth.

22. Next-slice decision
- The active next slice is `batch-45: export dashboard record blockage summary parity`.
- Reason:
  - batch-44 closed run/detail contexts, but export dashboard callers still need an extra detail
    fetch to read normalized blockage state
  - export records already persist runtime v2 context, so record-level parity is the closest
    dependency-closed continuation
  - this keeps the mainline on runtime self-heal control-plane closure instead of branching into a
    new transport, lane, or UI theme

23. Export dashboard parity decision
- Batch-45 is now considered closed and verified.
- Export dashboard records now expose `runtime_v2_context`, so the normalized blockage summary is
  readable from dashboard, export detail, document summary, and export action result without
  forcing an extra export-detail fetch.

24. Next-slice decision
- The active next slice is `batch-46: latest-run bounded-lane workflow parity beyond export-only context`.
- Reason:
  - `_runtime_v2_context_for_run(...)` still privileges export recovery fields and can drop
    review-deadlock or packet-runtime-defect bounded recovery from workflow-facing context
  - the next closest dependency-closed gap is to project latest-run blockage truth for all bounded
    repair lanes into document-level workflow surfaces
  - this is closer to runtime self-heal closure than inventing new transport variants or separate
    UI work

25. Latest-run workflow parity decision
- Batch-46 is now considered closed and verified.
- Document summary and history latest-run payloads now expose normalized blockage summary for a
  non-export bounded lane (`last_deadlock_recovery`) instead of assuming workflow context only
  matters for export recovery.

26. Next-slice decision
- The active next slice is `batch-47: widen Forge v2 smoke baseline to cover workflow blockage parity`.
- Reason:
  - the newly landed workflow blockage surfaces are now part of the intended resume contract
  - `.forge/init.sh` still only verifies the older controller/runtime slice and would miss these
    newer workflow regressions
  - hardening the default resume smoke is the closest dependency-closed continuation after closing
    the product-facing parity surfaces

27. Init smoke hardening decision
- Batch-47 is now considered closed and verified.
- `.forge/init.sh` now covers:
  - the original controller/runtime self-heal smoke
  - run summary blockage parity
  - export self-heal blockage parity
  - document summary/history workflow blockage parity
  - export dashboard blockage parity

28. Next-slice decision
- The active next slice is `batch-48: human-facing handoff doc reconciliation`.
- Reason:
  - `.forge` truth is now materially ahead of `snapshot.md`, `progress.txt`, and
    `docs/mainline-progress.md`
  - the user explicitly identified those files as important resume artifacts
  - reconciling them is the closest dependency-closed continuation after widening the automated
    resume smoke

29. Human handoff reconciliation decision
- Batch-48 is now considered closed and verified.
- `snapshot.md`, `progress.txt`, and `docs/mainline-progress.md` now describe the verified
  batch-47 state instead of leaving new programmers at the stale batch-42 handoff point.

30. Next-slice decision
- The active next slice is `batch-49: packet latest-run workflow parity regression coverage`.
- Reason:
  - document-level latest-run workflow parity is now implemented generically
  - review-deadlock has explicit API regression coverage, but packet-runtime-defect does not yet
  - adding that representative packet regression is the closest dependency-closed continuation

31. Packet workflow parity coverage decision
- Batch-49 is now considered closed and verified.
- Document summary/history latest-run workflow parity is now explicitly covered for both:
  - `last_deadlock_recovery`
  - `last_runtime_defect_recovery`

32. Next-slice decision
- The active next slice is `batch-50: default init smoke packet workflow parity coverage`.
- Reason:
  - packet latest-run workflow parity is now proven, but the default Forge v2 smoke still does not
    protect that exact regression
  - widening the smoke is the closest dependency-closed hardening step after closing packet parity

33. Packet init-smoke parity decision
- Batch-50 is now considered closed and verified.
- `.forge/init.sh` now covers both latest-run workflow parity regressions:
  - `last_deadlock_recovery`
  - `last_runtime_defect_recovery`
- Default Forge v2 resume smoke therefore protects both non-export bounded lanes, not only the
  review-side parity regression.

34. Framework change-request decision
- The current turn explicitly introduced a Forge v2 framework change request rather than another
  runtime product slice.
- That request is to formalize autonomous branch intake and stop-legality rules so Forge v2 no
  longer depends on ad hoc agent judgment when new work appears mid-run.
- It is valid to handle this as a framework-governance batch before selecting any new product slice.

35. Branch intake governance decision
- Forge v2 must classify discovered mid-run work as exactly one of:
  - `mainline_required`
  - `mainline_adjacent`
  - `out_of_band`
- Only `mainline_required` may interrupt immediately without a new human decision.
- `mainline_adjacent` must be recorded explicitly and then compete via the fork rule.
- `out_of_band` must be rejected or deferred explicitly, not left only in chat.

36. Single-ledger branch transaction decision
- Accepted, deferred, and rejected branch decisions are not complete until the single live
  `.forge/` ledger reflects them.
- The minimum transaction updates are:
  - `.forge/spec/SPEC.md` when target-state semantics change
  - `.forge/spec/FEATURES.json` when acceptance changes
  - `.forge/DECISIONS.md` when control logic changes
  - `.forge/STATE.md` when the active path changes
  - `.forge/log.md` for every branch decision
  - current or next batch contracts when sequencing changes
- No branch resolution may remain chat-only.

37. Stop-legality audit decision
- After every verified batch, Forge v2 must:
  - reconcile repo truth
  - classify newly discovered branch work
  - determine whether a next dependency-closed slice exists
  - either continue, record a blocker, or explicitly record that no next slice remains
- A tidy checkpoint, green tests, or a polished summary is never enough to justify a stop.
- For the current run, no further dependency-closed slice remains inside the present
  `FEATURES.json` inventory; any future work should start as a change request against the existing
  `.forge/` truth instead of pretending the current mainline is still half-open.

38. Governance-smoke change-request decision
- The next change request should not reopen runtime product behavior.
- The closest new slice is to make the default Forge v2 init smoke validate governance drift as
  well as runtime behavior.
- Reason:
  - branch-intake and stop-legality rules are now part of the active execution contract
  - they were still being verified only by ad hoc commands recorded in reports/state
  - moving that validation into the default smoke is the closest dependency-closed hardening step

39. Governance-smoke decision
- The default Forge v2 init path must now validate:
  - `.forge/spec/FEATURES.json` remains valid JSON
  - active branch-intake / stop-legality language still exists across skill, references, spec, and
    decisions
  - human-facing handoff docs still reflect the current mainline-complete / change-request truth
- This validation should remain lightweight enough for routine resume use.

40. Post-batch-52 completion decision
- Batch-52 is now considered closed and verified.
- The current `FEATURES.json` inventory is fully green through `F018`.
- No further dependency-closed slice remains after this governance-smoke hardening step.
- Any future work should enter as a new `change_request` against the existing single-ledger
  `.forge/` truth.

41. Root-cause decision for the unexpected stop
- The run stopped again because Forge v2 still treated "no next dependency-closed slice in the
  current inventory" as sufficient stop evidence, even under an explicit fully autonomous takeover
  contract.
- That was too weak for the user's real requirement.
- In full takeover mode, inventory completion must be treated as a checkpoint that opens the next
  `change_request`, not as automatic permission to stop.

42. Post-completion continuation decision
- Forge v2 must now run a local-truth continuation scan after inventory completion in active
  takeover mode.
- Valid scan inputs include:
  - warnings or hygiene regressions visible in the default smoke path
  - adjacent hardening gaps visible in `.forge/` truth or handoff docs
  - governance drift that weakens autonomous resume
- Only if that continuation scan finds no credible next change request may the run legally stop.

43. Next change-request decision
- The next credible adjacent slice is `batch-54: FastAPI lifecycle warning cleanup`.
- Reason:
  - the default Forge v2 smoke still emits explicit FastAPI `on_event` deprecation warnings
  - this is visible local truth in the default baseline, not speculative ideation
  - cleaning that lifecycle warning is closer and cheaper than chasing the broader sqlite
    `ResourceWarning` surface first

44. Lifecycle-warning cleanup decision
- Batch-54 is now considered closed and verified.
- The app now uses a lifespan-managed startup/shutdown path instead of deprecated FastAPI
  `on_event` hooks.
- Request-time database bootstrap now reuses the same initialization path as lifespan startup, so
  lifecycle cleanup did not reopen the REQ-EX-02 bootstrap path.

45. Post-batch-54 continuation decision
- The continuation scan after batch-54 still found one credible adjacent local-truth slice:
  sqlite `ResourceWarning: unclosed database` noise in the default Forge v2 smoke.
- That warning remained visible in the baseline even after lifecycle cleanup, so the next slice is
  `batch-55: sqlite resource-warning cleanup`.

46. Resource-warning cleanup decision
- Batch-55 is now considered closed and verified.
- sqlite backfill helpers and representative controller/runtime smoke tests now close connections
  and engines cleanly enough that the default init smoke no longer emits the unclosed-database
  resource warning.

47. Post-batch-55 stop-legality decision
- The continuation scan after batch-55 found no further credible adjacent `change_request` from
  current local truth.
- Current evidence:
  - `bash .forge/init.sh` passes
  - the FastAPI lifecycle warning probe returns `not_found`
  - the sqlite unclosed-database warning probe returns `not_found`
  - no nearer governance drift or smoke-visible hygiene regression remains in `.forge` truth
- The run may therefore return to an explicit `mainline_complete` checkpoint without violating the
  fully autonomous takeover contract.

48. Post-batch-55 continuation correction
- A closer review of current local truth showed one more adjacent hardening gap after batch-55:
  the warning-free baseline was still enforced only by ad hoc probe commands recorded in reports
  and state, not by the default `.forge/init.sh` path itself.
- Under the active takeover rules, that is still a credible adjacent `change_request`, because a
  future regression could restore those warnings while the default smoke continued to pass.

49. Warning-hygiene gate decision
- The next slice is `batch-56: default init warning-hygiene gate`.
- `.forge/init.sh` must now capture its smoke output and fail if either of these warning classes
  reappears:
  - FastAPI lifecycle `on_event` deprecation warning
  - sqlite `ResourceWarning: unclosed database`
- This enforcement should stay lightweight and local to the default init path.

50. Post-batch-56 stop-legality decision
- Batch-56 is now considered closed and verified.
- The default init smoke now enforces the known warning-hygiene guarantees as hard failures, and
  the validator itself rejects a synthetic forbidden-warning log.
- The continuation scan after batch-56 found no further credible adjacent `change_request` in
  current local truth, so the run may return to explicit `mainline_complete`.

51. Post-batch-56 continuation correction
- A further continuation scan still found one adjacent governance gap after batch-56:
  governance validation did not yet prove that the warning-hygiene gate remained wired into the
  default init path.
- That meant batch-56 could still silently degrade if someone removed the gate from `.forge/init.sh`
  while leaving handoff prose and reports intact.

52. Warning-gate governance decision
- The next slice is `batch-57: governance validation for warning-hygiene wiring`.
- The governance validator must now assert both:
  - `.forge/init.sh` still captures smoke output and invokes `validate_init_warning_hygiene.sh`
  - `.forge/scripts/validate_init_warning_hygiene.sh` still blocks the known FastAPI lifecycle and
    sqlite unclosed-database warnings

53. Post-batch-57 stop-legality decision
- Batch-57 is now considered closed and verified.
- Warning hygiene is now protected twice:
  - by the default init execution path
  - by the governance validator that proves the gate is still wired and still forbids the known
    warning classes
- The continuation scan after batch-57 found no further credible adjacent `change_request` in
  current local truth, so the run may remain at explicit `mainline_complete`.

54. Post-batch-57 continuation correction
- Another adjacent governance gap still remained after batch-57:
  governance validation did not yet prove that the latest verified batch/report artifacts actually
  existed on disk or that `.forge/STATE.md` still carried the explicit mainline checkpoint fields.
- That meant the current checkpoint could still partially drift while handoff prose and higher-level
  governance checks continued to pass.

55. Latest-checkpoint governance decision
- The next slice is `batch-58: latest verified checkpoint existence/alignment governance`.
- Governance validation must now assert:
  - the latest verified batch contract and report files exist on disk
  - `.forge/STATE.md` keeps the explicit mainline checkpoint fields:
    - `current_step: mainline_complete`
    - `active_batch: none`
    - `authoritative_batch_contract: none`
    - `expected_report_path: none`

56. Post-batch-58 stop-legality decision
- Batch-58 is now considered closed and verified.
- Governance validation now proves:
  - warning hygiene wiring remains present
  - the latest verified checkpoint artifacts exist
  - `.forge/STATE.md` still reflects the explicit mainline-complete checkpoint
- The continuation scan after batch-58 found no further credible adjacent `change_request` in
  current local truth, so the run may remain at explicit `mainline_complete`.

57. Post-batch-58 continuation correction
- One more adjacent governance gap still remained after batch-58:
  the governance validator itself still hardcoded the newest batch/report/feature markers.
- That meant each new verified governance checkpoint would require another validator self-patch,
  which is exactly the kind of repetitive drift this governance layer should absorb automatically.

58. Dynamic-checkpoint governance decision
- The next slice is `batch-59: dynamic latest-checkpoint discovery in governance validation`.
- The governance validator should now derive from `.forge` truth:
  - the latest passing feature id
  - the latest batch report name
  - the corresponding latest batch name
- Those derived values should replace the existing hardcoded checkpoint markers in its assertions.

59. Post-batch-59 stop-legality decision
- Batch-59 is now considered closed and verified.
- Governance validation now discovers the latest verified checkpoint dynamically from `.forge`
  truth instead of relying on hardcoded newest-marker ids.
- The continuation scan after batch-59 found no further credible adjacent `change_request` in
  current local truth, so the run may remain at explicit `mainline_complete`.

60. Post-batch-59 continuation correction
- One more adjacent governance gap still remained after batch-59:
  the governance validator still depended on the fixed `Ran 42 tests` smoke-count marker in handoff
  truth checks.
- That meant any future smoke widening would still force another validator self-patch even if the
  default init contract itself stayed intact.

61. Dynamic-smoke-contract governance decision
- The next slice is `batch-60: remove fixed smoke-count dependency from governance validation`.
- Governance validation should now verify:
  - the default `bash .forge/init.sh` contract is still the documented smoke path
  - handoff truth still records the post-smoke validation markers:
    - `smoke warning hygiene validated`
    - `governance contract validated`
- It should not rely on a hardcoded `Ran N tests` count.

62. Post-batch-60 stop-legality decision
- Batch-60 is now considered closed and verified.
- Governance validation now checks the default smoke contract without hardcoding the current test
  count.
- The continuation scan after batch-60 found no further credible adjacent `change_request` in
  current local truth, so the run may remain at explicit `mainline_complete`.

63. Post-batch-60 continuation correction
- One more adjacent governance gap still remained after batch-60:
  the validator still hardcoded the current checkpoint shape as
  `mainline_complete / active_batch none`.
- That meant the next active `change_request` batch would still force another validator self-patch
  just to keep governance validation aligned with live state.

64. Dynamic-state-shape governance decision
- The next slice is `batch-61: derive current checkpoint shape from STATE.md`.
- Governance validation should now:
  - read `current_step` dynamically from `.forge/STATE.md`
  - read `active_batch` dynamically from `.forge/STATE.md`
  - use those dynamic values in handoff-truth assertions instead of hardcoded checkpoint markers

65. Post-batch-61 stop-legality decision
- Batch-61 is now considered closed and verified.
- Governance validation now derives both:
  - the latest verified checkpoint markers
  - the current checkpoint shape
  from `.forge` truth instead of relying on stale hardcoded ids or state-form assumptions.
- The continuation scan after batch-61 found no further credible adjacent `change_request` in
  current local truth, so the run may remain at explicit `mainline_complete`.

66. Post-batch-61 continuation correction
- One more adjacent governance gap still remained after batch-61:
  the validator still treated `authoritative_batch_contract` and `expected_report_path` as weak
  presence-only fields instead of validating their meaning relative to the current batch shape.
- That meant STATE pointers could still drift semantically while governance validation continued to
  pass.

67. Full-checkpoint-tuple governance decision
- The next slice is `batch-62: validate the full STATE checkpoint tuple`.
- Governance validation should now:
  - read `authoritative_batch_contract` dynamically from `.forge/STATE.md`
  - read `expected_report_path` dynamically from `.forge/STATE.md`
  - validate those fields contextually against `active_batch`

68. Post-batch-62 stop-legality decision
- Batch-62 is now considered closed and verified.
- Governance validation now derives and validates the full STATE checkpoint tuple instead of only a
  partial subset of state fields.
- The continuation scan after batch-62 found no further credible adjacent `change_request` in
  current local truth, so the run may remain at explicit `mainline_complete`.

69. Remaining-work delegation decision
- The user requested an explicit overall progress output plus delegation of the remaining
  development surface to Forge v2.
- Because no credible adjacent slice remains in current local truth, the correct implementation is
  not to invent another product batch, but to record that future work is owned by Forge v2's
  `change_request` intake against the same single-ledger `.forge/` truth.
- This delegation should be visible in `.forge` truth and human handoff docs, not only in chat.

70. Post-batch-63 stop-legality decision
- Batch-63 is now considered closed and verified.
- Remaining work is now explicitly delegated to Forge v2's `change_request` intake in file truth.
- The run may remain at explicit `mainline_complete` until a real future `change_request` is
  introduced against the current single-ledger checkpoint.
