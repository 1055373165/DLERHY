"""Sentence-level parse-revision fork: retire, re-segment, carry translations, rebuild packets, settle issues."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.pool import StaticPool

from book_agent.core.ids import stable_id
from book_agent.domain.enums import (
    ArtifactStatus,
    Detector,
    IssueStatus,
    PacketSentenceRole,
    PacketStatus,
    ParseRevisionStatus,
    RootCauseLayer,
    Severity,
)
from book_agent.domain.event_kinds import DOCUMENT_REPARSED
from book_agent.domain.models import Block, DocumentParseRevision, Event, Sentence, SentenceLineage
from book_agent.domain.models.review import ReviewIssue
from book_agent.domain.models.translation import PacketSentenceMap, TranslationPacket, TranslationRun
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.infra.repositories.review import ReviewRepository, active_target_texts
from book_agent.infra.repositories.translation import TranslationRepository
from book_agent.orchestrator.bootstrap import BootstrapOrchestrator
from book_agent.services.issues import IssueService
from book_agent.services.parse_revision_fork import STALE_FLAG, ParseRevisionForkService
from book_agent.services.review import ReviewService
from book_agent.services.translation import TranslationService
from tests.test_translation_worker_abstraction import CHAPTER_XHTML, CONTAINER_XML, CONTENT_OPF, NAV_XHTML

ORIGINAL = "Context engineering is a discipline."


class ParseRevisionForkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.engine = build_engine(
            f"sqlite+pysqlite:///{Path(self.tempdir.name) / 'fork.db'}",
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
        self.chapter_id = artifacts.chapters[0].id
        with self.session_factory() as session:
            service = TranslationService(TranslationRepository(session))
            for packet in artifacts.translation_packets:
                service.execute_packet(packet.id)
            session.commit()
            self.sentence_id = session.scalars(select(Sentence.id).where(Sentence.source_text == ORIGINAL)).one()
            self.block_id = session.get(Sentence, self.sentence_id).block_id

    def _change_block(self, session, text: str) -> None:
        block = session.get(Block, self.block_id)
        block.source_text = text
        block.normalized_text = text
        block.source_span_json = {**(block.source_span_json or {}), STALE_FLAG: True}
        session.flush()

    def _packet_of(self, session, sentence_id: str) -> TranslationPacket:
        packet_id = session.scalars(
            select(PacketSentenceMap.packet_id).where(
                PacketSentenceMap.sentence_id == sentence_id, PacketSentenceMap.role == PacketSentenceRole.CURRENT
            )
        ).one()
        return session.get(TranslationPacket, packet_id)

    def test_unchanged_blocks_do_not_fork(self) -> None:
        with self.session_factory() as session:
            block = session.get(Block, self.block_id)
            block.source_span_json = {**(block.source_span_json or {}), STALE_FLAG: True}
            result = ParseRevisionForkService(session).resegment_blocks(self.document_id, reason="test")
            self.assertFalse(result.forked)
            self.assertNotIn(STALE_FLAG, session.get(Block, self.block_id).source_span_json)

    def test_added_sentence_carries_the_old_translation_and_asks_for_retranslation(self) -> None:
        with self.session_factory() as session:
            self._change_block(session, ORIGINAL + " It needs care.")
            result = ParseRevisionForkService(session).resegment_blocks(self.document_id, reason="refresh")
            session.commit()

            self.assertTrue(result.forked)
            self.assertEqual((result.retired_sentence_count, result.created_sentence_count), (1, 2))
            self.assertEqual(result.relation_counts, {"same": 1})
            old = session.get(Sentence, self.sentence_id)
            self.assertEqual(old.retired_by_revision_id, result.parse_revision_id)
            fresh = session.scalars(
                select(Sentence).where(Sentence.block_id == self.block_id, Sentence.retired_by_revision_id.is_(None)).order_by(Sentence.ordinal_in_block)
            ).all()
            self.assertEqual([sentence.source_text for sentence in fresh], [ORIGINAL, "It needs care."])
            lineage = session.scalars(select(SentenceLineage)).one()
            self.assertEqual((lineage.from_sentence_id, lineage.to_sentence_id, lineage.relation), (old.id, fresh[0].id, "same"))
            # The translation followed the sentence onto a new attempt; the added sentence has none.
            texts = active_target_texts(session, [fresh[0].id, fresh[1].id])
            self.assertEqual(texts[fresh[0].id], f"ZH::{ORIGINAL}")
            self.assertNotIn(fresh[1].id, texts)
            packet = self._packet_of(session, fresh[0].id)
            self.assertEqual(packet.status, PacketStatus.BUILT)
            self.assertEqual(result.retranslate_packet_ids, [packet.id])
            carried = session.scalars(select(TranslationRun).where(TranslationRun.packet_id == packet.id).order_by(TranslationRun.attempt.desc())).first()
            self.assertEqual(carried.model_config_json["worker"], "parse_revision_fork")
            revisions = session.scalars(select(DocumentParseRevision).where(DocumentParseRevision.document_id == self.document_id)).all()
            self.assertEqual([r.status for r in revisions if r.id == result.parse_revision_id], [ParseRevisionStatus.ACTIVE])
            self.assertTrue(all(r.status == ParseRevisionStatus.SUPERSEDED for r in revisions if r.id != result.parse_revision_id))
            self.assertEqual(session.scalars(select(Event).where(Event.kind == DOCUMENT_REPARSED)).one().payload["created_sentence_count"], 2)

            # Review sees only active sentences: the added one is an omission, the retired one is gone.
            artifacts = ReviewService(ReviewRepository(session)).review_chapter(self.chapter_id)
            omissions = [issue for issue in artifacts.issues if issue.issue_type == "OMISSION"]
            self.assertEqual([issue.sentence_id for issue in omissions], [fresh[1].id])

            TranslationService(TranslationRepository(session)).execute_packet(packet.id)
            artifacts = ReviewService(ReviewRepository(session)).review_chapter(self.chapter_id)
            self.assertFalse([issue for issue in artifacts.issues if issue.issue_type == "OMISSION"])

    def test_near_identical_text_is_fully_carried(self) -> None:
        with self.session_factory() as session:
            self._change_block(session, "Context engineering is a disciplines.")
            result = ParseRevisionForkService(session).resegment_blocks(self.document_id, reason="typo fix")
            self.assertEqual(result.relation_counts, {"same": 1})
            self.assertEqual(result.retranslate_packet_ids, [])
            # Packets that only used the sentence as context are rebuilt and carried too.
            fresh = session.scalars(
                select(Sentence).where(Sentence.block_id == self.block_id, Sentence.retired_by_revision_id.is_(None))
            ).one()
            owner = session.scalars(
                select(PacketSentenceMap.packet_id).where(
                    PacketSentenceMap.sentence_id == fresh.id, PacketSentenceMap.role == PacketSentenceRole.CURRENT
                )
            ).one()
            self.assertIn(owner, result.carried_packet_ids)
            self.assertTrue(all(session.get(TranslationPacket, pid).status == PacketStatus.TRANSLATED for pid in result.carried_packet_ids))
            self.assertEqual(result.carried_ratio, 1.0)

    def test_issues_on_retired_sentences_are_resolved_but_human_decisions_are_kept(self) -> None:
        with self.session_factory() as session:
            packet = self._packet_of(session, self.sentence_id)

            def issue(kind: str) -> ReviewIssue:
                return ReviewIssue(
                    id=stable_id("fork-issue", kind),
                    document_id=self.document_id,
                    chapter_id=self.chapter_id,
                    sentence_id=self.sentence_id,
                    packet_id=packet.id,
                    issue_type=kind,
                    root_cause_layer=RootCauseLayer.TRANSLATION,
                    severity=Severity.MEDIUM,
                    blocking=False,
                    detector=Detector.MODEL,
                    confidence=0.9,
                    evidence_json={},
                    status=IssueStatus.OPEN,
                )

            system_issue, human_issue = issue("STYLE_DRIFT"), issue("MISTRANSLATION_SEMANTIC")
            session.add_all([system_issue, human_issue])
            session.flush()
            IssueService(session).transition(human_issue.id, to_status=IssueStatus.WONTFIX, actor_id="human:editor", note="ok")
            self._change_block(session, "An entirely different sentence now.")
            result = ParseRevisionForkService(session).resegment_blocks(self.document_id, reason="refresh")

            self.assertEqual(result.relation_counts, {"removed": 1})
            self.assertEqual(result.resolved_issue_ids, [system_issue.id])
            self.assertEqual(result.human_decided_issue_ids, [human_issue.id])
            self.assertEqual(session.get(ReviewIssue, system_issue.id).status, IssueStatus.RESOLVED)
            self.assertIn("parse revision", session.get(ReviewIssue, system_issue.id).resolution_note)
            self.assertEqual(session.get(ReviewIssue, human_issue.id).status, IssueStatus.WONTFIX)

    def test_invalidated_block_retires_its_sentences_and_empty_packets(self) -> None:
        with self.session_factory() as session:
            packet = self._packet_of(session, self.sentence_id)
            session.get(Block, self.block_id).status = ArtifactStatus.INVALIDATED
            session.flush()
            service = ParseRevisionForkService(session)
            self.assertIn(self.block_id, service.stale_block_ids(self.document_id))
            result = service.resegment_blocks(self.document_id, reason="block removed")
            self.assertEqual((result.retired_sentence_count, result.created_sentence_count), (1, 0))
            self.assertEqual(result.relation_counts, {"removed": 1})
            refreshed_packet = session.get(TranslationPacket, packet.id)
            if packet.id in result.invalidated_packet_ids:
                self.assertEqual(refreshed_packet.status, PacketStatus.INVALIDATED)
            else:
                maps = session.scalars(select(PacketSentenceMap.sentence_id).where(PacketSentenceMap.packet_id == packet.id)).all()
                self.assertNotIn(self.sentence_id, maps)

    def test_code_relabelled_as_prose_between_packets_joins_the_packet_before_it(self) -> None:
        """Agent book p49: prose parsed as code has no packet; relabelled a paragraph it must be translated."""
        from book_agent.domain.enums import BlockType, ProtectedPolicy
        from book_agent.services.structure_edits import StructureEditService

        prose = "The call_llm function wraps the call. The semaphore limits it to ten requests."
        with self.session_factory() as session:
            last = session.scalars(select(Block).where(Block.chapter_id == self.chapter_id).order_by(Block.ordinal.desc())).first()
            code = Block(
                id=stable_id("block", self.chapter_id, "listing-prose"),
                chapter_id=self.chapter_id,
                ordinal=last.ordinal + 1,
                block_type=BlockType.CODE,
                source_text=prose,
                normalized_text=prose,
                source_span_json={},
                protected_policy=ProtectedPolicy.PROTECT,
                status=ArtifactStatus.ACTIVE,
            )
            session.add(code)
            session.flush()
            outcome = StructureEditService(session).relabel_block(
                self.document_id, code.id, BlockType.PARAGRAPH.value, actor_id="test", reason="prose parsed as code"
            )
            session.commit()

            self.assertTrue(outcome.fork.retranslate_packet_ids)
            self.assertEqual(outcome.fork.unpacketed_block_ids, [])
            fresh = session.scalars(select(Sentence.id).where(Sentence.block_id == code.id, Sentence.retired_by_revision_id.is_(None))).all()
            mapped = session.scalars(select(PacketSentenceMap.packet_id).where(PacketSentenceMap.sentence_id.in_(fresh))).all()
            self.assertEqual(set(mapped), set(outcome.fork.retranslate_packet_ids))

    def test_a_new_block_after_an_invalidated_packet_joins_a_live_one(self) -> None:
        """Art of AI p28: a paragraph the re-parse found after a running head (whose packet was
        invalidated, nothing in it to translate) was put in that packet and never translated."""
        from book_agent.domain.enums import BlockType, ProtectedPolicy

        prose = "Meanwhile, the hype is everywhere. Now what?"
        with self.session_factory() as session:
            last = session.scalars(select(Block).where(Block.chapter_id == self.chapter_id).order_by(Block.ordinal.desc())).first()
            packets = session.scalars(select(TranslationPacket).where(TranslationPacket.chapter_id == self.chapter_id)).all()
            dead = next(packet for packet in packets if packet.block_end_id == last.id)
            dead.status = PacketStatus.INVALIDATED
            block = Block(
                id=stable_id("block", self.chapter_id, "refresh-created"),
                chapter_id=self.chapter_id,
                ordinal=last.ordinal + 1,
                block_type=BlockType.PARAGRAPH,
                source_text=prose,
                normalized_text=prose,
                source_span_json={STALE_FLAG: True},
                protected_policy=ProtectedPolicy.TRANSLATE,
                status=ArtifactStatus.ACTIVE,
            )
            session.add(block)
            session.flush()
            result = ParseRevisionForkService(session).resegment_blocks(self.document_id, reason="new block")

            self.assertEqual(result.unpacketed_block_ids, [])
            self.assertTrue(result.retranslate_packet_ids)
            self.assertNotIn(dead.id, result.retranslate_packet_ids)
            self.assertEqual(session.get(TranslationPacket, dead.id).status, PacketStatus.INVALIDATED)


class StrandedBlockTests(unittest.TestCase):
    """Art of AI chapter 2: a packet whose range ended on a dropped copy was invalidated, and the
    sound paragraph inside it kept only context mappings; it was never translated."""

    def test_a_block_left_without_a_live_packet_joins_one(self) -> None:
        from tests.pilot_repair_fixture import PilotBench, block_with_text

        bench = PilotBench()
        self.addCleanup(bench.close)
        bench.translate_pilot()
        with bench.session_factory() as session:
            stranded = block_with_text(session, bench.document_id, "The first technique reads")
            sentence_ids = session.scalars(select(Sentence.id).where(Sentence.block_id == stranded.id, Sentence.retired_by_revision_id.is_(None))).all()
            for packet_id in set(
                session.scalars(
                    select(PacketSentenceMap.packet_id).where(
                        PacketSentenceMap.sentence_id.in_(sentence_ids), PacketSentenceMap.role == PacketSentenceRole.CURRENT
                    )
                ).all()
            ):
                session.get(TranslationPacket, packet_id).status = PacketStatus.INVALIDATED
            # Re-segmented after its packet went: no translation yet (Art of AI p56).
            from sqlalchemy import delete

            from book_agent.domain.models.translation import AlignmentEdge

            session.execute(delete(AlignmentEdge).where(AlignmentEdge.sentence_id.in_(sentence_ids)))
            session.flush()
            from datetime import datetime, timezone

            rehomed = ParseRevisionForkService(session)._rehome_stranded_blocks(
                bench.document_id, {stranded.chapter_id}, now=datetime.now(timezone.utc)
            )

            live_current = session.scalars(
                select(PacketSentenceMap.packet_id)
                .join(TranslationPacket, TranslationPacket.id == PacketSentenceMap.packet_id)
                .where(
                    PacketSentenceMap.sentence_id.in_(sentence_ids),
                    PacketSentenceMap.role == PacketSentenceRole.CURRENT,
                    TranslationPacket.status != PacketStatus.INVALIDATED,
                )
            ).all()
            self.assertTrue(live_current, "the stranded sentences are in no live packet")
            self.assertTrue(set(live_current) <= {packet.id for packet in rehomed})
            self.assertEqual({packet.status for packet in rehomed}, {PacketStatus.BUILT})

            # A block whose translation stands is left alone, live packet or not.
            other = block_with_text(session, bench.document_id, "The second technique masks")
            other_sentences = session.scalars(select(Sentence.id).where(Sentence.block_id == other.id, Sentence.retired_by_revision_id.is_(None))).all()
            for packet_id in set(
                session.scalars(
                    select(PacketSentenceMap.packet_id).where(
                        PacketSentenceMap.sentence_id.in_(other_sentences), PacketSentenceMap.role == PacketSentenceRole.CURRENT
                    )
                ).all()
            ):
                session.get(TranslationPacket, packet_id).status = PacketStatus.INVALIDATED
            session.flush()
            again = ParseRevisionForkService(session)._rehome_stranded_blocks(
                bench.document_id, {stranded.chapter_id}, now=datetime.now(timezone.utc)
            )
            self.assertEqual(again, [])

if __name__ == "__main__":
    unittest.main()
