# Forge State

last_update_time: 2026-04-03 12:24:36 +0800
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
- The current readiness decision is grounded in:
  - `/Users/smy/project/book-agent/artifacts/review/translate-agent-benchmark-execution-summary-current.json`
  - `/Users/smy/project/book-agent/artifacts/review/translate-agent-benchmark-scorecard-current.json`
  - `/Users/smy/project/book-agent/artifacts/review/translate-agent-lane-verdicts-current.json`
  - `/Users/smy/project/book-agent/artifacts/review/translate-agent-readiness-certification-current.md`

failed_items:
- none recorded in the current handoff state

next_items:
- Start controlled slice-first whole-document pilots on the currently certified lanes.
- Expand the benchmark corpus beyond the current nine-sample set before making stronger cross-format generalization claims.
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
- /Users/smy/project/book-agent/artifacts/review/gold-labels/pdf-how-llms-work-001.json
- /Users/smy/project/book-agent/artifacts/review/gold-labels/pdf-agentic-design-001.json
- /Users/smy/project/book-agent/artifacts/review/gold-labels/pdf-attention-paper-001.json
- /Users/smy/project/book-agent/artifacts/review/gold-labels/pdf-forming-teams-001.json
- /Users/smy/project/book-agent/artifacts/review/gold-labels/pdf-wandering-mind-001.json
- /Users/smy/project/book-agent/artifacts/review/gold-labels/pdf-react-001.json
- /Users/smy/project/book-agent/artifacts/review/gold-labels/pdf-epiplexity-001.json
- /Users/smy/project/book-agent/artifacts/review/gold-labels/epub-hands-on-llm-001.json
- /Users/smy/project/book-agent/artifacts/review/gold-labels/epub-agentic-data-001.json
