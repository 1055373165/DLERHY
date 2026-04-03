# Forge Decisions

1. Workspace decision
- Stay in the single main repo checkout on `main`.
- Do not create a second live worktree or a second live ledger.

2. Mainline decision
- The current active mainline is `translate-agent whole-document readiness and high-fidelity translation hardening`.
- The older `runtime self-heal closure` line is completed background capability, not the current mainline owner of repo momentum.

3. Scope control decision
- Prioritize work that improves whole-document translation readiness for `PDF books`, `EPUB books`, and `PDF papers`.
- Prioritize parser/export fidelity over unrelated product polish.
- Do not reopen runtime/governance work unless it directly blocks translate-agent readiness or whole-document execution.

4. Readiness interpretation decision
- The current measured benchmark verdict is `go`, not merely “looks good”.
- This means the system is approved for `controlled, slice-first whole-document execution` on the currently certified lanes.
- This does not mean the project has universal cross-format support for every PDF/EPUB layout in the wild.

5. Certified lane decision
- `L1` `EPUB-reflowable-tech-book`: `go`
- `L2` `PDF-text-tech-book`: `go`
- `L3` `PDF-text-academic-paper`: `go`
- `L6` `High-artifact-density-paper`: `go`
- `L6` remains a `Tier C` certification: preserve content/artifact fidelity first, and degrade explicitly when inner artifact text cannot be recovered safely.

6. Current follow-up decision
- High-risk text PDFs now enter the guarded bootstrap path through the normal product route; bootstrap-gate removal is no longer the active blocker.
- Vector-only and otherwise non-extractable PDF pages should be recorded as unavailable original-asset opportunities, not counted as original-extraction failures.
- The closest next slice is now `fragmented composite PDF figure parity`, especially where `L6` still falls back because a single figure is composed from multiple embedded image fragments.
- After that, the next closest slice is `controlled slice-first whole-document pilots` on certified lanes.
- After that, the next closest slice is `benchmark corpus expansion`, so readiness claims can extend beyond the current nine-sample set.

7. Operating-mode decision
- Default operating mode for certified lanes is `slice-first`.
- Do not switch to blind full-document rollout as the default.
- New document families or unfamiliar layouts must still go through benchmark-backed spot checks before they inherit the current readiness claim.

8. Current write-set decision
- Prefer staying inside:
  - `src/book_agent/domain/structure/pdf.py`
  - `src/book_agent/domain/structure/epub.py`
  - `src/book_agent/services/export.py`
  - `tests/test_pdf_support.py`
  - `artifacts/review/scripts/run_translate_agent_benchmark_execution.py`
  - `artifacts/review/scripts/generate_translate_agent_benchmark_scorecard.py`
  - `artifacts/review/scripts/generate_translate_agent_lane_verdicts.py`
  - `artifacts/review/translate-agent-benchmark-manifest-current.yaml`
  - `artifacts/review/gold-labels/*.json`
  - `artifacts/review/translate-agent-benchmark-*.json`
  - `artifacts/review/translate-agent-lane-verdicts-current.*`
  - `artifacts/review/translate-agent-readiness-certification-current.md`
  - `snapshot.md`
  - `progress.txt`
  - `docs/mainline-progress.md`
- Expand only if blocked by a real dependency.

9. Verification decision
- Every readiness-hardening slice should re-run the smallest dependency-closed proof that changes:
  - targeted `unittest` for parser/export regressions
  - `python3 -m py_compile` for touched Python files
  - `artifacts/review/scripts/run_translate_agent_benchmark_execution.py`
  - `artifacts/review/scripts/generate_translate_agent_benchmark_scorecard.py`
  - `artifacts/review/scripts/generate_translate_agent_lane_verdicts.py`
  - `python3 -m json.tool` on refreshed JSON artifacts when benchmark outputs change

10. Handoff decision
- `.forge` truth must now describe translate-agent readiness as the active mainline.
- Human-facing handoff files must agree with that truth:
  - `/Users/smy/project/book-agent/snapshot.md`
  - `/Users/smy/project/book-agent/progress.txt`
  - `/Users/smy/project/book-agent/docs/mainline-progress.md`
- Future continuation should enter as a change request against this translate-agent mainline truth, not by silently reviving the old runtime self-heal narrative.
