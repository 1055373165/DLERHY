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


if __name__ == "__main__":
    unittest.main()
