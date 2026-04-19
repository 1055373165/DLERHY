# ruff: noqa: E402
"""Phase 3 tests: review must surface skipped chapters, not swallow them.

Before this refactor ``_review_document_impl`` silently ``continue``-d
when a chapter's packets weren't fully TRANSLATED. The UI then
displayed ``review=succeeded / issues=0 / actions=0``, lying about what
got reviewed. These tests pin the explicit-skip accounting so a
regression can't undo that.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
os.environ.setdefault("BOOK_AGENT_TRANSLATION_BACKEND", "echo")
os.environ.setdefault("BOOK_AGENT_TRANSLATION_MODEL", "echo-worker")
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.enums import (
    ChapterStatus,
    DocumentStatus,
    PacketStatus,
    PacketType,
    SourceType,
)
from book_agent.domain.models import Chapter, Document
from book_agent.domain.models.translation import TranslationPacket
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.services.workflows import (
    ChapterReviewSkip,
    DocumentReviewResult,
    DocumentWorkflowService,
)


class ReviewSkipVisibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        self.export_root = tempfile.mkdtemp(prefix="review-skip-")

    def _seed(self, *, ch_packet_states: list[list[PacketStatus]]) -> str:
        with self.session_factory() as session:
            doc = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"skip-{uuid4()}",
                source_path="/tmp/skip.epub",
                title="t", author="a", src_lang="en", tgt_lang="zh",
                status=DocumentStatus.ACTIVE,
                parser_version=1, segmentation_version=1,
            )
            session.add(doc)
            session.flush()
            for ordinal, states in enumerate(ch_packet_states, start=1):
                chapter = Chapter(
                    document_id=doc.id, ordinal=ordinal,
                    status=ChapterStatus.PACKET_BUILT,
                )
                session.add(chapter)
                session.flush()
                for state in states:
                    session.add(TranslationPacket(
                        chapter_id=chapter.id,
                        packet_type=PacketType.TRANSLATE,
                        book_profile_version=1,
                        status=state,
                    ))
            session.commit()
            return doc.id

    def _workflow(self, session) -> DocumentWorkflowService:
        return DocumentWorkflowService(
            session,
            export_root=self.export_root,
            translation_worker=None,
        )

    def test_dataclass_defaults(self) -> None:
        """Regression guard: new fields don't break existing construction."""
        result = DocumentReviewResult(
            document_id="x",
            total_issue_count=0,
            total_action_count=0,
            chapter_results=[],
        )
        self.assertEqual(result.skipped_chapters, [])
        self.assertEqual(result.total_chapter_count, 0)
        self.assertEqual(result.examined_chapter_count, 0)
        self.assertEqual(result.skipped_chapter_count, 0)

    def test_chapters_with_non_translated_packets_are_skipped_explicitly(self) -> None:
        """Two chapters, only one fully translated — the other must surface as skip."""
        doc_id = self._seed(ch_packet_states=[
            [PacketStatus.TRANSLATED],
            [PacketStatus.BUILT, PacketStatus.TRANSLATED],
        ])
        with self.session_factory() as session:
            workflow = self._workflow(session)
            result = workflow.review_document(doc_id)

        self.assertEqual(result.total_chapter_count, 2)
        self.assertEqual(result.skipped_chapter_count, 1)
        self.assertEqual(len(result.skipped_chapters), 1)
        skip = result.skipped_chapters[0]
        self.assertIsInstance(skip, ChapterReviewSkip)
        self.assertEqual(skip.reason, "translate_incomplete")
        self.assertEqual(skip.pending_packet_count, 1)
        self.assertEqual(skip.failed_packet_count, 0)

    def test_failed_packet_tagged_as_translate_failed(self) -> None:
        doc_id = self._seed(ch_packet_states=[
            [PacketStatus.FAILED, PacketStatus.TRANSLATED],
        ])
        with self.session_factory() as session:
            workflow = self._workflow(session)
            result = workflow.review_document(doc_id)
        self.assertEqual(result.skipped_chapter_count, 1)
        self.assertEqual(result.skipped_chapters[0].reason, "translate_failed")
        self.assertEqual(result.skipped_chapters[0].failed_packet_count, 1)


if __name__ == "__main__":
    unittest.main()
