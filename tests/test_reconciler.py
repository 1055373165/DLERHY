# ruff: noqa: E402
"""Phase 2 tests for :mod:`book_agent.orchestrator.reconciler`.

Pins the core invariant that caused the Stage 2/3/4 production defect:
JSON cache claiming ``succeeded`` while physical rows tell a different
story MUST yield a P0 drift finding.
"""

import os
import sys
import unittest
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
os.environ.setdefault("BOOK_AGENT_TRANSLATION_BACKEND", "echo")
os.environ.setdefault("BOOK_AGENT_TRANSLATION_MODEL", "echo-worker")
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.enums import (
    ChapterStatus,
    DocumentRunStatus,
    DocumentRunType,
    DocumentStatus,
    PacketStatus,
    PacketType,
    SourceType,
    WorkItemScopeType,
    WorkItemStage,
    WorkItemStatus,
)
from book_agent.domain.models import Chapter, Document
from book_agent.domain.models.ops import DocumentRun, StageTransition, WorkItem
from book_agent.domain.models.translation import TranslationPacket
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.orchestrator.reconciler import (
    DRIFT_KIND_CACHE_OVER_REPORTS_RUNNING,
    DRIFT_KIND_CACHE_OVER_REPORTS_SUCCESS,
    Reconciler,
)


class ReconcilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _seed_run(
        self,
        *,
        translated: int = 0,
        pending: int = 0,
        pipeline_stages: dict[str, dict[str, str]] | None = None,
    ) -> tuple[str, str]:
        with self.session_factory() as session:
            doc = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"rec-{uuid4()}",
                source_path="/tmp/rec.epub",
                title="t", author="a", src_lang="en", tgt_lang="zh",
                status=DocumentStatus.ACTIVE,
                parser_version=1, segmentation_version=1,
            )
            session.add(doc)
            session.flush()
            chapter = Chapter(
                document_id=doc.id, ordinal=1,
                status=ChapterStatus.PACKET_BUILT,
            )
            session.add(chapter)
            session.flush()
            for _ in range(translated):
                session.add(TranslationPacket(
                    chapter_id=chapter.id,
                    packet_type=PacketType.TRANSLATE,
                    book_profile_version=1,
                    status=PacketStatus.TRANSLATED,
                ))
            for _ in range(pending):
                session.add(TranslationPacket(
                    chapter_id=chapter.id,
                    packet_type=PacketType.TRANSLATE,
                    book_profile_version=1,
                    status=PacketStatus.BUILT,
                ))
            run = DocumentRun(
                document_id=doc.id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                status=DocumentRunStatus.RUNNING,
                requested_by="test",
                priority=100,
                status_detail_json={
                    "pipeline": {"stages": pipeline_stages or {}},
                },
            )
            session.add(run)
            session.commit()
            return doc.id, run.id

    def _add_wi(
        self,
        run_id: str,
        stage: WorkItemStage,
        status: WorkItemStatus,
    ) -> None:
        with self.session_factory() as session:
            scope_type = (
                WorkItemScopeType.PACKET if stage == WorkItemStage.TRANSLATE
                else WorkItemScopeType.DOCUMENT if stage == WorkItemStage.REVIEW
                else WorkItemScopeType.EXPORT
            )
            session.add(WorkItem(
                run_id=run_id,
                stage=stage,
                scope_type=scope_type,
                scope_id=str(uuid4()),
                status=status,
                attempt=1,
                input_version_bundle_json={},
            ))
            session.commit()

    # --- findings ------------------------------------------------------

    def test_no_drift_when_cache_matches_physics(self) -> None:
        _doc, run_id = self._seed_run()
        with self.session_factory() as session:
            self.assertEqual(Reconciler(session).check_run(run_id), [])

    def test_success_lie_caught_when_translate_still_running(self) -> None:
        """Production defect: review cache=succeeded while translate=82/430."""
        _doc, run_id = self._seed_run(
            translated=82, pending=348,
            pipeline_stages={
                "translate": {"status": "succeeded"},
                "review": {"status": "succeeded"},
            },
        )
        for _ in range(82):
            self._add_wi(run_id, WorkItemStage.TRANSLATE, WorkItemStatus.SUCCEEDED)
        for _ in range(348):
            self._add_wi(run_id, WorkItemStage.TRANSLATE, WorkItemStatus.PENDING)

        with self.session_factory() as session:
            findings = Reconciler(session).check_run(run_id)

        stages_flagged = {f.stage: f for f in findings}
        self.assertIn("translate", stages_flagged)
        self.assertEqual(
            stages_flagged["translate"].kind,
            DRIFT_KIND_CACHE_OVER_REPORTS_SUCCESS,
        )
        self.assertIn("review", stages_flagged)

    def test_running_lie_caught_when_nothing_seeded(self) -> None:
        _doc, run_id = self._seed_run(
            pipeline_stages={"translate": {"status": "running"}},
        )
        with self.session_factory() as session:
            findings = Reconciler(session).check_run(run_id)
        kinds = {f.kind for f in findings}
        self.assertIn(DRIFT_KIND_CACHE_OVER_REPORTS_RUNNING, kinds)

    def test_check_and_audit_writes_transition_row_per_finding(self) -> None:
        _doc, run_id = self._seed_run(
            translated=1, pending=1,
            pipeline_stages={"translate": {"status": "succeeded"}},
        )
        self._add_wi(run_id, WorkItemStage.TRANSLATE, WorkItemStatus.SUCCEEDED)
        self._add_wi(run_id, WorkItemStage.TRANSLATE, WorkItemStatus.PENDING)

        with self.session_factory() as session:
            findings = Reconciler(session).check_and_audit(run_id)
            session.commit()

        self.assertEqual(len(findings), 1)
        with self.session_factory() as session:
            rows = list(session.scalars(sa.select(StageTransition)).all())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].stage, "translate")
        self.assertEqual(rows[0].to_status, "drift_detected")
        self.assertEqual(rows[0].triggered_by, "reconciler")
        self.assertIn(
            DRIFT_KIND_CACHE_OVER_REPORTS_SUCCESS, rows[0].reason,
        )


if __name__ == "__main__":
    unittest.main()
