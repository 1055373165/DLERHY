# Forge State

last_update_time: 2026-04-03 21:10:52 +0800
mode: resume
current_step: mainline_complete
active_batch: none
authoritative_batch_contract: none
expected_report_path: none
active_feature_ids:
- none

active_worker_slot:
- worker_id: none
- worker_nickname: none
- model: none
- reasoning: none
- dispatch_time: none
- last_harvest_check: none

completed_items:
- Previous `runtime self-heal closure` and Forge v2 governance hardening remain completed and reusable, but they are no longer the active mainline.
- The active product mainline is now `translate-agent whole-document readiness and high-fidelity translation hardening`.
- Translate-agent benchmark coverage is now complete for the current nine-sample certification set across `L1 / L2 / L3 / L6`.
- Current benchmark execution is `overall_verdict = go` for controlled, slice-first whole-document execution on the certified lanes.
- `L1` `EPUB-reflowable-tech-book` is measured `go`.
- `L2` `PDF-text-tech-book` is measured `go`.
- `L3` `PDF-text-academic-paper` is measured `go`.
- `L6` `High-artifact-density-paper` is measured `go` at `Tier C`, with explicit artifact preservation and controlled degradation when inner artifact text cannot be recovered safely.
- High-risk text PDFs now enter the guarded bootstrap path through the normal product route instead of requiring a direct parser probe.
- PDF asset provenance now distinguishes true original-image opportunities from vector-only or otherwise non-extractable pages, so fallback renders on those pages no longer masquerade as original-extraction misses.
- Fragmented composite PDF figures are now treated as noncanonical original-asset opportunities on the current certification set when no single extractable source image exists.
- Current benchmark execution has `9/9` executed samples, `0` parse failures, and `0` catastrophic protected-artifact corruption events across the certified lane set.
- A generated pilot pack now exists for the certified lanes at `/Users/smy/project/book-agent/artifacts/review/translate-agent-pilot-pack-current.json` and `/Users/smy/project/book-agent/artifacts/review/translate-agent-pilot-pack-current.md`.
- The first real `L1` slice-first whole-document pilot has been executed through the normal product path:
  - report: `/Users/smy/project/book-agent/artifacts/real-book-live/translate-agent-pilot-epub-hands-on-llm-001/report.json`
  - run_id: `a7a94e52-fdac-4678-a4bb-ba16ab17583f`
  - stop_reason: `pilot.slice_target_reached`
  - translated_packet_count: `15`
  - measured usage: `token_in 19817`, `token_out 4072`, `cost_usd 0.00613001`
- Slice-first pilot control has now been hardened so claim budget respects `completed + inflight` packets instead of only completed packets.
- A strict-cap smoke proof now exists for the corrected pilot-control path:
  - report: `/Users/smy/project/book-agent/artifacts/real-book-live/translate-agent-pilot-epub-hands-on-llm-001-strict-cap-smoke/report.json`
  - run_id: `6133c4e0-0e8c-46e9-bc14-55a4cb6e9158`
  - `max_completed_packets = 2`
  - final translated_packet_count: `2`
- First-pass slice-first pilots have now been executed across all certified lanes:
  - `L1`: `/Users/smy/project/book-agent/artifacts/real-book-live/translate-agent-pilot-epub-hands-on-llm-001/report.json`
  - `L2`: `/Users/smy/project/book-agent/artifacts/real-book-live/translate-agent-pilot-pdf-agentic-design-001/report.json`
  - `L3`: `/Users/smy/project/book-agent/artifacts/real-book-live/translate-agent-pilot-pdf-attention-paper-001/report.json`
  - `L6`: `/Users/smy/project/book-agent/artifacts/real-book-live/translate-agent-pilot-pdf-epiplexity-001/report.json`
- All four certified-lane pilot runs paused legally with `pilot.slice_target_reached` and no provider / bootstrap / parser stop reason displaced the slice-first control path.
- Pilot run-level evidence is now summarized in:
  - `/Users/smy/project/book-agent/artifacts/review/translate-agent-pilot-summary-current.json`
  - `/Users/smy/project/book-agent/artifacts/review/translate-agent-pilot-summary-current.md`
