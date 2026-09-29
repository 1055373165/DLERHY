"""Repair loop fixes for the whole-book gate: translation memory, settled terms, retries, new paths.

Runs on the pilot repair bench (tests/pilot_repair_fixture.py) with its fake Chinese model.
"""

from __future__ import annotations

import unittest

from sqlalchemy import select

from book_agent.domain.enums import BlockType, PacketStatus
from book_agent.domain.models import Block
from book_agent.domain.models.translation import TranslationPacket
from book_agent.domain.quality.pilot_repair import REPAIR_PATHS, WorkItem
from book_agent.infra.repositories.review import active_target_texts
from book_agent.services.pilot_repair_tools import PilotRepairToolbox, RepairBudget, RepairContext
from tests.pilot_repair_fixture import block_with_text, packet_of, sentence_ids
from tests.test_pilot_repair import PilotRepairBenchCase


def _path(check_id: str, name: str):
    return next(path for path in REPAIR_PATHS[check_id] if path.name == name)


class ToolboxCase(PilotRepairBenchCase):
    def toolbox(self, session, chapter_ids: list[str]) -> PilotRepairToolbox:
        ctx = RepairContext(
            session=session,
            document_id=self.bench.document_id,
            run=None,
            chapter_ids=list(chapter_ids),
            workflow_factory=self.bench.workflow,
            budget=RepairBudget(budget_usd=None, max_packets=100),
        )
        return PilotRepairToolbox(ctx)

    def restyle(self, contains: str, *, text: str, block_type: str) -> str:
        from book_agent.services.parse_revision_fork import ParseRevisionForkService

        with self.bench.session_factory() as session:
            block = block_with_text(session, self.bench.document_id, contains)
            block.source_text = text
            block.normalized_text = text
            block.block_type = BlockType(block_type)
            session.flush()
            fork = ParseRevisionForkService(session).resegment_blocks(self.bench.document_id, block_ids=[block.id], reason="inject")
            workflow = self.bench.workflow(session)
            for packet_id in fork.retranslate_packet_ids:
                workflow.translation_service.execute_packet(packet_id)
            session.commit()
            return block.id


class TranslationMemoryTests(ToolboxCase):
    def test_a_restored_heading_the_book_already_translates_is_filled_without_retranslation(self) -> None:
        chapter_ids = self.translate()
        # Chapter 1 has a translated "Summary" heading; chapter 2's "Summary" was hidden as code.
        self.restyle("This chapter explains why", text="Summary", block_type="heading")
        hidden = self.restyle("The second technique masks", text="Summary", block_type="code")
        with self.bench.session_factory() as session:
            first = next(sid for sid in sentence_ids(session, self.bench.document_id, "Summary") if session.get(Block, hidden).id not in sid)
            known = active_target_texts(session, sentence_ids(session, self.bench.document_id, "Summary"))
            summary_zh = next(text for text in known.values() if text)
            neighbours = sentence_ids(session, self.bench.document_id, "The first technique reads")
            before = active_target_texts(session, neighbours)
        self.worker.calls.clear()

        with self.bench.session_factory() as session:
            toolbox = self.toolbox(session, chapter_ids)
            finding = {
                "key": f"L1.heading_as_text:block:{hidden}",
                "check_id": "L1.heading_as_text",
                "severity": "major",
                "location": {"block_id": hidden},
                "target": {"kind": "block", "id": hidden},
                "evidence": {"heading_level": 2},
            }
            result = toolbox.apply(WorkItem("L1.heading_as_text", _path("L1.heading_as_text", "structure_edit.relabel_as_heading"), [finding]))
            session.commit()

            self.assertEqual(self.worker.calls, [], "the packet was retranslated instead of filled from memory")
            self.assertEqual(result.packet_ids, [])
            self.assertTrue(result.params["translation_memory"])
            heading_sentences = [
                sid for sid in sentence_ids(session, self.bench.document_id, "Summary") if sid != first
            ]
            targets = active_target_texts(session, heading_sentences)
            self.assertIn(summary_zh, targets.values())
            # The rest of the packet is exactly as it was.
            self.assertEqual(active_target_texts(session, neighbours), before)
            packet = session.get(TranslationPacket, packet_of(session, heading_sentences[-1]))
            self.assertEqual(packet.status, PacketStatus.TRANSLATED)

    def test_retranslation_hints_carry_the_books_settled_term_renderings(self) -> None:
        chapter_ids = self.translate()
        from book_agent.services.glossary_service import GlossaryService

        with self.bench.session_factory() as session:
            GlossaryService(session).lock_term(
                self.bench.document_id, "LLM", "大语言模型", target_variants=["大型语言模型"], lock_level=__import__("book_agent.domain.enums", fromlist=["LockLevel"]).LockLevel.PREFERRED
            )
            session.commit()
            sentence = sentence_ids(session, self.bench.document_id, "An LLM answers from")[0]
            packet = packet_of(session, sentence)
        self.worker.calls.clear()
        with self.bench.session_factory() as session:
            toolbox = self.toolbox(session, chapter_ids)
            toolbox.retranslate([packet], ["上一版译文的问题：漏译。"])
            session.commit()
        hints = self.worker.calls[-1]["hints"]
        self.assertTrue(any("「LLM」译为「大语言模型」" in hint for hint in hints), hints)


