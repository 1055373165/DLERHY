"""Re-parsing the untranslated chapters with the current parser (services/chapter_reparse.py)."""

from __future__ import annotations

import unittest

from sqlalchemy import func, select

from book_agent.domain.enums import ArtifactStatus, BlockType, PacketStatus
from book_agent.domain.models import Block, Chapter, Document, Sentence
from book_agent.domain.models.translation import TranslationPacket, TranslationRun
from book_agent.services.chapter_reparse import (
    PARSER_FINGERPRINT_KEY,
    ChapterBoundaryMismatch,
    ChapterReparseService,
    SourceMissing,
    parser_fingerprint,
)
from tests.pilot_repair_fixture import PilotBench


class ChapterReparseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bench = PilotBench()
        self.addCleanup(self.bench.close)
        self.pilot = self.bench.translate_pilot()

    def _third_chapter(self, session) -> Chapter:
        return self.bench.chapters(session)[2]

    def _damage(self, session, chapter_id: str) -> str:
        """An old parser's mistake: a body paragraph kept as code."""
        block = session.scalars(
            select(Block).where(Block.chapter_id == chapter_id, Block.block_type == BlockType.PARAGRAPH).order_by(Block.ordinal)
        ).first()
        block.block_type = BlockType.CODE
        session.flush()
        return block.id

    def _active(self, session, chapter_id: str) -> list[Block]:
        return list(
            session.scalars(select(Block).where(Block.chapter_id == chapter_id, Block.status == ArtifactStatus.ACTIVE).order_by(Block.ordinal)).all()
        )

    def test_import_records_the_parser_fingerprint(self) -> None:
        with self.bench.session_factory() as session:
            document = self.bench.document(session)
            self.assertEqual((document.metadata_json or {}).get(PARSER_FINGERPRINT_KEY), parser_fingerprint())
            self.assertFalse(ChapterReparseService(session).needs_reparse(self.bench.document_id))
            document.metadata_json = {**document.metadata_json, PARSER_FINGERPRINT_KEY: "older"}
            self.assertTrue(ChapterReparseService(session).needs_reparse(self.bench.document_id))

    def test_untranslated_chapters_are_rebuilt_and_translated_ones_kept(self) -> None:
        with self.bench.session_factory() as session:
            chapter = self._third_chapter(session)
            damaged = self._damage(session, chapter.id)
            pilot_packets = sorted(session.scalars(select(TranslationPacket.id).where(TranslationPacket.chapter_id.in_(self.pilot))).all())
            pilot_blocks = sorted(b.id for chapter_id in self.pilot for b in self._active(session, chapter_id))
            old_packets = set(session.scalars(select(TranslationPacket.id).where(TranslationPacket.chapter_id == chapter.id)).all())
            session.commit()

            dry = ChapterReparseService(session).reparse_untranslated(self.bench.document_id, dry_run=True)
            outcomes = {entry.chapter_id: entry.outcome for entry in dry.chapters}
            self.assertEqual(outcomes[chapter.id], "refreshed")
            self.assertEqual({outcomes[chapter_id] for chapter_id in self.pilot}, {"translated"})
            self.assertEqual(session.get(Block, damaged).block_type, BlockType.CODE)

            result = ChapterReparseService(session).reparse_untranslated(self.bench.document_id)
            session.commit()
            self.assertEqual(result.refreshed_chapter_ids, [chapter.id])
            entry = next(item for item in result.chapters if item.chapter_id == chapter.id)
            self.assertEqual(entry.before.get("code"), 1)
            self.assertNotIn("code", entry.after)

            # The chapter keeps its id and place; its blocks, sentences and packets are new.
            self.assertEqual(self._third_chapter(session).id, chapter.id)
            self.assertEqual(session.get(Block, damaged).status, ArtifactStatus.INVALIDATED)
            new_blocks = self._active(session, chapter.id)
            self.assertTrue(new_blocks)
            self.assertFalse(any(block.block_type == BlockType.CODE for block in new_blocks))
            new_packets = list(session.scalars(select(TranslationPacket).where(TranslationPacket.chapter_id == chapter.id)).all())
            self.assertTrue(new_packets)
            self.assertFalse(old_packets & {packet.id for packet in new_packets})
            self.assertEqual({packet.status for packet in new_packets}, {PacketStatus.BUILT})
            retired = session.scalar(
                select(func.count(Sentence.id)).where(Sentence.chapter_id == chapter.id, Sentence.retired_by_revision_id == result.parse_revision_id)
            )
            self.assertGreater(retired, 0)

            # The pilot's chapters are exactly as they were.
            self.assertEqual(sorted(session.scalars(select(TranslationPacket.id).where(TranslationPacket.chapter_id.in_(self.pilot))).all()), pilot_packets)
            self.assertEqual(sorted(b.id for chapter_id in self.pilot for b in self._active(session, chapter_id)), pilot_blocks)

            # A second run finds nothing left to do.
            again = ChapterReparseService(session).reparse_untranslated(self.bench.document_id)
            self.assertEqual(again.refreshed_chapter_ids, [])
            self.assertIsNone(again.parse_revision_id)

    def test_the_layout_evidence_of_refreshed_chapters_is_read_again(self) -> None:
        """Art of AI: the import flagged p304 and the appendix as suspect (tables read as stray prose);
        after the re-parse the review still judged reading order by that evidence."""
        with self.bench.session_factory() as session:
            chapter = self._third_chapter(session)
            self._damage(session, chapter.id)
            pilot_chapter = session.get(Chapter, self.pilot[0])
            pilot_page = (pilot_chapter.metadata_json or {}).get("source_page_start")
            chapter.metadata_json = {**(chapter.metadata_json or {}), "pdf_layout_risk": "high", "suspicious_page_numbers": [7]}
            pilot_chapter.metadata_json = {**(pilot_chapter.metadata_json or {}), "suspicious_page_numbers": [pilot_page]}
            document = self.bench.document(session)
            evidence = dict((document.metadata_json or {}).get("pdf_page_evidence") or {})
            pages = [dict(page) for page in evidence.get("pdf_pages") or []]
            for page in pages:
                if page.get("page_number") in {7, pilot_page}:
                    page["layout_suspect"] = True
            document.metadata_json = {**document.metadata_json, "pdf_page_evidence": {**evidence, "pdf_pages": pages}}
            session.commit()

            ChapterReparseService(session).reparse_untranslated(self.bench.document_id)
            session.commit()
            session.expire_all()
            chapter = self._third_chapter(session)
            self.assertEqual(chapter.metadata_json.get("suspicious_page_numbers"), [])
            self.assertNotEqual(chapter.metadata_json.get("pdf_layout_risk"), "high")
            by_page = {
                page["page_number"]: page
                for page in self.bench.document(session).metadata_json["pdf_page_evidence"]["pdf_pages"]
            }
            self.assertFalse(by_page[7].get("layout_suspect"))
            # The pilot's translated chapter keeps the evidence of the blocks it still has.
            self.assertTrue(by_page[pilot_page].get("layout_suspect"))
            self.assertEqual(session.get(Chapter, self.pilot[0]).metadata_json.get("suspicious_page_numbers"), [pilot_page])

    def test_revert_puts_the_old_structure_back(self) -> None:
        with self.bench.session_factory() as session:
            chapter = self._third_chapter(session)
            damaged = self._damage(session, chapter.id)
            before = [(b.id, b.ordinal) for b in self._active(session, chapter.id)]
            session.commit()
            result = ChapterReparseService(session).reparse_untranslated(self.bench.document_id)
            session.commit()
            ChapterReparseService(session).revert(self.bench.document_id, result.parse_revision_id)
            session.commit()
            self.assertEqual([(b.id, b.ordinal) for b in self._active(session, chapter.id)], before)
            self.assertEqual(session.get(Block, damaged).block_type, BlockType.CODE)
            packets = session.scalar(select(func.count(TranslationPacket.id)).where(TranslationPacket.chapter_id == chapter.id))
            self.assertGreater(packets, 0)
            live = session.scalar(
                select(func.count(Sentence.id)).where(Sentence.chapter_id == chapter.id, Sentence.retired_by_revision_id.is_(None))
            )
            self.assertGreater(live, 0)

    def test_revert_refused_once_translated(self) -> None:
        with self.bench.session_factory() as session:
            chapter = self._third_chapter(session)
            self._damage(session, chapter.id)
            session.commit()
            result = ChapterReparseService(session).reparse_untranslated(self.bench.document_id)
            session.commit()
            packet_id = session.scalars(select(TranslationPacket.id).where(TranslationPacket.chapter_id == chapter.id)).first()
            self.bench.workflow(session).translation_service.execute_packet(packet_id)
            session.commit()
            self.assertGreater(session.scalar(select(func.count(TranslationRun.id)).where(TranslationRun.packet_id == packet_id)), 0)
            with self.assertRaises(Exception):
                ChapterReparseService(session).revert(self.bench.document_id, result.parse_revision_id)

    def test_missing_source_file_is_refused(self) -> None:
        with self.bench.session_factory() as session:
            document = session.get(Document, self.bench.document_id)
            document.source_path = str(self.bench.root / "gone.pdf")
            session.commit()
            with self.assertRaises(SourceMissing) as caught:
                ChapterReparseService(session).reparse_untranslated(self.bench.document_id)
            self.assertIn("找不到", str(caught.exception))

    def _whole_book_run(self, session, request: dict | None = None) -> str:
        from book_agent.domain.enums import DocumentRunType
        from book_agent.infra.repositories.run_control import RunControlRepository
        from book_agent.services.run_control import RunControlService

        summary = RunControlService(RunControlRepository(session)).create_run(
            document_id=self.bench.document_id,
            run_type=DocumentRunType.TRANSLATE_FULL,
            requested_by="test",
            status_detail_json={"run_request": dict(request or {})},
        )
        session.commit()
        return summary.run_id

    def _finish(self, session, run_id: str) -> None:
        from book_agent.domain.enums import DocumentRunStatus
        from book_agent.domain.models.ops import DocumentRun

        # One open run per book: the next test run needs this one closed.
        session.get(DocumentRun, run_id).status = DocumentRunStatus.CANCELLED
        session.commit()

    def _executor(self):
        from book_agent.app.runtime.document_run_executor import DocumentRunExecutor

        return DocumentRunExecutor(session_factory=self.bench.session_factory, export_root=self.bench.export_root, translation_worker=self.bench.worker)

    def test_a_whole_book_run_reparses_first_when_the_parser_changed(self) -> None:
        from book_agent.domain.models.ops import DocumentRun
        from book_agent.orchestrator.run_plan import plan_for_run

        with self.bench.session_factory() as session:
            chapter = self._third_chapter(session)
            self._damage(session, chapter.id)
            document = self.bench.document(session)
            document.metadata_json = {**document.metadata_json, PARSER_FINGERPRINT_KEY: "older-parser"}
            session.commit()
            run_id = self._whole_book_run(session)
            run = session.get(DocumentRun, run_id)
            plan = plan_for_run(run.run_type, run.status_detail_json)
            self.assertEqual(plan.stages[-2:], ("book_qa", "book_repair"))
        executor = self._executor()
        executor._maybe_reparse_untranslated(run_id, plan)
        executor._maybe_reparse_untranslated(run_id, plan)  # once only
        with self.bench.session_factory() as session:
            outcome = session.get(DocumentRun, run_id).status_detail_json["chapter_reparse"]
            self.assertEqual(outcome["refreshed_chapter_ids"], [chapter.id])
            self.assertEqual(self.bench.document(session).metadata_json[PARSER_FINGERPRINT_KEY], parser_fingerprint())
            self.assertFalse(any(b.block_type == BlockType.CODE for b in self._active(session, chapter.id)))

    def test_a_whole_book_run_skips_the_reparse_when_unchanged_off_or_the_file_is_gone(self) -> None:
        from book_agent.domain.models.ops import DocumentRun
        from book_agent.orchestrator.run_plan import plan_for_run

        executor = self._executor()

        def check(request: dict | None = None) -> dict:
            with self.bench.session_factory() as session:
                run_id = self._whole_book_run(session, request)
                run = session.get(DocumentRun, run_id)
                plan = plan_for_run(run.run_type, run.status_detail_json)
            executor._maybe_reparse_untranslated(run_id, plan)
            with self.bench.session_factory() as session:
                outcome = session.get(DocumentRun, run_id).status_detail_json["chapter_reparse"]
                self._finish(session, run_id)
            return outcome

        self.assertEqual(check(), {"skipped": "parser_unchanged"})
        with self.bench.session_factory() as session:
            document = self.bench.document(session)
            document.metadata_json = {**document.metadata_json, PARSER_FINGERPRINT_KEY: "older-parser"}
            session.commit()
        self.assertEqual(check({"reparse_untranslated": "off"}), {"skipped": "off"})
        with self.bench.session_factory() as session:
            self.bench.document(session).source_path = str(self.bench.root / "gone.pdf")
            session.commit()
        self.assertIn("找不到", check()["refused"])

    def test_changed_chapter_boundaries_are_refused(self) -> None:
        class OneChapterFewer:
            def __init__(self, inner) -> None:
                self.inner = inner

            def parse(self, document, path):
                artifacts = self.inner.parse(document, path)
                artifacts.chapters = artifacts.chapters[:-1]
                return artifacts

        from book_agent.services.bootstrap import ParseService

        with self.bench.session_factory() as session:
            chapter = self._third_chapter(session)
            self._damage(session, chapter.id)
            session.commit()
            service = ChapterReparseService(session, parse_service=OneChapterFewer(ParseService()))
            with self.assertRaises(ChapterBoundaryMismatch):
                service.reparse_untranslated(self.bench.document_id)


if __name__ == "__main__":
    unittest.main()
