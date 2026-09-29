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