class StaleDraftTests(ToolboxCase):
    """After a merge, one sentence had the current translation of several packets (the p.53 callout)."""

    def test_a_fork_over_packets_on_one_block_keeps_one_and_invalidates_the_rest(self) -> None:
        from book_agent.domain.enums import PacketSentenceRole
        from book_agent.domain.models import Sentence
        from book_agent.domain.models.translation import PacketSentenceMap
        from book_agent.services.parse_revision_fork import ParseRevisionForkService

        self.translate()
        with self.bench.session_factory() as session:
            block = block_with_text(session, self.bench.document_id, "An LLM answers from")
            owner = session.get(TranslationPacket, packet_of(session, sentence_ids(session, self.bench.document_id, "An LLM answers from")[0]))
            # A merge moved a second packet's bounds onto the same blocks (the p.53 callout had six).
            twin = TranslationPacket(
                id="00000000-0000-4000-8000-00000000d0b1",
                chapter_id=owner.chapter_id,
                block_start_id=owner.block_start_id,
                block_end_id=owner.block_end_id,
                packet_type=owner.packet_type,
                book_profile_version=owner.book_profile_version,
                chapter_brief_version=owner.chapter_brief_version,
                termbase_version=owner.termbase_version,
                entity_snapshot_version=owner.entity_snapshot_version,
                packet_json=dict(owner.packet_json or {}),
                status=PacketStatus.TRANSLATED,
            )
            session.add(twin)
            session.flush()
            for mapping in session.scalars(select(PacketSentenceMap).where(PacketSentenceMap.packet_id == owner.id)).all():
                session.add(PacketSentenceMap(packet_id=twin.id, sentence_id=mapping.sentence_id, role=mapping.role))
            session.flush()
            block.source_text = block.source_text + " It is a new sentence here."
            block.normalized_text = block.source_text
            fork = ParseRevisionForkService(session).resegment_blocks(self.bench.document_id, block_ids=[block.id], reason="test")
            session.commit()
            self.assertIn(twin.id, fork.invalidated_packet_ids)
            self.assertEqual(session.get(TranslationPacket, twin.id).status, PacketStatus.INVALIDATED)
            current = list(session.scalars(select(Sentence.id).where(Sentence.block_id == block.id, Sentence.retired_by_revision_id.is_(None))).all())
            owners = session.execute(
                select(PacketSentenceMap.sentence_id, PacketSentenceMap.packet_id).where(
                    PacketSentenceMap.sentence_id.in_(current), PacketSentenceMap.role == PacketSentenceRole.CURRENT
                )
            ).all()
            self.assertEqual({packet_id for _sentence, packet_id in owners}, {owner.id})
            # Nothing is left to translate in the invalidated packet: progress ignores it.
            from book_agent.orchestrator.stage_status import StageStatusCalculator

            evidence = StageStatusCalculator(session)._translate_evidence("no-run", self.bench.document_id)
            self.assertNotEqual(evidence.status.value, "failed")

    def test_the_review_query_prefers_the_packet_that_owns_the_sentence(self) -> None:
        """Another packet's newer-numbered attempt left active does not replace the owner's text."""
        from book_agent.core.ids import stable_id
        from book_agent.domain.enums import RunStatus, TargetSegmentStatus
        from book_agent.domain.models.translation import AlignmentEdge, TargetSegment, TranslationRun

        chapter_ids = self.translate()
        with self.bench.session_factory() as session:
            sentence = sentence_ids(session, self.bench.document_id, "An LLM answers from")[0]
            owner = packet_of(session, sentence)
            before = active_target_texts(session, [sentence])[sentence]
            other = session.scalars(
                select(TranslationPacket.id).where(TranslationPacket.chapter_id == chapter_ids[1], TranslationPacket.id != owner)
            ).first()
            run = TranslationRun(
                id=stable_id("translation-run", other, 99),
                packet_id=other,
                model_name="stale",
                model_config_json={},
                prompt_version="x",
                attempt=99,
                status=RunStatus.SUCCEEDED,
                output_json={},
            )
            session.add(run)
            session.flush()
            segment = TargetSegment(
                id=stable_id("target-segment", run.id, 1),
                chapter_id=chapter_ids[1],
                translation_run_id=run.id,
                ordinal=1,
                text_zh="一段过时的整段译文",
                segment_type="sentence",
                confidence=0.5,
                final_status=TargetSegmentStatus.DRAFT,
            )
            session.add(segment)
            session.flush()
            session.add(AlignmentEdge(id=stable_id("alignment-edge", sentence, segment.id), sentence_id=sentence, target_segment_id=segment.id, relation_type="1:1", confidence=0.5, created_by="system"))
            session.flush()
            self.assertEqual(active_target_texts(session, [sentence])[sentence], before)


if __name__ == "__main__":
    unittest.main()
