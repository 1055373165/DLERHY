# Translate Agent Readiness Snapshot

Last Updated: 2026-04-03 21:10 +0800
Workspace: `/Users/smy/project/book-agent`
Branch: `main`
Worktree Policy: single live worktree only

## 1. Snapshot Purpose

This snapshot is the authoritative human-readable handoff for the current translate-agent mainline.

It exists so that:

- the next programmer can recover the active translation-readiness context without replaying chat
- `forge-v2` can resume from the current `.forge/` truth without creating a second ledger
- future change requests start from measured readiness facts rather than from stale runtime-self-heal context

## 2. What The Real Mainline Is

The current mainline is no longer runtime self-heal closure.

The active mainline is:

`translate-agent high-fidelity whole-document translation readiness`
+
`benchmark-backed certification for PDF books / EPUB books / PDF papers`
+
`controlled slice-first rollout instead of blind full-document execution`

The north star is:

- before translating entire books or papers at scale, the system should prove that it can
  - preserve protected artifacts
  - recover heading hierarchy
  - maintain reading order
  - link figures/tables/equations to captions
  - preserve or safely degrade assets
  - keep code / equations / tables out of ordinary prose translation paths

## 3. Current Readiness Verdict

Current certification scope: `translate-agent-readiness-current-sample-set`

Current measured verdict: `overall go`

Grounding artifacts:

- `/Users/smy/project/book-agent/artifacts/review/translate-agent-benchmark-execution-summary-current.json`
- `/Users/smy/project/book-agent/artifacts/review/translate-agent-benchmark-scorecard-current.json`
- `/Users/smy/project/book-agent/artifacts/review/translate-agent-lane-verdicts-current.json`
- `/Users/smy/project/book-agent/artifacts/review/translate-agent-readiness-certification-current.md`

Certified lanes:

- `L1` `EPUB-reflowable-tech-book` -> `go`
- `L2` `PDF-text-tech-book` -> `go`
- `L3` `PDF-text-academic-paper` -> `go`
- `L6` `High-artifact-density-paper` -> `go`

## 4. What This Means Operationally

The current stack is certified for:

- controlled, slice-first whole-document execution on the certified lanes
- benchmark-backed release decisions
- high-fidelity preservation of protected artifacts across the current sample set

The current stack is not yet claiming:

- universal support for every PDF/EPUB format in the wild
- blind full-document rollout as the default execution mode
- complete original-asset extraction parity on every PDF slice

## 5. Important Boundaries

- Certification currently applies to the nine-sample benchmark corpus, not to every unseen document family.
- High-risk text PDFs now enter the guarded bootstrap path through the normal product route instead of requiring a direct parser probe.
- `L2` and `L6` still include fallback-rendered PDF asset slices; those slices are accepted because legibility passes, and current provenance now distinguishes vector-only/non-extractable pages, noncanonical fragmented composites, and true original-asset parity misses.
- `L6` is effectively a `Tier C` lane: content fidelity plus explicit artifact preservation and controlled degradation when inner artifact text cannot be safely recovered.

## 6. Core Files On The Active Mainline

- `/Users/smy/project/book-agent/src/book_agent/domain/structure/pdf.py`
- `/Users/smy/project/book-agent/src/book_agent/domain/structure/epub.py`
- `/Users/smy/project/book-agent/src/book_agent/services/export.py`
- `/Users/smy/project/book-agent/tests/test_pdf_support.py`
- `/Users/smy/project/book-agent/artifacts/review/scripts/run_translate_agent_benchmark_execution.py`
- `/Users/smy/project/book-agent/artifacts/review/scripts/generate_translate_agent_benchmark_scorecard.py`
- `/Users/smy/project/book-agent/artifacts/review/scripts/generate_translate_agent_lane_verdicts.py`
- `/Users/smy/project/book-agent/artifacts/review/translate-agent-benchmark-manifest-current.yaml`
- `/Users/smy/project/book-agent/artifacts/review/gold-labels/`

## 7. Most Recent Meaningful Closure

The current line has already closed the benchmark-program phase:

- benchmark corpus is populated and annotated
- lane verdict generation is working
- execution scorecard generation is working
- parser hardening for EPUB / PDF / paper lanes is in place
- current certification artifacts agree on `overall go`

## 8. Next Todo

1. Fold the new expansion-wave `overall go` result into active readiness truth and use it as reusable evidence.
2. Annotate and probe the first `L4` OCR-heavy sample before any new whole-document pilot spend on uncertified lanes.
3. Continue EPUB family expansion with another low-token parser/export probe when that is a better next fit than `L4`.
4. Keep already-certified and already-cleared `L2/L5` expansion slices closed unless later spot checks reopen them with real evidence.

## 9. Pilot Evidence

- Pilot pack:
  - `/Users/smy/project/book-agent/artifacts/review/translate-agent-pilot-pack-current.json`
  - `/Users/smy/project/book-agent/artifacts/review/translate-agent-pilot-pack-current.md`
- First-pass certified-lane pilots:
  - `L1`: `/Users/smy/project/book-agent/artifacts/real-book-live/translate-agent-pilot-epub-hands-on-llm-001/report.json`
    - paused legally with `pilot.slice_target_reached`
    - translated `15` packets
    - usage `token_in 19817 / token_out 4072 / cost_usd 0.00613001`
  - `L2`: `/Users/smy/project/book-agent/artifacts/real-book-live/translate-agent-pilot-pdf-agentic-design-001/report.json`
    - paused legally with `pilot.slice_target_reached`
    - translated `10` packets
    - usage `token_in 13222 / token_out 2364 / cost_usd 0.00361444`
  - `L3`: `/Users/smy/project/book-agent/artifacts/real-book-live/translate-agent-pilot-pdf-attention-paper-001/report.json`
    - paused legally with `pilot.slice_target_reached`
    - translated `8` packets
    - usage `token_in 10909 / token_out 2365 / cost_usd 0.00320914`
  - `L6`: `/Users/smy/project/book-agent/artifacts/real-book-live/translate-agent-pilot-pdf-epiplexity-001/report.json`
    - paused legally with `pilot.slice_target_reached`
    - translated `6` packets
    - usage `token_in 8561 / token_out 2016 / cost_usd 0.00263092`
- Strict-cap smoke proof:
  - `/Users/smy/project/book-agent/artifacts/real-book-live/translate-agent-pilot-epub-hands-on-llm-001-strict-cap-smoke/report.json`
  - `max_completed_packets = 2`
  - final translated packet count = `2`
- Pilot summary:
  - `/Users/smy/project/book-agent/artifacts/review/translate-agent-pilot-summary-current.json`
  - `/Users/smy/project/book-agent/artifacts/review/translate-agent-pilot-summary-current.md`
- Corpus expansion draft:
  - `/Users/smy/project/book-agent/artifacts/review/translate-agent-benchmark-corpus-expansion-draft.yaml`
- First expansion-wave probe:
  - `/Users/smy/project/book-agent/artifacts/review/translate-agent-benchmark-expansion-wave1.yaml`
  - `/Users/smy/project/book-agent/artifacts/review/translate-agent-benchmark-expansion-wave1-scorecard.json`
  - `/Users/smy/project/book-agent/artifacts/review/translate-agent-benchmark-expansion-wave1-execution-summary.json`
  - `/Users/smy/project/book-agent/artifacts/review/translate-agent-benchmark-expansion-wave1-lane-verdicts.json`
- Wave 1 measured result:
  - `overall = go`
  - `L2 = go`
  - `L5 = go`
