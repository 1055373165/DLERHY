"""Multi-instance execution: run ownership leases, the API-only switch, SKIP LOCKED claims, cross-process worker cache invalidation."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.dialects import postgresql
from sqlalchemy.pool import StaticPool

from book_agent.app.runtime.document_run_executor import (
    DisabledRunExecutor,
    DocumentRunExecutor,
    ensure_document_run_executor,
    executor_instance_id,
)
from book_agent.core.config import Settings
from book_agent.domain.enums import DocumentRunType, DocumentStatus, ProviderKind, SourceType, WorkItemStage
from book_agent.domain.models import Document
from book_agent.domain.models.ops import DocumentRun
from book_agent.domain.models.provider_credential import ProviderCredential
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.services import provider_credentials
from book_agent.services.run_control import RunControlService
from book_agent.workers.factory import TranslationWorkerProvider


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine(
            "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _executor(self, instance_id: str, ttl: float = 30.0) -> DocumentRunExecutor:
        return DocumentRunExecutor(
            session_factory=self.session_factory,
            export_root="artifacts/exports",
            translation_worker=None,
            state_reconciler_interval_seconds=0,
            poll_interval_seconds=0,
            run_ownership_ttl_seconds=ttl,
            instance_id=instance_id,
        )

    def _running_run(self) -> str:
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"multi-instance-{uuid4()}",
                source_path="/srv/books/multi.epub",
                title="Multi Instance",
                src_lang="en",
                tgt_lang="zh",
                status=DocumentStatus.ACTIVE,
                parser_version=1,
                segmentation_version=1,
            )
            session.add(document)
            session.flush()
            control = RunControlService(RunControlRepository(session))
            run = control.create_run(
                document_id=document.id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                requested_by="multi-instance-test",
                status_detail_json={"run_request": {"terminology": "skip"}},
            )
            control.resume_run(run.run_id, actor_id="multi-instance-test", note="start")
            session.commit()
            return run.run_id

    def _owner(self, run_id: str) -> tuple[str | None, datetime | None]:
        with self.session_factory() as session:
            run = session.get(DocumentRun, run_id)
            return run.executor_owner, run.executor_lease_expires_at


class RunOwnershipTests(_Base):
    def test_one_instance_owns_a_run_until_its_lease_expires(self) -> None:
        run_id = self._running_run()
        first, second = self._executor("host-a:1:aaaa"), self._executor("host-b:2:bbbb")

        self.assertEqual(first._acquire_run_ownership([run_id]), [run_id])
        self.assertEqual(second._acquire_run_ownership([run_id]), [])
        # Taking ownership is not progress: updated_at (read by stale-run detection) stays put.
        with self.session_factory() as session:
            before_updated = session.get(DocumentRun, run_id).updated_at
        first._acquire_run_ownership([run_id])
        with self.session_factory() as session:
            self.assertEqual(session.get(DocumentRun, run_id).updated_at, before_updated)
        # Renewal by the owner succeeds and pushes the expiry forward.
        _, expires_before = self._owner(run_id)
        self.assertEqual(first._acquire_run_ownership([run_id]), [run_id])
        self.assertGreaterEqual(self._owner(run_id)[1], expires_before)

        # The owner stops renewing: once the lease has expired the other instance takes over.
        with self.session_factory() as session:
            session.execute(
                update(DocumentRun)
                .where(DocumentRun.id == run_id)
                .values(executor_lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
            )
            session.commit()
        self.assertEqual(second._acquire_run_ownership([run_id]), [run_id])
        self.assertEqual(self._owner(run_id)[0], "host-b:2:bbbb")

        # The previous owner's run loop notices on its next tick and exits without doing work.
        with patch.object(first, "_reclaim_expired_leases") as reclaim:
            first._run_loop(run_id)
        reclaim.assert_not_called()

    def test_stop_releases_owned_runs(self) -> None:
        run_id = self._running_run()
        executor = self._executor("host-a:1:aaaa")
        executor._acquire_run_ownership([run_id])
        self.assertTrue(executor.stop(work_timeout_seconds=0))
        self.assertEqual(self._owner(run_id), (None, None))
        self.assertEqual(self._executor("host-b:2:bbbb")._acquire_run_ownership([run_id]), [run_id])

    def test_instance_ids_name_host_and_process(self) -> None:
        import os
        import socket

        instance = executor_instance_id()
        host, pid, suffix = instance.rsplit(":", 2)
        self.assertEqual((host, pid), (socket.gethostname(), str(os.getpid())))
        self.assertEqual(len(suffix), 8)
        self.assertNotEqual(instance, executor_instance_id())

    def test_api_only_replicas_get_a_disabled_executor(self) -> None:
        app = SimpleNamespace(state=SimpleNamespace(document_run_executor=None))
        with patch(
            "book_agent.core.config.get_settings",
            return_value=Settings(translation_backend="echo", run_executor_enabled=False),
        ):
            executor = ensure_document_run_executor(app)
        self.assertIsInstance(executor, DisabledRunExecutor)
        executor.wake("any-run")
        self.assertTrue(executor.stop())


class ClaimQueryTests(_Base):
    def test_claim_candidates_skip_rows_locked_by_other_claimers_on_postgres(self) -> None:
        with self.session_factory() as session:
            stmt = RunControlRepository(session).claimable_work_items_statement(
                "run-id", stage=WorkItemStage.TRANSLATE, scan_limit=16
            )
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        self.assertIn("FOR UPDATE SKIP LOCKED", sql)


class WorkerCacheTests(_Base):
    def _credential(self) -> str:
        with self.session_factory() as session:
            record = provider_credentials.create_credential(
                session,
                name="echo",
                provider_kind=ProviderKind.ECHO,
                model_name="echo-worker",
                base_url="http://localhost",
                api_key=None,
                streaming=False,
                max_output_tokens=1024,
                timeout_seconds=30,
                max_retries=0,
                retry_backoff_seconds=1.0,
                activate=True,
            )
            session.commit()
            return record.id

    def test_a_change_committed_by_another_process_rebuilds_the_worker_after_the_check_interval(self) -> None:
        credential_id = self._credential()
        clock = [100.0]
        provider = TranslationWorkerProvider(
            settings=Settings(translation_backend="echo"),
            session_factory=lambda: self.session_factory,
            db_check_interval_seconds=5,
            clock=lambda: clock[0],
        )
        # Another process does not share this process's in-memory counter.
        with patch.object(provider_credentials, "current_revision", return_value=0):
            worker = provider.get()
            self.assertIs(provider.get(), worker)
            with self.session_factory() as session:
                session.execute(
                    update(ProviderCredential)
                    .where(ProviderCredential.id == credential_id)
                    .values(config_revision=ProviderCredential.config_revision + 1)
                )
                session.commit()
            clock[0] += 1
            self.assertIs(provider.get(), worker, "the database is re-read at most once per interval")
            clock[0] += 5
            rebuilt = provider.get()
            self.assertIsNot(rebuilt, worker)
            clock[0] += 5
            self.assertIs(provider.get(), rebuilt, "an unchanged key keeps the cached worker")

    def test_editing_or_activating_the_active_credential_bumps_its_revision(self) -> None:
        credential_id = self._credential()
        with self.session_factory() as session:
            before = session.get(ProviderCredential, credential_id).config_revision
            provider_credentials.update_credential(session, credential_id, max_retries=3)
            session.commit()
            after_edit = session.get(ProviderCredential, credential_id).config_revision
            provider_credentials.activate_credential(session, credential_id)
            session.commit()
            after_activate = session.get(ProviderCredential, credential_id).config_revision
            key = provider_credentials.active_credential_key(session)
        self.assertEqual((after_edit, after_activate), (before + 1, before + 2))
        self.assertEqual(key, (credential_id, after_activate))


if __name__ == "__main__":
    unittest.main()
