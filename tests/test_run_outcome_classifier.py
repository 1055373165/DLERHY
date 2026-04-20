# ruff: noqa: E402
"""Unit tests for :func:`classify_run_outcome` (spec Phase 2 / P0.2a).

The classifier is a pure function mapping per-stage :class:`StageStatus`
values to a :class:`RunOutcome` decision used by
:meth:`RunExecutionService.reconcile_run_terminal_state`. These tests
pin the decision table exhaustively so the reconciler-level SQL tests
only need to verify the wiring, not the semantics.
"""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
os.environ.setdefault("BOOK_AGENT_TRANSLATION_BACKEND", "echo")
os.environ.setdefault("BOOK_AGENT_TRANSLATION_MODEL", "echo-worker")
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.orchestrator.stage_status import (
    OPTIONAL_PIPELINE_STAGES,
    REQUIRED_PIPELINE_STAGES,
    RunOutcome,
    StageStatus,
    classify_run_outcome,
)


class ClassifyRunOutcomeTests(unittest.TestCase):
    def test_required_stage_translate_is_the_only_required_stage(self) -> None:
        # The classifier's behaviour flows entirely from these constants;
        # pinning them here guards against accidental scope creep in a
        # future refactor that reclassifies review/exports as required.
        self.assertEqual(REQUIRED_PIPELINE_STAGES, frozenset({"translate"}))
        self.assertEqual(
            OPTIONAL_PIPELINE_STAGES,
            frozenset({"review", "bilingual_html", "merged_html"}),
        )

    def test_empty_map_is_running(self) -> None:
        # Classifier is called with a full PIPELINE_STAGES map in prod, but
        # an empty mapping must still land on RUNNING rather than SUCCEEDED
        # — the "all required stages green" check trivially matches when
        # REQUIRED is empty, but translate is required so the fallback is
        # "required stage missing → RUNNING".
        self.assertEqual(classify_run_outcome({}), RunOutcome.RUNNING)

    def test_required_failed_beats_every_other_signal(self) -> None:
        # A required-stage failure is authoritative even when optional
        # stages are still running or succeeded. This prevents a later
        # rule from silently downgrading a hard failure to a warning.
        self.assertEqual(
            classify_run_outcome(
                {
                    "translate": StageStatus.FAILED,
                    "review": StageStatus.SUCCEEDED,
                    "bilingual_html": StageStatus.RUNNING,
                    "merged_html": StageStatus.NOT_STARTED,
                }
            ),
            RunOutcome.FAILED,
        )

    def test_required_not_succeeded_keeps_run_running(self) -> None:
        # Translate still in progress (or not started) ⇒ can't decide a
        # terminal outcome yet; classifier must return RUNNING so the
        # reconciler declines to flip the run.
        for translate_status in (
            StageStatus.NOT_STARTED,
            StageStatus.RUNNING,
            StageStatus.PARTIAL,
        ):
            with self.subTest(translate=translate_status):
                self.assertEqual(
                    classify_run_outcome(
                        {
                            "translate": translate_status,
                            "review": StageStatus.SUCCEEDED,
                            "bilingual_html": StageStatus.SUCCEEDED,
                            "merged_html": StageStatus.SUCCEEDED,
                        }
                    ),
                    RunOutcome.RUNNING,
                )

    def test_all_green_is_succeeded(self) -> None:
        self.assertEqual(
            classify_run_outcome(
                {
                    "translate": StageStatus.SUCCEEDED,
                    "review": StageStatus.SUCCEEDED,
                    "bilingual_html": StageStatus.SUCCEEDED,
                    "merged_html": StageStatus.SUCCEEDED,
                }
            ),
            RunOutcome.SUCCEEDED,
        )

    def test_translate_green_with_optional_not_started_is_succeeded(self) -> None:
        # Core P0.2a semantic: "optional stage NOT_STARTED ⇒ not requested".
        # A translate-only run whose operator never asked for review or
        # exports must reach SUCCEEDED, not hang at RUNNING forever.
        self.assertEqual(
            classify_run_outcome(
                {
                    "translate": StageStatus.SUCCEEDED,
                    "review": StageStatus.NOT_STARTED,
                    "bilingual_html": StageStatus.NOT_STARTED,
                    "merged_html": StageStatus.NOT_STARTED,
                }
            ),
            RunOutcome.SUCCEEDED,
        )

    def test_optional_running_blocks_terminal_transition(self) -> None:
        # SUCCEEDED_WITH_WARNINGS is a terminal label — emitting it while
        # an optional stage is still running would let a later failure
        # transition "unterminate" the run. Classifier must wait.
        self.assertEqual(
            classify_run_outcome(
                {
                    "translate": StageStatus.SUCCEEDED,
                    "review": StageStatus.RUNNING,
                    "bilingual_html": StageStatus.NOT_STARTED,
                    "merged_html": StageStatus.NOT_STARTED,
                }
            ),
            RunOutcome.RUNNING,
        )

    def test_optional_failed_with_required_green_is_succeeded_with_warnings(self) -> None:
        # Degraded-success: translate went green, but the user asked for
        # review and review failed. Run reaches a terminal state but
        # signals the degraded outcome so the UI can show a warning.
        self.assertEqual(
            classify_run_outcome(
                {
                    "translate": StageStatus.SUCCEEDED,
                    "review": StageStatus.FAILED,
                    "bilingual_html": StageStatus.SUCCEEDED,
                    "merged_html": StageStatus.NOT_STARTED,
                }
            ),
            RunOutcome.SUCCEEDED_WITH_WARNINGS,
        )

    def test_optional_failed_with_optional_running_keeps_running(self) -> None:
        # Mixed signal: one optional failed, another still running. We
        # cannot yet decide between SUCCEEDED_WITH_WARNINGS (if the
        # running one succeeds) and still-SUCCEEDED_WITH_WARNINGS (if it
        # also fails) — but either way the run is not yet terminal, so
        # the *running* signal wins and we stay in RUNNING.
        self.assertEqual(
            classify_run_outcome(
                {
                    "translate": StageStatus.SUCCEEDED,
                    "review": StageStatus.FAILED,
                    "bilingual_html": StageStatus.RUNNING,
                    "merged_html": StageStatus.NOT_STARTED,
                }
            ),
            RunOutcome.RUNNING,
        )


if __name__ == "__main__":
    unittest.main()
