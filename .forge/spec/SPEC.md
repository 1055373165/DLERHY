# Translate Agent Whole-Document Readiness Spec

## North Star

Book Agent should behave like a high-fidelity translation system that can safely translate whole
books and papers only after it has proven readiness on representative document families.

For `PDF books`, `EPUB books`, and `PDF papers`, the system should:

- preserve protected artifacts such as code, equations, tables, figures, captions, and inline code
- recover usable heading hierarchy even when the source PDF layout is irregular
- keep reading order stable enough for technical and academic documents
- prefer original assets whenever they exist, and fall back to high-resolution rendering when they
  do not
- degrade explicitly when full layout fidelity is unsafe, instead of silently corrupting content
- use benchmark-backed evidence rather than impression-based confidence to decide whether a whole
  document may proceed

## Explicit Requirements

1. The current benchmark corpus must cover the active certified lanes:
   - `L1` `EPUB-reflowable-tech-book`
   - `L2` `PDF-text-tech-book`
   - `L3` `PDF-text-academic-paper`
   - `L6` `High-artifact-density-paper`
2. Gold labels must exist for the current benchmark samples so parser/export quality is measured
   against explicit truth rather than subjective inspection.
3. The parser/export stack must preserve protected artifacts instead of routing them through normal
   prose translation paths.
4. The parser/export stack must recover heading hierarchy well enough for benchmarked technical and
   academic documents.
5. The parser/export stack must preserve or explicitly link figure/table/equation captions.
6. PDF and EPUB image handling must prefer original assets when available and use high-resolution
   fallback rendering when they are not.
7. The readiness decision must be grounded in executable benchmark outputs:
   - execution summary
   - scorecard
   - lane verdicts
   - certification report
8. High-risk text PDFs must be allowed through the guarded bootstrap path when they are parser-ready text PDFs; explicit risk metadata should remain visible instead of being hidden behind an entry rejection.
9. Whole-document execution must default to `slice-first` on certified lanes.
10. The current readiness claim must remain bounded to the current benchmark corpus; it must not be
   restated as universal cross-format support.
11. Future continuation must enter through explicit `change_request` work against the same single
    `.forge/` ledger.

## Hidden Requirements

- Code blocks, commands, equations, tables, and figure-internal artifacts must not be translated as
  ordinary prose.
- Asset fidelity is not just about images existing; the system must preserve original-resolution
  assets whenever the source format actually provides them.
- High-artifact-density papers may require controlled degradation; that downgrade must be explicit,
  not silent.
- Parser success alone is insufficient; readiness depends on structure, artifact protection,
  reading order, caption linkage, and asset legibility together.
- Sample-level oddities may remain, but lane-level certification must still be explainable from
  file truth.
- Resume sessions must not depend on chat memory to know which document families are currently safe
  to run.

## Constraints

- Work in the single shared checkout.
- Do not open a second live ledger.
- Keep benchmark execution cheap enough that readiness can be rechecked without wasting large
  translation token budgets.
- Prefer targeted parser/export hardening and benchmark probes over large blind reruns.
- Keep whole-document rollout conservative: `slice-first` before `full-rollout`.

## Chosen Problem Framing

The mainline problem is no longer runtime self-heal closure.

The current repo is primarily solving:

- whether the translate agent can faithfully preserve technical document structure and artifacts
- whether that claim is measured strongly enough to justify whole-document execution
- how to keep future scale-up decisions evidence-based instead of intuition-based

## Chosen Solution Topology

The active topology is:

1. classify the document into a benchmarked lane
2. parse structure and protected artifacts
3. preserve assets using original-first extraction plus fallback rendering
4. compare benchmark slices against gold labels
5. derive execution summary, scorecard, and lane verdicts
6. certify or block whole-document execution
7. on certified lanes, run whole documents in `slice-first` mode

## User And Operator Flows

### Certified Lane Flow

1. A document matches a certified lane.
2. Parser/export stack preserves structure and artifacts.
3. The lane remains backed by current benchmark evidence.
4. Whole-document translation proceeds in `slice-first` mode.

### New Layout Flow

1. A new document family or unfamiliar layout appears.
2. It does not inherit certification by default.
3. It receives benchmark-backed spot checks or a new lane sample.
4. Only then may it join the certified set.

### Controlled Degradation Flow

1. A high-artifact-density slice cannot safely recover inner artifact text.
2. The system preserves the artifact and degrades explicitly.
3. It does not pretend the output is full visual/textual fidelity when it is not.

## Edge Cases And Failure Modes

- heading and body text arrive in a single PDF block
- first-page academic frontmatter embeds `ABSTRACT` inside author text
- appendix-style headings use lettered numbering rather than numeric numbering
- figure and caption appear across page or block boundaries
- PDF exposes vector drawings instead of embedded bitmap images
- benchmarked high-risk PDFs are parser-ready but still blocked by a stricter product bootstrap gate

## Non-Goals

- claiming universal support for every PDF/EPUB/paper format
- blind full-document rollout by default
- reopening older runtime self-heal work as the mainline narrative
- spending large translation token budgets just to rediscover already-measured parser issues

## Success Criteria

The current mainline is considered successful when:

- the current benchmark corpus remains executable
- `L1`, `L2`, `L3`, and `L6` remain `go`
- current benchmark execution still shows no parse failures on the active certification set
- current benchmark execution still shows no catastrophic protected-artifact corruption on the
  certified set
- current handoff truth explains both what is certified now and what still needs hardening next
- whole-document runs on certified lanes proceed in controlled `slice-first` mode

## Initial Delivery Strategy

1. Keep the current benchmark/certification truth as the active mainline baseline.
2. Keep high-risk text PDFs on the guarded bootstrap path so parser readiness and product-path readiness stay converged.
3. Distinguish unavailable original-asset opportunities from true parity misses on PDF lanes.
4. Use slice-first whole-document pilots as the next default step on currently certified lanes.
5. Expand the benchmark corpus before widening readiness claims further.
