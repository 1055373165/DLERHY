"""Segment edits: a new attempt, old text superseded, guards, audit and model-issue closing."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.pool import StaticPool

from book_agent.domain.enums import ActorType, Detector, IssueStatus, PacketSentenceRole, ProtectedPolicy, TargetSegmentStatus
from book_agent.domain.event_kinds import TRANSLATION_SEGMENT_EDITED
from book_agent.domain.models import Block, Event, Sentence
from book_agent.domain.models.ops import AuditEvent
from book_agent.domain.models.review import ReviewIssue
from book_agent.domain.models.translation import TargetSegment, TranslationRun
from book_agent.harness.agents.reviewer import ReportIssueArgs, ReviewerAgent, _current_sentences, report_issue
from book_agent.harness.tools.registry import ToolContext
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.infra.repositories.review import active_target_texts
from book_agent.infra.repositories.translation import TranslationRepository
from book_agent.orchestrator.bootstrap import BootstrapOrchestrator
from book_agent.services.glossary_service import GlossaryService
from book_agent.services.segment_edit import EditRejected, SegmentEditService
from book_agent.services.translation import TranslationService
from tests.test_translation_worker_abstraction import CHAPTER_XHTML, CONTAINER_XML, CONTENT_OPF, NAV_XHTML

SENTENCE = "Context engineering is a discipline."


class SegmentEditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.engine = build_engine(
            f"sqlite+pysqlite:///{Path(self.tempdir.name) / 'edit.db'}",
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
        with self.session_factory() as session:
            service = TranslationService(TranslationRepository(session))
            for packet in artifacts.translation_packets:
                service.execute_packet(packet.id)
            session.commit()
            self.sentence_id = session.scalars(select(Sentence.id).where(Sentence.source_text == SENTENCE)).one()

    def test_edit_creates_a_new_attempt_and_supersedes_the_old_segments(self) -> None:
        with self.session_factory() as session:
            result = SegmentEditService(session).edit_sentence(
                self.sentence_id, "上下文工程是一门学科。", reason="直译", actor_id="agent.repair"
            )
            session.commit()

            self.assertEqual(result.attempt, 2)
            self.assertEqual(result.previous_text, f"ZH::{SENTENCE}")
            self.assertEqual(result.covered_sentence_ids, [self.sentence_id])
            self.assertEqual(active_target_texts(session, [self.sentence_id])[self.sentence_id], "上下文工程是一门学科。")
            runs = session.scalars(select(TranslationRun).where(TranslationRun.packet_id == result.packet_id)).all()
            self.assertEqual(sorted(run.attempt for run in runs), [1, 2])
            edited = session.get(TranslationRun, result.translation_run_id)
            self.assertEqual(edited.model_config_json["worker"], "segment_edit")
            self.assertEqual(edited.token_in, 0)
            old_segments = session.scalars(
                select(TargetSegment).where(TargetSegment.translation_run_id != result.translation_run_id, TargetSegment.chapter_id == result.chapter_id)
            ).all()
            self.assertTrue(
                all(segment.final_status == TargetSegmentStatus.SUPERSEDED for segment in old_segments if segment.translation_run_id == edited.model_config_json["edited_from_run_id"])
            )
            audit = session.scalars(select(AuditEvent).where(AuditEvent.action == "target_segment.edited")).one()
            self.assertEqual(audit.payload_json["previous_text"], f"ZH::{SENTENCE}")
            self.assertEqual(session.scalars(select(Event).where(Event.kind == TRANSLATION_SEGMENT_EDITED)).one().payload["attempt"], 2)

    def test_guards(self) -> None:
        with self.session_factory() as session:
            service = SegmentEditService(session)
            with self.assertRaisesRegex(EditRejected, "empty"):
                service.edit_sentence(self.sentence_id, "  ", reason="x", actor_id="a")
            with self.assertRaisesRegex(EditRejected, "not a correction"):
                service.edit_sentence(self.sentence_id, "上" * 400, reason="x", actor_id="a")
            GlossaryService(session).lock_term(self.document_id, "context engineering", "上下文工程")
            with self.assertRaisesRegex(EditRejected, "locked glossary terms"):
                service.edit_sentence(self.sentence_id, "语境设计是一门学科，非常重要。", reason="x", actor_id="a")
            sentence = session.get(Sentence, self.sentence_id)
            session.get(Block, sentence.block_id).protected_policy = ProtectedPolicy.PROTECT
            with self.assertRaisesRegex(EditRejected, "protect"):
                service.edit_sentence(self.sentence_id, "上下文工程是一门学科。", reason="x", actor_id="a")

    def test_edit_closes_open_model_findings_on_the_sentence(self) -> None:
        with self.session_factory() as session:
            seed = ReviewerAgent(session).start_turn(document_id=self.document_id, model_name="m", mode="full")
            ctx = ToolContext(session=session, document_id=self.document_id, agent_kind="reviewer", turn_id=seed.turn_id)
            sentence = session.get(Sentence, self.sentence_id)
            from book_agent.domain.models.translation import PacketSentenceMap

            packet_id = session.scalars(
                select(PacketSentenceMap.packet_id).where(
                    PacketSentenceMap.sentence_id == sentence.id, PacketSentenceMap.role == PacketSentenceRole.CURRENT
                )
            ).one()
            report_issue(
                ctx,
                ReportIssueArgs(
                    packet_id=packet_id,
                    sentence_alias=f"S{[item.id for item in _current_sentences(session, packet_id)].index(sentence.id) + 1}",
                    issue_type="STYLE_DRIFT",
                    severity="medium",
                    confidence=0.8,
                    explanation="直译腔。",
                ),
            )
            result = SegmentEditService(session).edit_sentence(
                self.sentence_id, "上下文工程是一门学科。", reason="改写", actor_id="human:editor", actor_type=ActorType.HUMAN
            )
            issue = session.scalars(select(ReviewIssue).where(ReviewIssue.detector == Detector.MODEL)).one()
            self.assertEqual(result.closed_model_issue_ids, [issue.id])
            self.assertEqual(issue.status, IssueStatus.RESOLVED)
            self.assertIn("Translation edited (attempt 2)", issue.resolution_note)


if __name__ == "__main__":
    unittest.main()
