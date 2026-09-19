"""Provider credential endpoints.

GET requests run in a session that rolls back on exit; the first-touch
bootstrap from settings must still persist, and must not re-run (and bump the
worker cache revision) on every read.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from book_agent.app.main import create_app
from book_agent.core.config import Settings
from book_agent.domain.models import ProviderCredential
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.services.provider_credentials import current_revision


class ProvidersApiTests(unittest.TestCase):
    def setUp(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        self.engine = build_engine(
            f"sqlite+pysqlite:///{Path(tempdir.name) / 'book-agent.db'}",
            connect_args={"check_same_thread": False},
        )
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        app = create_app()
        app.state.session_factory = self.session_factory
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        settings_patch = patch(
            "book_agent.app.api.routes.providers.get_settings",
            return_value=Settings(translation_backend="echo", translation_model="echo-worker"),
        )
        settings_patch.start()
        self.addCleanup(settings_patch.stop)

    def _credential_count(self) -> int:
        with self.session_factory() as session:
            return session.scalar(select(func.count(ProviderCredential.id))) or 0

    def test_list_bootstrap_persists_once(self) -> None:
        first = self.client.get("/v1/providers")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(len(first.json()), 1)
        self.assertEqual(self._credential_count(), 1)
        revision_after_bootstrap = current_revision()

        second = self.client.get("/v1/providers")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(second.json()), 1)
        self.assertEqual(self._credential_count(), 1)
        self.assertEqual(current_revision(), revision_after_bootstrap)

    def test_active_bootstrap_persists(self) -> None:
        active = self.client.get("/v1/providers/active")
        self.assertEqual(active.status_code, 200)
        self.assertIsNotNone(active.json())
        self.assertEqual(self._credential_count(), 1)


if __name__ == "__main__":
    unittest.main()
