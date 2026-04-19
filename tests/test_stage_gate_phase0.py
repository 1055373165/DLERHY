# ruff: noqa: E402
"""Phase 0 regression tests for DocumentRunExecutor stage gating.

These tests pin the behavior that Stage 3 (review) and Stage 4 (export) must
refuse to advance while Stage 2 (translate) packets remain un-translated, even
if the JSON cache in ``status_detail_json.pipeline.stages.*.status`` happens to
say "succeeded". See docs/specs on state-consistency refactor (Phase 0).
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

from book_agent.app.runtime.document_run_executor import DocumentRunExecutor
from book_agent.domain.enums import (
    ChapterStatus,
    DocumentRunStatus,
    DocumentRunType,
    DocumentStatus,
    ExportType,
    PacketStatus,
    PacketType,
    SourceType,
    WorkItemScopeType,
    WorkItemStage,
    WorkItemStatus,
)
from book_agent.domain.models import Chapter, Document
from book_agent.domain.models.ops import DocumentRun, WorkItem
from book_agent.domain.models.translation import TranslationPacket
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory


def _unique_fingerprint(prefix: str) -> str:
    return f"{prefix}-{uuid4()}"


class StageGatePhase0Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        self.export_root = tempfile.mkdtemp(prefix="stage-gate-phase0-")
        self.executor = DocumentRunExecutor(
            session_factory=self.session_factory,
            export_root=self.export_root,
            translation_worker=None,
            enable_controller_runner=False,
        )

    def _seed_document_with_packets(
        self,
        *,
        translated_count: int,
        pending_count: int,
        failed_count: int = 0,
    ) -> tuple[str, str]:
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=_unique_fingerprint("stage-gate"),
                source_path="/tmp/stage-gate.epub",
                title="Stage Gate Phase 0",
                author="Tester",
                src_lang="en",
                tgt_lang="zh",
                status=DocumentStatus.ACTIVE,
                parser_version=1,
                segmentation_version=1,
            )
            session.add(document)
            session.flush()
            chapter = Chapter(
                document_id=document.id,
                ordinal=1,
                title_src="Ch 1",
                status=ChapterStatus.PACKET_BUILT,
            )
            session.add(chapter)
            session.flush()
            for _ in range(translated_count):
                session.add(TranslationPacket(
                    chapter_id=chapter.id,
                    packet_type=PacketType.TRANSLATE,
                    book_profile_version=1,
                    status=PacketStatus.TRANSLATED,
                ))
            for _ in range(pending_count):
                session.add(TranslationPacket(
                    chapter_id=chapter.id,
                    packet_type=PacketType.TRANSLATE,
                    book_profile_version=1,
                    status=PacketStatus.BUILT,
                ))
            for _ in range(failed_count):
                session.add(TranslationPacket(
                    chapter_id=chapter.id,
                    packet_type=PacketType.TRANSLATE,
                    book_profile_version=1,
                    status=PacketStatus.FAILED,
                ))
            run = DocumentRun(
                document_id=document.id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                status=DocumentRunStatus.RUNNING,
                requested_by="test",
                priority=100,
                status_detail_json={
                    "pipeline": {
                        "stages": {
                            "translate": {"status": "succeeded"},
                            "review": {"status": "succeeded"},
                            "bilingual_html": {"status": "succeeded"},
                        }
                    }
                },
            )
            session.add(run)
            session.commit()
            return document.id, run.id

    def _seed_translate_work_items(
        self,
        run_id: str,
        *,
        succeeded: int,
        pending: int,
    ) -> None:
        with self.session_factory() as session:
            for i in range(succeeded):
                session.add(WorkItem(
                    run_id=run_id,
                    stage=WorkItemStage.TRANSLATE,
                    scope_type=WorkItemScopeType.PACKET,
                    scope_id=str(uuid4()),
                    status=WorkItemStatus.SUCCEEDED,
                    attempt=1,
                    input_version_bundle_json={},
                ))
            for i in range(pending):
                session.add(WorkItem(
                    run_id=run_id,
                    stage=WorkItemStage.TRANSLATE,
                    scope_type=WorkItemScopeType.PACKET,
                    scope_id=str(uuid4()),
                    status=WorkItemStatus.PENDING,
                    attempt=1,
                    input_version_bundle_json={},
                ))
            session.commit()

    def _count_stage_work_items(self, run_id: str, stage: WorkItemStage) -> int:
        with self.session_factory() as session:
            return len(self.executor._list_stage_items(session, run_id, stage))

    # --- tests ----------------------------------------------------------

    def test_review_blocked_when_translation_incomplete(self) -> None:
        """Stage 3 must not advance while TranslationPacket status!=TRANSLATED exists.

        Reproduces the production defect where UI showed review=SUCCEEDED
        while translate was only 82/430. The JSON cache claims 'succeeded'
        but physical packets are still BUILT, so review must be refused.
        """
        _document_id, run_id = self._seed_document_with_packets(
            translated_count=82,
            pending_count=348,
        )
        self._seed_translate_work_items(run_id, succeeded=82, pending=348)

        advanced = self.executor._process_review_stage(run_id)

        self.assertFalse(advanced, "review must not advance while packets still BUILT")
        self.assertEqual(
            self._count_stage_work_items(run_id, WorkItemStage.REVIEW),
            0,
            "no review work item may be seeded before translation completes",
        )

    def test_review_blocked_when_translate_work_items_empty(self) -> None:
        """Empty translate work-item list must never be treated as 'complete'.

        Covers failure mode 2 (空集合==完成). Packets exist but no work items
        have been seeded yet; gate must refuse even though JSON cache says
        'succeeded'.
        """
        _document_id, run_id = self._seed_document_with_packets(
            translated_count=0,
            pending_count=5,
        )
        # intentionally seed zero translate work items

        advanced = self.executor._process_review_stage(run_id)

        self.assertFalse(advanced)
        self.assertEqual(
            self._count_stage_work_items(run_id, WorkItemStage.REVIEW), 0
        )

    def test_review_blocked_when_any_packet_failed(self) -> None:
        """A FAILED packet must block review even if zero BUILT remain."""
        _document_id, run_id = self._seed_document_with_packets(
            translated_count=5,
            pending_count=0,
            failed_count=1,
        )
        self._seed_translate_work_items(run_id, succeeded=6, pending=0)

        advanced = self.executor._process_review_stage(run_id)

        self.assertFalse(advanced)

    def test_export_blocked_when_translation_incomplete(self) -> None:
        """Stage 4 (bilingual export) must not run while translate unfinished."""
        _document_id, run_id = self._seed_document_with_packets(
            translated_count=10,
            pending_count=3,
        )
        self._seed_translate_work_items(run_id, succeeded=10, pending=3)

        advanced = self.executor._process_export_stage(
            run_id, export_type=ExportType.BILINGUAL_HTML
        )

        self.assertFalse(advanced)
        self.assertEqual(
            self._count_stage_work_items(run_id, WorkItemStage.EXPORT), 0
        )

    def test_export_blocked_when_review_work_item_missing(self) -> None:
        """Even with all packets TRANSLATED, export must wait for a SUCCEEDED
        review work item to actually exist — a bare JSON cache flag is not
        enough."""
        _document_id, run_id = self._seed_document_with_packets(
            translated_count=5,
            pending_count=0,
        )
        self._seed_translate_work_items(run_id, succeeded=5, pending=0)
        # No review work item seeded.

        advanced = self.executor._process_export_stage(
            run_id, export_type=ExportType.BILINGUAL_HTML
        )

        self.assertFalse(advanced)

    def test_merged_export_blocked_when_bilingual_not_done(self) -> None:
        """merged_html must wait for bilingual_html work items to succeed."""
        _document_id, run_id = self._seed_document_with_packets(
            translated_count=5,
            pending_count=0,
        )
        self._seed_translate_work_items(run_id, succeeded=5, pending=0)
        # Seed SUCCEEDED review
        with self.session_factory() as session:
            session.add(WorkItem(
                run_id=run_id,
                stage=WorkItemStage.REVIEW,
                scope_type=WorkItemScopeType.DOCUMENT,
                scope_id=str(uuid4()),
                status=WorkItemStatus.SUCCEEDED,
                attempt=1,
                input_version_bundle_json={},
            ))
            # Seed a PENDING bilingual export
            session.add(WorkItem(
                run_id=run_id,
                stage=WorkItemStage.EXPORT,
                scope_type=WorkItemScopeType.EXPORT,
                scope_id=str(uuid4()),
                status=WorkItemStatus.PENDING,
                attempt=1,
                input_version_bundle_json={"export_type": ExportType.BILINGUAL_HTML.value},
            ))
            session.commit()

        advanced = self.executor._process_export_stage(
            run_id, export_type=ExportType.MERGED_HTML
        )

        self.assertFalse(advanced)


if __name__ == "__main__":
    unittest.main()
