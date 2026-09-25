"""Repairs keep a packet's alignment consistent (the Agent book's ALIGNMENT_FAILURE after its pilot repair).

A structure edit re-segments a block (the parse revision fork) and carries the packet's latest
translation into a new attempt; the repair then retranslates what the fork could not carry. When
the same packet went through fork, retranslation and fork again, the first fork's attempt kept
active segments: its edges to sentences the second fork retired made that attempt "current" for
them at export, and its other segments, on live sentences a newer attempt renders, were orphans:
ALIGNMENT_FAILURE, blocking, reason export_time_misalignment.
"""

from __future__ import annotations

import unittest

from sqlalchemy import select

from book_agent.domain.enums import TargetSegmentStatus
from book_agent.domain.models import Block
from book_agent.domain.models.translation import TargetSegment, TranslationRun
from book_agent.export.alignment import build_export_misalignment_evidence
from book_agent.infra.repositories.export import ExportRepository
from book_agent.services.parse_revision_fork import ParseRevisionForkService
from book_agent.services.pilot_repair_tools import PilotRepairToolbox, RepairBudget, RepairContext
from tests.pilot_repair_fixture import FakeZhWorker, PilotBench, block_with_text


class RepairAlignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.worker = FakeZhWorker()
        self.bench = PilotBench(self.worker)
        self.addCleanup(self.bench.close)
        self.bench.translate_pilot()
        with self.bench.session_factory() as session:
            self.block_id = block_with_text(session, self.bench.document_id, "The first technique reads").id

    def _rewrite(self, text: str) -> list[str]:
        """Change the block's text, fork it, and retranslate what the fork left (as a repair does)."""
        with self.bench.session_factory() as session:
            block = session.get(Block, self.block_id)
            block.source_text = text
            block.normalized_text = text
            session.flush()
            fork = ParseRevisionForkService(session).resegment_blocks(self.bench.document_id, block_ids=[block.id], reason="test")
            toolbox = PilotRepairToolbox(
                RepairContext(
                    session=session,
                    document_id=self.bench.document_id,
                    run=None,
                    chapter_ids=[],
                    workflow_factory=self.bench.workflow,
                    budget=RepairBudget(None, 100),
                )
            )
            toolbox.retranslate(list(fork.retranslate_packet_ids), [])
            session.commit()
            return list(fork.rebuilt_packet_ids)

    def _evidence(self):
        with self.bench.session_factory() as session:
            chapter_id = session.get(Block, self.block_id).chapter_id
            return build_export_misalignment_evidence(ExportRepository(session).load_chapter_bundle(chapter_id))

    def test_fork_retranslate_fork_leaves_no_orphan_segments(self) -> None:
        self._rewrite("Alpha reads the context. Beta predicts a token. Gamma repeats it.")
        self._rewrite("Alpha reads the context. Beta masks a token instead. Gamma repeats it.")
        packets = self._rewrite("Alpha now reads both sides. Beta masks a token instead. Gamma repeats it.")

        evidence = self._evidence()
        self.assertEqual(evidence.orphan_target_segment_ids, [])
        self.assertEqual(evidence.missing_target_sentence_ids, [])
        self.assertFalse(evidence.has_anomalies)
        # Only the packet's current attempt has active segments.
        with self.bench.session_factory() as session:
            for packet_id in packets:
                active_runs = set(
                    session.scalars(
                        select(TargetSegment.translation_run_id)
                        .join(TranslationRun, TranslationRun.id == TargetSegment.translation_run_id)
                        .where(TranslationRun.packet_id == packet_id, TargetSegment.final_status != TargetSegmentStatus.SUPERSEDED)
                    ).all()
                )
                self.assertLessEqual(len(active_runs), 1, packet_id)



class RepairVerificationTests(unittest.TestCase):
    def test_an_attempt_that_leaves_an_orphan_segment_is_reverted(self) -> None:
        from unittest.mock import patch

        from book_agent.core.ids import stable_id
        from book_agent.services.pilot_repair import PilotRepairService
        from tests.pilot_repair_fixture import sentence_ids

        worker = FakeZhWorker()
        bench = PilotBench(worker)
        self.addCleanup(bench.close)
        with bench.session_factory() as session:
            (omitted,) = sentence_ids(session, bench.document_id, "Teams track the score")
        worker.omit_sentences.add(omitted)
        bench.translate_pilot()

        original = PilotRepairToolbox._retranslate_packets__with_hints

        def leaves_an_orphan(toolbox, item):
            result = original(toolbox, item)
            run = toolbox.session.scalars(
                select(TranslationRun).where(TranslationRun.packet_id == result.packet_ids[0]).order_by(TranslationRun.attempt.desc())
            ).first()
            toolbox.session.add(
                TargetSegment(
                    id=stable_id("target-segment", run.id, 99),
                    chapter_id=toolbox.session.get(Block, block_with_text(toolbox.session, bench.document_id, "Teams track").id).chapter_id,
                    translation_run_id=run.id,
                    ordinal=99,
                    text_zh="没有对齐的译文。",
                    segment_type="sentence",
                    final_status=TargetSegmentStatus.DRAFT,
                )
            )
            toolbox.session.flush()
            return result

        with patch.object(PilotRepairToolbox, "_retranslate_packets__with_hints", leaves_an_orphan):
            with bench.session_factory() as session:
                repair = PilotRepairService(session, workflow_factory=bench.workflow).run(bench.document_id)
                from book_agent.services.pilot_repair import repair_payload

                (attempt,) = repair_payload(session, repair)["attempts"]
        self.assertEqual(attempt["outcome"], "reverted")
        self.assertTrue(attempt["reason"].startswith("worse:alignment"), attempt["reason"])


if __name__ == "__main__":
    unittest.main()
