# ruff: noqa: E402

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
    DocumentRunStatus,
    DocumentRunType,
    DocumentStatus,
    SourceType,
)
from book_agent.domain.models import Document
from book_agent.domain.models.ops import DocumentRun
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.run_control import RunControlRepository


class FinalizeStageSnapshotsOnSuccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        self.export_root = tempfile.mkdtemp(prefix="finalize-stage-")
        self.executor = DocumentRunExecutor(
            session_factory=self.session_factory,
            export_root=self.export_root,
            translation_worker=None,
            enable_controller_runner=False,
        )

    def _seed_run(self, *, stages: dict) -> str:
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"finalize-{uuid4()}",
                source_path="/tmp/finalize.epub",
                title="Finalize Stage",
                author="Tester",
                src_lang="en",
                tgt_lang="zh",
                status=DocumentStatus.ACTIVE,
                parser_version=1,
                segmentation_version=1,
            )
            session.add(document)
            session.flush()
            run = DocumentRun(
                document_id=document.id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                status=DocumentRunStatus.RUNNING,
                requested_by="test",
                priority=100,
                status_detail_json={"pipeline": {"stages": stages}},
            )
            session.add(run)
            session.commit()
            return run.id

    def _read_stages(self, run_id: str) -> dict:
        with self.session_factory() as session:
            run = RunControlRepository(session).get_run(run_id)
            return dict((run.status_detail_json or {}).get("pipeline", {}).get("stages") or {})

    def test_sync_pipeline_status_finalizes_stale_stage_snapshots(self) -> None:
        run_id = self._seed_run(
            stages={
                "translate": {"status": "running", "pending_packet_count": 1},
                "review": {"status": "running"},
                "bilingual_html": {"status": "pending"},
                "merged_html": {"status": "pending"},
            }
        )

        self.executor._sync_pipeline_status(run_id, "succeeded")

        stages = self._read_stages(run_id)
        self.assertEqual(stages["translate"]["status"], "succeeded")
        self.assertEqual(stages["translate"]["pending_packet_count"], 0)
        self.assertEqual(stages["review"]["status"], "succeeded")
        self.assertEqual(stages["bilingual_html"]["status"], "succeeded")
        self.assertEqual(stages["merged_html"]["status"], "succeeded")
        self.assertEqual(stages["pipeline"]["status"], "succeeded")

    def test_finalize_preserves_terminal_non_success_statuses(self) -> None:
        run_id = self._seed_run(
            stages={
                "translate": {"status": "succeeded", "pending_packet_count": 0},
                "review": {"status": "failed", "failure_reason": "boom"},
                "bilingual_html": {"status": "cancelled"},
                "merged_html": {"status": "running"},
            }
        )

        self.executor._sync_pipeline_status(run_id, "succeeded")

        stages = self._read_stages(run_id)
        self.assertEqual(stages["translate"]["status"], "succeeded")
        self.assertEqual(stages["review"]["status"], "failed")
        self.assertEqual(stages["review"]["failure_reason"], "boom")
        self.assertEqual(stages["bilingual_html"]["status"], "cancelled")
        self.assertEqual(stages["merged_html"]["status"], "succeeded")
        self.assertEqual(stages["pipeline"]["status"], "succeeded")

    def test_finalize_is_no_op_when_pipeline_stages_missing(self) -> None:
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"finalize-empty-{uuid4()}",
                source_path="/tmp/finalize-empty.epub",
                title="Finalize Empty",
                author="Tester",
                src_lang="en",
                tgt_lang="zh",
                status=DocumentStatus.ACTIVE,
                parser_version=1,
                segmentation_version=1,
            )
            session.add(document)
            session.flush()
            run = DocumentRun(
                document_id=document.id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                status=DocumentRunStatus.RUNNING,
                requested_by="test",
                priority=100,
                status_detail_json={},
            )
            session.add(run)
            session.commit()
            run_id = run.id

        self.executor._sync_pipeline_status(run_id, "succeeded")

        with self.session_factory() as session:
            run = RunControlRepository(session).get_run(run_id)
            detail = run.status_detail_json or {}
            pipeline = detail.get("pipeline") or {}
            self.assertEqual(pipeline.get("stages", {}).get("pipeline", {}).get("status"), "succeeded")

    def test_sync_pipeline_status_propagates_non_success_terminal(self) -> None:
        run_id = self._seed_run(
            stages={"translate": {"status": "running"}}
        )

        self.executor._sync_pipeline_status(run_id, "failed")

        stages = self._read_stages(run_id)
        # Non-success terminals must NOT rewrite individual stage snapshots.
        self.assertEqual(stages["translate"]["status"], "running")
        self.assertEqual(stages["pipeline"]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
