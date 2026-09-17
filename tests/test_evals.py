"""Evals harness: suites, metrics, thresholds, report files and the CLI contract."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from book_agent.core.config import Settings
from book_agent.evals.runner import SuiteResult, Threshold, run_evals
from book_agent.evals.suites import load_review_pairs
from book_agent.harness.kernel.model import AgentStep, ToolCall
from book_agent.harness.kernel.openai_model import EchoAgentModel
from book_agent.workers.translator import EchoTranslationWorker, TranslationWorkerMetadata

ROOT = Path(__file__).resolve().parents[1]
DATASET = json.loads((ROOT / "evals" / "datasets" / "terminology" / "momentum_book.json").read_text(encoding="utf-8"))


class GlossaryWorker:
    """Echo output plus the expected rendering of every locked term the sentence mentions."""

    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(worker_name="glossary", model_name="glossary-worker", prompt_version="eval.test")

    def translate(self, task):
        result = EchoTranslationWorker().translate(task)
        sources = {sentence.id: sentence.source_text.lower() for sentence in task.current_sentences}
        for segment in result.output.target_segments:
            source = " ".join(sources.get(sentence_id, "") for sentence_id in segment.source_sentence_ids)
            stem_hits = [
                item["target_term"]
                for item in DATASET["locked_terms"]
                if item["source_term"].split()[0].rstrip("s") in source
            ]
            segment.text_zh = segment.text_zh + " " + " ".join(stem_hits)
        return result


class OracleReviewerModel:
    """Reports MISTRANSLATION_SEMANTIC on the annotated problem targets and finishes every packet."""

    def __init__(self, problem_targets: set[str]) -> None:
        self.problem_targets = problem_targets
        self.calls = 0

    def step(self, *, model_name, messages, tools):
        self.calls += 1
        last = messages[-1]
        if last["role"] != "tool":
            return AgentStep(text=None, tool_calls=[ToolCall(f"c{self.calls}", "next_review_batch", {})])
        output = (json.loads(last["content"]).get("output") or {})
        if output.get("done"):
            return AgentStep(text="done")
        if "pairs" not in output:
            return AgentStep(text=None, tool_calls=[ToolCall(f"c{self.calls}", "next_review_batch", {})])
        calls = [
            ToolCall(
                f"c{self.calls}-{pair['alias']}",
                "report_issue",
                {
                    "packet_id": output["packet_id"],
                    "sentence_alias": pair["alias"],
                    "issue_type": "MISTRANSLATION_SEMANTIC",
                    "severity": "high",
                    "confidence": 0.9,
                    "explanation": "与原文不符。",
                },
            )
            for pair in output["pairs"]
            if pair["target"] in self.problem_targets
        ]
        calls.append(ToolCall(f"c{self.calls}-finish", "finish_packet", {"packet_id": output["packet_id"]}))
        return AgentStep(text=None, tool_calls=calls)


class EvalsTests(unittest.TestCase):
    def setUp(self) -> None:
        (ROOT / ".test-tmp").mkdir(exist_ok=True)
        self.tempdir = tempfile.TemporaryDirectory(dir=ROOT / ".test-tmp")
        self.addCleanup(self.tempdir.cleanup)
        self.output = Path(self.tempdir.name) / "report"
        self.settings = Settings(translation_backend="echo", translation_model="echo-worker")

    def _suite(self, report, name):
        return next(suite for suite in report["suites"] if suite["suite"] == name)

    def test_terminology_and_export_pass_with_a_worker_that_honours_the_glossary(self) -> None:
        report = run_evals(
            ["export"],
            output_dir=self.output,
            settings=self.settings,
            translation_worker=GlossaryWorker(),
            agent_model=EchoAgentModel(),
        )
        terminology = self._suite(report, "terminology")
        self.assertEqual(terminology["metrics"]["coverage"], 1.0)
        self.assertEqual(terminology["metrics"]["locked_term_consistency"], 1.0, terminology["cases"])
        self.assertGreaterEqual(terminology["metrics"]["term_occurrences"], 10)
        export = self._suite(report, "export")
        self.assertTrue(export["passed"], export)
        self.assertGreater(export["metrics"]["checks"], 0)
        self.assertTrue(report["passed"])
        self.assertEqual(report["harness"]["worker"], "glossary-worker")
        written = json.loads((self.output / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(written["suites"], report["suites"])
        self.assertIn("| export | pass |", (self.output / "summary.md").read_text(encoding="utf-8"))
        self.assertFalse((self.output / "work" / "eval.db").exists())

    def test_echo_translation_fails_the_consistency_threshold(self) -> None:
        report = run_evals(["terminology"], output_dir=self.output, settings=self.settings)
        terminology = self._suite(report, "terminology")
        self.assertEqual(terminology["metrics"]["locked_term_consistency"], 0.0)
        self.assertEqual(terminology["failed_thresholds"], ["locked_term_consistency=0.0 < 0.95"])
        self.assertTrue(terminology["cases"])
        self.assertFalse(report["passed"])
        self.assertEqual(report["harness"]["agent_model"], "EchoAgentModel")

    def test_review_metrics_follow_what_the_reviewer_reports(self) -> None:
        pairs = load_review_pairs()
        problems = {pair["target"] for pair in pairs if pair["expected"]}
        report = run_evals(
            ["review"],
            output_dir=self.output,
            settings=self.settings,
            translation_worker=EchoTranslationWorker(),
            agent_model=OracleReviewerModel(problems),
        )
        review = self._suite(report, "review")
        self.assertEqual(review["metrics"]["precision"], 1.0, review)
        self.assertEqual(review["metrics"]["recall"], 1.0, review)
        by_id = {case["id"]: case for case in review["cases"]}
        self.assertEqual(by_id["r02"]["reported"], ["MISTRANSLATION_SEMANTIC"])
        self.assertEqual(by_id["r01"]["reported"], [])
        # r04 expects OMISSION: found on the right pair, wrong type.
        self.assertLess(review["metrics"]["type_accuracy"], 1.0)

        silent = run_evals(
            ["review"], output_dir=self.output, settings=self.settings, translation_worker=EchoTranslationWorker()
        )
        review = self._suite(silent, "review")
        self.assertIsNone(review["metrics"]["precision"])
        self.assertEqual(review["metrics"]["recall"], 0.0)
        self.assertFalse(review["passed"])

    def test_structure_suite_matches_the_golden_snapshots(self) -> None:
        report = run_evals(["structure"], output_dir=self.output, settings=self.settings)
        structure = self._suite(report, "structure")
        self.assertEqual(structure["metrics"]["block_type_agreement"], 1.0, structure["cases"])
        self.assertTrue(report["passed"])

    def test_threshold_rules_and_unknown_suites(self) -> None:
        result = SuiteResult("x", {"a": 0.5, "b": None}, [Threshold("a", 0.5), Threshold("b", 0.1)])
        self.assertEqual(result.failed_thresholds, ["b=None < 0.1"])
        skipped = SuiteResult("x", {}, [Threshold("a", 1.0)], skipped_reason="no checkout")
        self.assertEqual(skipped.failed_thresholds, [])
        with self.assertRaises(ValueError):
            run_evals(["nope"], output_dir=self.output, settings=self.settings)

    def test_a_crashing_suite_is_reported_as_failed(self) -> None:
        from book_agent.evals import suites

        def boom(context):
            raise RuntimeError("kaput")

        original = suites.SUITES["structure"]
        suites.SUITES["structure"] = boom
        try:
            report = run_evals(["structure"], output_dir=self.output, settings=self.settings)
        finally:
            suites.SUITES["structure"] = original
        structure = self._suite(report, "structure")
        self.assertFalse(structure["passed"])
        self.assertIn("RuntimeError: kaput", structure["notes"][0])


if __name__ == "__main__":
    unittest.main()
