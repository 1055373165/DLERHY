# ruff: noqa: E402
"""Regression pins for CAS work_item transitions (P0.1b).

RunControlRepository.release_work_item and .expire_lease each flip a
work_item out of the LEASED/RUNNING "owned by a worker" region. If the
transition is not compare-and-swap, a late-arriving release can clobber
a terminal decision already made by the lease reaper (or vice versa):

- Worker finishes a packet, calls release_work_item(status=SUCCEEDED)
- Lease reaper had already flipped the work_item to RETRYABLE_FAILED a
  few ms earlier because heartbeat timed out
- Without CAS, release's ORM attribute write overwrites RETRYABLE_FAILED
  with SUCCEEDED → the run thinks it succeeded, in reality the worker's
  result was never committed to translation_packets

These tests reproduce that race by priming the DB state manually (a
live lease token pointing at a work_item that some "other" actor has
already moved out of LEASED/RUNNING) and asserting the transition is
a no-op on the work_item.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
os.environ.setdefault("BOOK_AGENT_TRANSLATION_BACKEND", "echo")
os.environ.setdefault("BOOK_AGENT_TRANSLATION_MODEL", "echo-worker")
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.enums import (
    DocumentRunStatus,
    DocumentRunType,
    DocumentStatus,
    SourceType,
    WorkItemScopeType,
    WorkItemStage,
    WorkItemStatus,
    WorkerLeaseStatus,
)
from book_agent.domain.models import Document
from book_agent.domain.models.ops import DocumentRun, WorkItem, WorkerLease
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.run_control import RunControlRepository


class WorkItemCASTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _seed_leased_work_item(
        self, *, preempted_status: WorkItemStatus | None = None
    ) -> tuple[str, str, str]:
        """Return (run_id, work_item_id, lease_token).

        If ``preempted_status`` is set, the work_item is placed in that
        status instead of LEASED, simulating an actor that already
        raced ahead of the caller's transition.
        """
        now = datetime.now(timezone.utc)
        lease_expires_at = now + timedelta(minutes=5)
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"cas-{uuid4()}",
                source_path="/tmp/cas.epub",
                title="CAS",
                author="tester",
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
                started_at=now,
            )
            session.add(run)
            session.flush()
            work_item = WorkItem(
                run_id=run.id,
                stage=WorkItemStage.TRANSLATE,
                scope_type=WorkItemScopeType.PACKET,
                scope_id=str(uuid4()),
                status=preempted_status or WorkItemStatus.LEASED,
                priority=100,
                lease_owner="worker-A",
                lease_expires_at=lease_expires_at,
                last_heartbeat_at=now,
            )
            session.add(work_item)
            session.flush()
            lease_token = f"tok-{uuid4()}"
            lease = WorkerLease(
                run_id=run.id,
                work_item_id=work_item.id,
                worker_name="worker",
                worker_instance_id="worker-A",
                lease_token=lease_token,
                status=WorkerLeaseStatus.ACTIVE,
                lease_expires_at=lease_expires_at,
                last_heartbeat_at=now,
            )
            session.add(lease)
            session.commit()
            return run.id, work_item.id, lease_token

    def _get_work_item_status(self, work_item_id: str) -> WorkItemStatus:
        with self.session_factory() as session:
            work_item = session.get(WorkItem, work_item_id)
            assert work_item is not None
            return work_item.status

    # ---- release_work_item CAS -----------------------------------------

    def test_release_does_not_clobber_already_retryable_failed(self) -> None:
        # Reaper beat the worker: work_item is already RETRYABLE_FAILED
        # but the lease is still ACTIVE (worker hasn't called release yet).
        # A late release(status=SUCCEEDED) must NOT overwrite the terminal
        # decision the reaper already made.
        _, work_item_id, lease_token = self._seed_leased_work_item(
            preempted_status=WorkItemStatus.RETRYABLE_FAILED
        )
        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            repo = RunControlRepository(session)
            repo.release_work_item(
                lease_token=lease_token,
                status=WorkItemStatus.SUCCEEDED,
                released_at=now,
                output_artifact_refs_json={"packet_id": str(uuid4())},
            )
            session.commit()
        self.assertEqual(
            self._get_work_item_status(work_item_id),
            WorkItemStatus.RETRYABLE_FAILED,
        )

    def test_release_flips_lease_even_when_work_item_preempted(self) -> None:
        # The worker's "I'm done" signal on the lease row must still be
        # recorded even when the work_item CAS misses — otherwise the
        # lease stays ACTIVE forever and the reaper keeps re-firing.
        run_id, _, lease_token = self._seed_leased_work_item(
            preempted_status=WorkItemStatus.RETRYABLE_FAILED
        )
        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            repo = RunControlRepository(session)
            repo.release_work_item(
                lease_token=lease_token,
                status=WorkItemStatus.SUCCEEDED,
                released_at=now,
            )
            session.commit()
        with self.session_factory() as session:
            from sqlalchemy import select as _select

            lease = session.scalar(
                _select(WorkerLease).where(WorkerLease.lease_token == lease_token)
            )
            self.assertIsNotNone(lease)
            self.assertEqual(lease.status, WorkerLeaseStatus.RELEASED)

    def test_release_succeeds_on_happy_path(self) -> None:
        # The healthy case: lease is ACTIVE, work_item is LEASED, worker
        # releases with SUCCEEDED. The CAS must not accidentally block
        # the normal flow.
        _, work_item_id, lease_token = self._seed_leased_work_item()
        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            repo = RunControlRepository(session)
            repo.release_work_item(
                lease_token=lease_token,
                status=WorkItemStatus.SUCCEEDED,
                released_at=now,
            )
            session.commit()
        self.assertEqual(
            self._get_work_item_status(work_item_id),
            WorkItemStatus.SUCCEEDED,
        )

    # ---- expire_lease CAS ----------------------------------------------

    def test_expire_lease_is_noop_when_work_item_already_succeeded(self) -> None:
        # Worker finished and released the work_item but the reaper hasn't
        # cleaned up the lease row yet. A late expire_lease must NOT flip
        # SUCCEEDED back to RETRYABLE_FAILED.
        _, work_item_id, _ = self._seed_leased_work_item(
            preempted_status=WorkItemStatus.SUCCEEDED
        )
        # Find the lease id
        with self.session_factory() as session:
            from sqlalchemy import select as _select

            lease = session.scalar(
                _select(WorkerLease).where(WorkerLease.work_item_id == work_item_id)
            )
            assert lease is not None
            lease_id = lease.id
        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            repo = RunControlRepository(session)
            result = repo.expire_lease(
                lease_id=lease_id,
                expired_at=now,
                error_class="lease_expired",
                error_detail_json={},
            )
            session.commit()
        self.assertIsNone(result)
        self.assertEqual(
            self._get_work_item_status(work_item_id),
            WorkItemStatus.SUCCEEDED,
        )

    def test_expire_lease_flips_to_retryable_failed_on_live_lease(self) -> None:
        # Healthy-path regression: lease ACTIVE + work_item RUNNING →
        # expire must actually flip to RETRYABLE_FAILED.
        _, work_item_id, _ = self._seed_leased_work_item(
            preempted_status=WorkItemStatus.RUNNING
        )
        with self.session_factory() as session:
            from sqlalchemy import select as _select

            lease = session.scalar(
                _select(WorkerLease).where(WorkerLease.work_item_id == work_item_id)
            )
            assert lease is not None
            lease_id = lease.id
        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            repo = RunControlRepository(session)
            result = repo.expire_lease(
                lease_id=lease_id,
                expired_at=now,
                error_class="lease_expired",
                error_detail_json={"source": "reaper"},
            )
            session.commit()
        self.assertIsNotNone(result)
        self.assertEqual(
            self._get_work_item_status(work_item_id),
            WorkItemStatus.RETRYABLE_FAILED,
        )


if __name__ == "__main__":
    unittest.main()
