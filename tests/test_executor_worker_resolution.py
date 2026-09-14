"""Regression: the background run executor must translate with the configured
worker, not silently fall back to EchoTranslationWorker.

Before the fix, app startup built the executor from ``app.state.translation_worker``
while it was still ``None`` and never re-read it, so every background run used the
echo placeholder.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

from book_agent.app.main import create_app
from book_agent.app.runtime.document_run_executor import DocumentRunExecutor
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.workers.translator import EchoTranslationWorker


class _SentinelWorker(EchoTranslationWorker):
    """Distinguishable stand-in for a real provider-backed worker."""


class ExecutorWorkerResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        self.engine = build_engine(
            f"sqlite+pysqlite:///{Path(tempdir.name) / 'book-agent.db'}",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        self.export_root = str(Path(tempdir.name) / "exports")

    def test_executor_prefers_resolver_over_fixed_worker(self) -> None:
        resolved = _SentinelWorker()
        executor = DocumentRunExecutor(
            session_factory=self.session_factory,
            export_root=self.export_root,
            translation_worker=None,
            translation_worker_resolver=lambda: resolved,
            enable_controller_runner=False,
        )
        with self.session_factory() as session:
            worker = executor._workflow_service(session).translation_service.worker
        self.assertIs(worker, resolved)

    def test_lifespan_executor_uses_provider_resolved_worker(self) -> None:
        resolved = _SentinelWorker()
        app = create_app()
        app.state.session_factory = self.session_factory
        app.state.export_root = self.export_root
        with patch("book_agent.app.main.resolve_translation_worker", return_value=resolved):
            with TestClient(app):
                executor = app.state.document_run_executor
                self.assertIsNotNone(executor)
                with self.session_factory() as session:
                    worker = executor._workflow_service(session).translation_service.worker
        self.assertIs(worker, resolved)
        self.assertIsNone(app.state.document_run_executor)

    def test_explicit_app_worker_override_wins_over_resolved_cache(self) -> None:
        override = _SentinelWorker()
        app = create_app()
        app.state.session_factory = self.session_factory
        with patch("book_agent.app.main.resolve_translation_worker", return_value=EchoTranslationWorker()):
            self.assertIsNot(app.state.resolve_translation_worker(), override)
            app.state.translation_worker = override
            self.assertIs(app.state.resolve_translation_worker(), override)

    def test_disabling_controller_runner_is_honored(self) -> None:
        executor = DocumentRunExecutor(
            session_factory=self.session_factory,
            export_root=self.export_root,
            translation_worker=None,
            enable_controller_runner=False,
        )
        self.assertIsNone(executor._controller_runner)


if __name__ == "__main__":
    unittest.main()
