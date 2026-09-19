"""Reviewer Agent: sampling, the tool loop, blocking and dedupe rules, closing and escalation, plan wiring."""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.pool import StaticPool

from book_agent.core.ids import stable_id
from book_agent.domain.enums import (
    ActionStatus,
    ActionType,
    AgentTurnStatus,
    Detector,
    DocumentRunType,
    IssueStatus,
    PacketStatus,
    RootCauseLayer,
    Severity,
)
from book_agent.domain.models.review import IssueAction, ReviewIssue
from book_agent.domain.models.translation import TranslationPacket, TranslationRun
from book_agent.harness.agents.reviewer import (
    MODE_FULL,
    MODE_SAMPLED,
    MODE_SKIP,
    FinishPacketArgs,
    NextReviewBatchArgs,
    ReportIssueArgs,
    ReviewerAgent,
    finish_packet,
    next_review_batch,
    report_issue,
    select_review_packets,
)
from book_agent.harness.kernel.model import AgentStep, ToolCall
from book_agent.harness.kernel.turn import AgentTurnRunner
from book_agent.harness.tools.registry import ToolContext
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.infra.repositories.review import ReviewRepository
from book_agent.infra.repositories.translation import TranslationRepository
from book_agent.orchestrator.bootstrap import BootstrapOrchestrator
from book_agent.orchestrator.rerun import style_hints_for_issue
from book_agent.orchestrator.run_plan import plan_for_run
from book_agent.orchestrator.stage_gate import STAGE_DEPENDENCIES
from book_agent.services.issues import IssueService
from book_agent.services.translation import TranslationService
from tests.test_translation_worker_abstraction import CHAPTER_XHTML, CONTAINER_XML, CONTENT_OPF, NAV_XHTML


class ReviewerAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.engine = build_engine(
            f"sqlite+pysqlite:///{Path(self.tempdir.name) / 'reviewer.db'}",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        epub_path = Path(self.tempdir.name) / "sample.epub"
        with zipfile.ZipFile(epub_path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", CONTENT_OPF)
            archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
            archive.writestr("OEBPS/chapter1.xhtml", CHAPTER_XHTML)
        artifacts = BootstrapOrchestrator().bootstrap_epub(epub_path)
        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()
        self.document_id = artifacts.document.id
        self.packet_ids = [packet.id for packet in artifacts.translation_packets]
        with self.session_factory() as session:
            service = TranslationService(TranslationRepository(session))
            for packet_id in self.packet_ids:
                service.execute_packet(packet_id)
            session.commit()

    # --- helpers ------------------------------------------------------------------

    def _start(self, session, mode=MODE_FULL):
        return ReviewerAgent(session).start_turn(document_id=self.document_id, model_name="m", mode=mode)

    def _ctx(self, session, turn_id) -> ToolContext:
        return ToolContext(session=session, document_id=self.document_id, agent_kind="reviewer", turn_id=turn_id)

    def _report(self, session, ctx, packet_id, **overrides):
        args = {
            "packet_id": packet_id,
            "sentence_alias": "S1",
            "issue_type": "MISTRANSLATION_SEMANTIC",
            "severity": "high",
            "confidence": 0.9,
            "explanation": "把主语译反了。",
            "suggested_target_text": "解决这个问题的办法是上下文工程。",
        }
        args.update(overrides)
        return report_issue(ctx, ReportIssueArgs(**args))

    def _model_issue(self, session) -> ReviewIssue:
        return session.scalars(select(ReviewIssue).where(ReviewIssue.detector == Detector.MODEL)).one()

    # --- sampling -------------------------------------------------------------------

    def test_sampling_prefers_suspicious_packets_and_keeps_reading_order(self) -> None:
        with self.session_factory() as session:
            full = select_review_packets(session, self.document_id, mode=MODE_FULL)
            self.assertEqual(sorted(full), sorted(self.packet_ids))
            self.assertEqual(select_review_packets(session, self.document_id, mode=MODE_SKIP), [])
            suspicious = full[-1]
            run = session.scalars(select(TranslationRun).where(TranslationRun.packet_id == suspicious)).one()
            run.error_code = "output_sentence_coverage_incomplete"
            session.flush()
            sampled = select_review_packets(session, self.document_id, mode=MODE_SAMPLED, per_chapter=1)
            self.assertEqual(sampled, [suspicious])
            two = select_review_packets(session, self.document_id, mode=MODE_SAMPLED, per_chapter=2)
            self.assertEqual(len(two), 2)
            self.assertEqual(two, [packet_id for packet_id in full if packet_id in two])

    # --- tools through the real turn runner -------------------------------------------

    def test_turn_walks_the_queue_and_files_a_blocking_issue_with_a_repair_action(self) -> None:
        with self.session_factory() as session:
            seed = self._start(session)
            session.commit()

        class QueueModel:
            """Reads each batch, reports one problem on the first packet, finishes every packet."""

            def __init__(self) -> None:
                self.calls = 0
                self.reported = False

            def step(self, *, model_name, messages, tools):
                self.calls += 1
                last = messages[-1]
                if last["role"] != "tool":
                    return AgentStep(text=None, tool_calls=[ToolCall(f"c{self.calls}", "next_review_batch", {})])
                result = json.loads(last["content"])
                output = result.get("output") or {}
                if output.get("done"):
                    return AgentStep(text="审校完成。")
                if "pairs" in output:
                    if not self.reported:
                        self.reported = True
                        return AgentStep(
                            text=None,
                            tool_calls=[
                                ToolCall(
                                    f"c{self.calls}",
                                    "report_issue",
                                    {
                                        "packet_id": output["packet_id"],
                                        "sentence_alias": "S1",
                                        "issue_type": "MISTRANSLATION_SEMANTIC",
                                        "severity": "high",
                                        "confidence": 0.92,
                                        "explanation": "意思译错。",
                                    },
                                ),
                                ToolCall(f"c{self.calls}b", "finish_packet", {"packet_id": output["packet_id"]}),
                            ],
                        )
                    return AgentStep(text=None, tool_calls=[ToolCall(f"c{self.calls}", "finish_packet", {"packet_id": output["packet_id"]})])
                return AgentStep(text=None, tool_calls=[ToolCall(f"c{self.calls}", "next_review_batch", {})])

        runner = AgentTurnRunner(
            session_factory=self.session_factory,
            model=QueueModel(),
            registry=ReviewerAgent.registry(),
            policy=ReviewerAgent.policy(),
        )
        outcome = runner.run(seed.turn_id)

        self.assertEqual(outcome.status, AgentTurnStatus.SUCCEEDED, outcome.stop_reason)
        with self.session_factory() as session:
            issue = self._model_issue(session)
            self.assertEqual(issue.issue_type, "MISTRANSLATION_SEMANTIC")
            self.assertEqual(issue.root_cause_layer, RootCauseLayer.TRANSLATION)
            self.assertTrue(issue.blocking)
            self.assertEqual(issue.evidence_json["reason"], "model_review")
            self.assertTrue(issue.evidence_json["actual_target_text"].startswith("ZH::"))
            action = session.scalars(select(IssueAction).where(IssueAction.issue_id == issue.id)).one()
            self.assertEqual((action.action_type, action.status), (ActionType.RERUN_PACKET, ActionStatus.PLANNED))
            ctx = self._ctx(session, seed.turn_id)
            self.assertTrue(next_review_batch(ctx, NextReviewBatchArgs())["done"])

    def test_batch_lists_aliases_targets_and_known_issues(self) -> None:
        with self.session_factory() as session:
            seed = self._start(session)
            ctx = self._ctx(session, seed.turn_id)
            batch = next_review_batch(ctx, NextReviewBatchArgs())
            self.assertFalse(batch["done"])
            self.assertEqual(batch["pairs"][0]["alias"], "S1")
            self.assertTrue(batch["pairs"][0]["target"].startswith("ZH::"))
            finish_packet(ctx, FinishPacketArgs(packet_id=batch["packet_id"]))
            # finish_packet only counts once its tool result is on the ledger; the runner writes that.
            with self.assertRaises(Exception):
                finish_packet(ctx, FinishPacketArgs(packet_id="00000000-0000-0000-0000-000000000000"))

    # --- rules ------------------------------------------------------------------------

    def test_batch_shows_the_latest_attempt_even_when_the_old_one_was_not_superseded(self) -> None:
        packet_id = self.packet_ids[1]

        class PrefixWorker:
            def metadata(self):
                from book_agent.workers.translator import TranslationWorkerMetadata

                return TranslationWorkerMetadata(worker_name="prefix", model_name="prefix", prompt_version="v2")

            def translate(self, task):
                from book_agent.workers.translator import EchoTranslationWorker

                result = EchoTranslationWorker().translate(task)
                for segment in result.output.target_segments:
                    segment.text_zh = "NEW::" + segment.text_zh
                return result

        with self.session_factory() as session:
            session.get(TranslationPacket, packet_id).status = PacketStatus.BUILT
            TranslationService(TranslationRepository(session), worker=PrefixWorker()).execute_packet(packet_id)
            seed = self._start(session)
            self._report(session, self._ctx(session, seed.turn_id), packet_id, confidence=0.5)
            self.assertTrue(self._model_issue(session).evidence_json["actual_target_text"].startswith("NEW::ZH::"))

    def test_low_confidence_or_minor_findings_do_not_block(self) -> None:
        with self.session_factory() as session:
            seed = self._start(session)
            ctx = self._ctx(session, seed.turn_id)
            self._report(session, ctx, self.packet_ids[1], confidence=0.6)
            issue = self._model_issue(session)
            self.assertFalse(issue.blocking)
            self._report(session, ctx, self.packet_ids[2], severity="medium", confidence=0.99, issue_type="STYLE_DRIFT")
            style = session.scalars(select(ReviewIssue).where(ReviewIssue.issue_type == "STYLE_DRIFT")).one()
            self.assertFalse(style.blocking)
            self.assertEqual(style.evidence_json["preferred_hint"], "解决这个问题的办法是上下文工程。")

    def test_rule_issue_on_the_same_sentence_covers_the_finding(self) -> None:
        with self.session_factory() as session:
            seed = self._start(session)
            ctx = self._ctx(session, seed.turn_id)
            batch = next_review_batch(ctx, NextReviewBatchArgs())
            packet = session.get(TranslationPacket, batch["packet_id"])
            sentence_id = ReviewerAgentTests._first_sentence_id(session, packet.id)
            rule_issue = ReviewIssue(
                id=stable_id("rule-omission", sentence_id),
                document_id=self.document_id,
                chapter_id=packet.chapter_id,
                sentence_id=sentence_id,
                packet_id=packet.id,
                issue_type="OMISSION",
                root_cause_layer=RootCauseLayer.ALIGNMENT,
                severity=Severity.HIGH,
                blocking=True,
                detector=Detector.RULE,
                confidence=1.0,
                evidence_json={},
                status=IssueStatus.OPEN,
            )
            session.add(rule_issue)
            session.flush()
            result = self._report(session, ctx, packet.id, issue_type="OMISSION")
            self.assertFalse(result["recorded"])
            self.assertIn("already covered", result["reason"])
            self.assertIn("OMISSION", next_review_batch(ctx, NextReviewBatchArgs())["pairs"][0]["known_issues"])

    def test_unknown_alias_and_incomplete_term_conflict_are_tool_errors(self) -> None:
        with self.session_factory() as session:
            seed = self._start(session)
            ctx = self._ctx(session, seed.turn_id)
            with self.assertRaises(Exception) as alias_error:
                self._report(session, ctx, self.packet_ids[1], sentence_alias="S9")
            self.assertIn("unknown sentence alias", str(alias_error.exception))
            with self.assertRaises(Exception) as zero_error:
                self._report(session, ctx, self.packet_ids[1], sentence_alias="S0")
            self.assertIn("unknown sentence alias", str(zero_error.exception))
            with self.assertRaises(Exception) as term_error:
                self._report(session, ctx, self.packet_ids[1], issue_type="TERM_CONFLICT")
            self.assertIn("source_term", str(term_error.exception))

    # --- lifecycle --------------------------------------------------------------------

    def test_retranslation_closes_the_finding_and_repeat_reports_reopen_then_escalate(self) -> None:
        packet_id = self.packet_ids[1]
        with self.session_factory() as session:
            seed = self._start(session)
            ctx = self._ctx(session, seed.turn_id)
            self._report(session, ctx, packet_id, confidence=0.7)
            session.commit()

        for expected_reopens, expected_blocking in ((1, False), (2, True)):
            with self.session_factory() as session:
                session.get(TranslationPacket, packet_id).status = PacketStatus.BUILT
                TranslationService(TranslationRepository(session)).execute_packet(packet_id)
                issue = self._model_issue(session)
                self.assertEqual(issue.status, IssueStatus.RESOLVED)
                self.assertIn("awaiting the next model review", issue.resolution_note)
                result = self._report(session, self._ctx(session, seed.turn_id), packet_id, confidence=0.7)
                session.commit()
                issue = self._model_issue(session)
                self.assertTrue(result["reopened"])
                self.assertEqual(issue.status, IssueStatus.OPEN)
                self.assertEqual(issue.reopen_count, expected_reopens)
                self.assertEqual(issue.blocking, expected_blocking)
        with self.session_factory() as session:
            self.assertEqual(self._model_issue(session).evidence_json["escalation"], "reopened_after_repair")

    def test_human_wontfix_is_not_closed_by_retranslation_or_reopened_by_the_reviewer(self) -> None:
        packet_id = self.packet_ids[1]
        with self.session_factory() as session:
            seed = self._start(session)
            ctx = self._ctx(session, seed.turn_id)
            self._report(session, ctx, packet_id)
            IssueService(session).transition(
                self._model_issue(session).id, to_status=IssueStatus.WONTFIX, actor_id="human:editor", note="fine as is"
            )
            session.get(TranslationPacket, packet_id).status = PacketStatus.BUILT
            TranslationService(TranslationRepository(session)).execute_packet(packet_id)
            result = self._report(session, ctx, packet_id)
            issue = self._model_issue(session)
            self.assertEqual(issue.status, IssueStatus.WONTFIX)
            self.assertFalse(result["recorded"])
            self.assertIn("human closed", result["note"])

    def test_model_findings_become_rerun_hints(self) -> None:
        with self.session_factory() as session:
            seed = self._start(session)
            self._report(session, self._ctx(session, seed.turn_id), self.packet_ids[1])
            hints = style_hints_for_issue(self._model_issue(session))
        self.assertEqual(len(hints), 2)
        self.assertIn("MISTRANSLATION_SEMANTIC", hints[0])
        self.assertIn("把主语译反了", hints[0])
        self.assertIn("解决这个问题的办法是上下文工程", hints[1])

    def test_rule_review_does_not_resolve_model_findings(self) -> None:
        from book_agent.services.review import ReviewService

        with self.session_factory() as session:
            seed = self._start(session)
            self._report(session, self._ctx(session, seed.turn_id), self.packet_ids[1])
            chapter_id = session.get(TranslationPacket, self.packet_ids[1]).chapter_id
            ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            self.assertEqual(self._model_issue(session).status, IssueStatus.OPEN)

    # --- plan -------------------------------------------------------------------------

    def test_plan_places_model_review_between_translate_and_review(self) -> None:
        plan = plan_for_run(DocumentRunType.TRANSLATE_FULL, {})
        self.assertEqual(plan.stages[plan.stages.index("translate") + 1], "model_review")
        self.assertEqual(plan.stages[plan.stages.index("model_review") + 1], "review")
        self.assertEqual(plan.model_review_mode, "sampled")
        self.assertIn("model_review", plan.agent_stages)
        skipped = plan_for_run(DocumentRunType.TRANSLATE_FULL, {"run_request": {"model_review": "skip"}})
        self.assertNotIn("model_review", skipped.stages)
        full = plan_for_run(DocumentRunType.TRANSLATE_FULL, {"run_request": {"model_review": "FULL"}})
        self.assertEqual(full.model_review_mode, "full")
        self.assertEqual(STAGE_DEPENDENCIES["model_review"], ("translate",))
        self.assertIn("model_review", STAGE_DEPENDENCIES["review"])
        self.assertNotIn("model_review", plan_for_run(DocumentRunType.REVIEW_FULL, {}).stages)

    @staticmethod
    def _first_sentence_id(session, packet_id: str) -> str:
        from book_agent.harness.agents.reviewer import _current_sentences

        return _current_sentences(session, packet_id)[0].id


if __name__ == "__main__":
    unittest.main()