- Current pilot evidence indicates the higher-value next default slice is now benchmark corpus expansion, not deeper immediate resume on already-certified lanes.
- A corpus-expansion draft now exists at `/Users/smy/project/book-agent/artifacts/review/translate-agent-benchmark-corpus-expansion-draft.yaml`.
- The next expansion wave now has seeded gold-label stubs for:
  - `epub-agentic-theories-001`
  - `epub-managing-memory-001`
  - `pdf-building-ai-agents-001`
  - `pdf-173140-001`
  - `pdf-man-solved-market-zh-001`
  - `pdf-self-observation-zh-001`
- The expansion draft introduces two new candidate lanes:
  - `L4` `PDF-ocr-heavy-book`
  - `L5` `PDF-mixed-layout-book`
- The first expansion pair is no longer stub-only:
  - `pdf-building-ai-agents-001` is now `annotated_v1`
  - `pdf-173140-001` is now `annotated_v1`
- A minimal expansion-wave parser/export probe now exists at:
  - `/Users/smy/project/book-agent/artifacts/review/translate-agent-benchmark-expansion-wave1.yaml`
  - `/Users/smy/project/book-agent/artifacts/review/translate-agent-benchmark-expansion-wave1-scorecard.json`
  - `/Users/smy/project/book-agent/artifacts/review/translate-agent-benchmark-expansion-wave1-execution-summary.json`
  - `/Users/smy/project/book-agent/artifacts/review/translate-agent-benchmark-expansion-wave1-lane-verdicts.json`
- Expansion wave 1 is now measured `overall_verdict = go`:
  - `pdf-building-ai-agents-001` (`L2`) is `go`
  - `pdf-173140-001` (`L5`) is `go`
- Expansion wave 1 is no longer blocked on measured `L2/L5` parser failures; it now serves as reusable evidence that one additional PDF tech-book family and one mixed-layout PDF book family pass parser/export readiness without translation-token spend.
- The current readiness decision is grounded in:
  - `/Users/smy/project/book-agent/artifacts/review/translate-agent-benchmark-execution-summary-current.json`
  - `/Users/smy/project/book-agent/artifacts/review/translate-agent-benchmark-scorecard-current.json`
  - `/Users/smy/project/book-agent/artifacts/review/translate-agent-lane-verdicts-current.json`
  - `/Users/smy/project/book-agent/artifacts/review/translate-agent-readiness-certification-current.md`

failed_items:
- none recorded in the current handoff state

next_items:
- Fold the new expansion-wave measured `go` result into the corpus-expansion draft and active readiness narrative.
- Annotate the first `L4` OCR-heavy expansion sample before any translation-token pilot spend on uncertified lanes.
- Prefer a smallest parser/export probe for that first `L4` sample before deciding whether OCR-heavy material deserves a new pilot pack.
- Continue corpus expansion with either `epub-agentic-theories-001` or the first `L4` sample rather than reopening already-cleared `L2/L5` blockers.
- Only reopen deeper certified-lane resumes if later spot checks expose a concrete lane-specific issue or if a product objective requires more than first-pass pilot evidence.
- Reopen PDF asset-parity hardening only if a future document exposes a true extractable-original miss instead of a vector-only or noncanonical composite case.

