# ruff: noqa: E402
"""Regression pins for the run lineage query API (P0.3c).

After retry_run cold-starts a fresh DocumentRun row (P0.3b), operators
and automation need a way to reconstruct the chain without walking
``resume_from_run_id`` by hand. ``get_run_lineage`` materializes the
whole chain — ancestors reached by following ``resume_from_run_id``
up to the root, plus every descendant reachable forward — for any run
in the lineage.

Lineage is technically a tree (an operator could retry the same
predecessor twice), so we cover the branching case as well as the
linear one.
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

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
from book_agent.services.run_control import RunControlService


class RunLineageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        self.base_time = datetime(2026, 4, 21, 12, 0, 0, tzinfo=timezone.utc)
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"lineage-{uuid4()}",
                source_path="/tmp/lineage.epub",
                title="Lineage",
                author="tester",
                src_lang="en",
                tgt_lang="zh",
                status=DocumentStatus.ACTIVE,
                parser_version=1,
                segmentation_version=1,
            )
            session.add(document)
            session.flush()
            session.commit()
            self.document_id = document.id

    def _service(self, session) -> RunControlService:
        return RunControlService(RunControlRepository(session))

    def _seed_run(
        self,
        *,
        status: DocumentRunStatus,
        resume_from_run_id: str | None,
        created_offset_seconds: int,
    ) -> str:
        with self.session_factory() as session:
            run = DocumentRun(
                document_id=self.document_id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                status=status,
                requested_by="operator",
                priority=100,
                resume_from_run_id=resume_from_run_id,
                created_at=self.base_time + timedelta(seconds=created_offset_seconds),
                updated_at=self.base_time + timedelta(seconds=created_offset_seconds),
            )
            session.add(run)
            session.flush()
            session.commit()
            return run.id

    # ---- linear chain --------------------------------------------------

    def test_lineage_walks_ancestors_and_descendants(self) -> None:
        # A (FAILED) → B (FAILED, resume_from=A) → C (RUNNING, resume_from=B)
        a = self._seed_run(status=DocumentRunStatus.FAILED, resume_from_run_id=None, created_offset_seconds=0)
        b = self._seed_run(status=DocumentRunStatus.FAILED, resume_from_run_id=a, created_offset_seconds=10)
        c = self._seed_run(status=DocumentRunStatus.RUNNING, resume_from_run_id=b, created_offset_seconds=20)

        with self.session_factory() as session:
            chain = self._service(session).get_run_lineage(b)

        self.assertEqual(chain.focus_run_id, b)
        self.assertEqual(chain.root_run_id, a)
        self.assertEqual(chain.document_id, self.document_id)
        self.assertEqual(chain.node_count, 3)
        self.assertEqual([n.run_id for n in chain.nodes], [a, b, c])
        # Spot-check: predecessor edge surfaces in the node.
        self.assertEqual(chain.nodes[1].resume_from_run_id, a)
        self.assertEqual(chain.nodes[2].resume_from_run_id, b)

    def test_lineage_from_root_still_collects_descendants(self) -> None:
        a = self._seed_run(status=DocumentRunStatus.FAILED, resume_from_run_id=None, created_offset_seconds=0)
        b = self._seed_run(status=DocumentRunStatus.RUNNING, resume_from_run_id=a, created_offset_seconds=10)

        with self.session_factory() as session:
            chain = self._service(session).get_run_lineage(a)

        self.assertEqual(chain.focus_run_id, a)
        self.assertEqual(chain.root_run_id, a)
        self.assertEqual([n.run_id for n in chain.nodes], [a, b])

    # ---- branching chain -----------------------------------------------

    def test_lineage_covers_branches_when_same_predecessor_retried_twice(self) -> None:
        # Operator retried A twice — both B and C resume from A.
        # Lineage must surface the whole tree, not just one branch.
        a = self._seed_run(status=DocumentRunStatus.FAILED, resume_from_run_id=None, created_offset_seconds=0)
        b = self._seed_run(status=DocumentRunStatus.FAILED, resume_from_run_id=a, created_offset_seconds=10)
        c = self._seed_run(status=DocumentRunStatus.RUNNING, resume_from_run_id=a, created_offset_seconds=20)

        with self.session_factory() as session:
            chain = self._service(session).get_run_lineage(b)

        self.assertEqual(chain.root_run_id, a)
        self.assertEqual(chain.node_count, 3)
        self.assertEqual([n.run_id for n in chain.nodes], [a, b, c])

    # ---- isolation -----------------------------------------------------

    def test_lineage_isolates_per_document(self) -> None:
        a = self._seed_run(status=DocumentRunStatus.FAILED, resume_from_run_id=None, created_offset_seconds=0)
        with self.session_factory() as session:
            other_doc = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"lineage-other-{uuid4()}",
                source_path="/tmp/lineage2.epub",
                title="Lineage2",
                author="tester",
                src_lang="en",
                tgt_lang="zh",
                status=DocumentStatus.ACTIVE,
                parser_version=1,
                segmentation_version=1,
            )
            session.add(other_doc)
            session.flush()
            other_run = DocumentRun(
                document_id=other_doc.id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                status=DocumentRunStatus.RUNNING,
                requested_by="other",
                priority=100,
            )
            session.add(other_run)
            session.flush()
            session.commit()

        with self.session_factory() as session:
            chain = self._service(session).get_run_lineage(a)

        self.assertEqual(chain.node_count, 1)
        self.assertEqual([n.run_id for n in chain.nodes], [a])

    def test_lineage_unknown_run_raises(self) -> None:
        with self.session_factory() as session:
            with self.assertRaises(ValueError):
                self._service(session).get_run_lineage("does-not-exist")


if __name__ == "__main__":
    unittest.main()