working_tree_scope:
- /Users/smy/project/book-agent/.forge/STATE.md
- /Users/smy/project/book-agent/.forge/DECISIONS.md
- /Users/smy/project/book-agent/.forge/log.md
- /Users/smy/project/book-agent/.forge/spec/SPEC.md
- /Users/smy/project/book-agent/.forge/spec/FEATURES.json
- /Users/smy/project/book-agent/progress.txt
- /Users/smy/project/book-agent/snapshot.md
- /Users/smy/project/book-agent/docs/mainline-progress.md
- /Users/smy/project/book-agent/src/book_agent/domain/structure/pdf.py
- /Users/smy/project/book-agent/src/book_agent/domain/structure/epub.py
- /Users/smy/project/book-agent/src/book_agent/services/export.py
- /Users/smy/project/book-agent/tests/test_pdf_support.py
- /Users/smy/project/book-agent/artifacts/review/scripts/run_translate_agent_benchmark_execution.py
- /Users/smy/project/book-agent/artifacts/review/scripts/generate_translate_agent_benchmark_scorecard.py
- /Users/smy/project/book-agent/artifacts/review/scripts/generate_translate_agent_lane_verdicts.py
- /Users/smy/project/book-agent/artifacts/review/translate-agent-benchmark-manifest-current.yaml
- /Users/smy/project/book-agent/artifacts/review/translate-agent-benchmark-execution-summary-current.json
- /Users/smy/project/book-agent/artifacts/review/translate-agent-benchmark-scorecard-current.json
- /Users/smy/project/book-agent/artifacts/review/translate-agent-lane-verdicts-current.json
- /Users/smy/project/book-agent/artifacts/review/translate-agent-readiness-certification-current.md
- /Users/smy/project/book-agent/artifacts/review/scripts/generate_translate_agent_pilot_pack.py
- /Users/smy/project/book-agent/artifacts/review/translate-agent-pilot-pack-current.json
- /Users/smy/project/book-agent/artifacts/review/translate-agent-pilot-pack-current.md
- /Users/smy/project/book-agent/artifacts/review/translate-agent-pilot-summary-current.json
- /Users/smy/project/book-agent/artifacts/review/translate-agent-pilot-summary-current.md
- /Users/smy/project/book-agent/artifacts/review/translate-agent-benchmark-corpus-expansion-draft.yaml
- /Users/smy/project/book-agent/artifacts/review/translate-agent-benchmark-expansion-wave1.yaml
- /Users/smy/project/book-agent/artifacts/review/translate-agent-benchmark-expansion-wave1-scorecard.json
- /Users/smy/project/book-agent/artifacts/review/translate-agent-benchmark-expansion-wave1-execution-summary.json
- /Users/smy/project/book-agent/artifacts/review/translate-agent-benchmark-expansion-wave1-lane-verdicts.json
- /Users/smy/project/book-agent/artifacts/review/execution-pack-expansion-wave1/
- /Users/smy/project/book-agent/scripts/run_real_book_live.py
- /Users/smy/project/book-agent/tests/test_real_book_live_reporting.py
- /Users/smy/project/book-agent/artifacts/real-book-live/translate-agent-pilot-epub-hands-on-llm-001/report.json
- /Users/smy/project/book-agent/artifacts/real-book-live/translate-agent-pilot-epub-hands-on-llm-001-strict-cap-smoke/report.json
- /Users/smy/project/book-agent/artifacts/real-book-live/translate-agent-pilot-pdf-agentic-design-001/report.json
- /Users/smy/project/book-agent/artifacts/real-book-live/translate-agent-pilot-pdf-attention-paper-001/report.json
- /Users/smy/project/book-agent/artifacts/real-book-live/translate-agent-pilot-pdf-epiplexity-001/report.json
- /Users/smy/project/book-agent/artifacts/review/gold-labels/pdf-how-llms-work-001.json
- /Users/smy/project/book-agent/artifacts/review/gold-labels/pdf-agentic-design-001.json
- /Users/smy/project/book-agent/artifacts/review/gold-labels/pdf-attention-paper-001.json
- /Users/smy/project/book-agent/artifacts/review/gold-labels/pdf-forming-teams-001.json
- /Users/smy/project/book-agent/artifacts/review/gold-labels/pdf-wandering-mind-001.json
- /Users/smy/project/book-agent/artifacts/review/gold-labels/pdf-react-001.json
- /Users/smy/project/book-agent/artifacts/review/gold-labels/pdf-epiplexity-001.json
- /Users/smy/project/book-agent/artifacts/review/gold-labels/pdf-building-ai-agents-001.json
- /Users/smy/project/book-agent/artifacts/review/gold-labels/pdf-173140-001.json
- /Users/smy/project/book-agent/artifacts/review/gold-labels/epub-hands-on-llm-001.json
- /Users/smy/project/book-agent/artifacts/review/gold-labels/epub-agentic-data-001.json
