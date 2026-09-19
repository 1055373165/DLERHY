"""With no model provider configured the app still opens, and translating says what to set up."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

from book_agent.core.config import Settings, get_settings
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.workers.failures import FailureDisposition, classify_failure
from book_agent.workers.factory import UnconfiguredTranslationWorker, resolve_translation_worker
from book_agent.workers.providers import ProviderNotConfigured
from tests.export_golden_scenario import write_epub

ROOT = Path(__file__).resolve().parents[1]


class ProviderNotConfiguredTests(unittest.TestCase):
    def setUp(self) -> None:
        tempdir = tempfile.TemporaryDirectory(dir=ROOT / ".test-tmp")
        self.addCleanup(tempdir.cleanup)
        self.root = Path(tempdir.name)
        engine = build_engine(
            f"sqlite+pysqlite:///{self.root / 'p.db'}", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        self.session_factory = build_session_factory(engine=engine)
        # The project .env may hold a real key; this test is about having none.
        for patcher in (
            patch.dict(Settings.model_config, {"env_file": None}),
            patch.dict(
                os.environ,
                {
                    "BOOK_AGENT_AUTH_MODE": "disabled",
                    "BOOK_AGENT_RUN_EXECUTOR_ENABLED": "false",
                    "BOOK_AGENT_TRANSLATION_BACKEND": "openai_compatible",
                    "BOOK_AGENT_TRANSLATION_OPENAI_API_KEY": "",
                    "BOOK_AGENT_EXPORT_ROOT": str(self.root / "exports"),
                    "BOOK_AGENT_UPLOAD_ROOT": str(self.root / "uploads"),
                },
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        get_settings.cache_clear()
        self.addCleanup(get_settings.cache_clear)

    def test_the_worker_stands_in_and_a_translation_pauses_the_run(self) -> None:
        with self.session_factory() as session:
            worker = resolve_translation_worker(session, get_settings())
        self.assertIsInstance(worker, UnconfiguredTranslationWorker)
        self.assertIn("服务商", worker.reason)
        with self.assertRaises(ProviderNotConfigured) as raised:
            worker.translate(None)
        classification = classify_failure(raised.exception)
        self.assertEqual(classification.disposition, FailureDisposition.PAUSE)
        self.assertEqual(classification.pause_reason, "provider.not_configured")

    def test_library_and_upload_work_and_starting_a_run_says_what_to_set_up(self) -> None:
        from book_agent.app.main import create_app

        app = create_app()
        app.state.session_factory = self.session_factory
        client = TestClient(app)
        self.addCleanup(client.close)

        self.assertEqual(client.get("/v1/documents/history?limit=5").status_code, 200)
        epub = write_epub(self.root)
        with epub.open("rb") as handle:
            uploaded = client.post(
                "/v1/documents/bootstrap-upload", files={"source_file": (epub.name, handle, "application/epub+zip")}
            )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        document_id = uploaded.json()["document_id"]

        refused = client.post("/v1/runs", json={"document_id": document_id, "run_type": "translate_full", "requested_by": "t"})
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertIn("服务商", refused.json()["detail"])
        # Exports need no model.
        exported = client.post("/v1/runs", json={"document_id": document_id, "run_type": "export_full", "requested_by": "t"})
        self.assertEqual(exported.status_code, 201, exported.text)
        cancelled = client.post(f"/v1/runs/{exported.json()['run_id']}/cancel", json={"actor_id": "t"})
        self.assertEqual(cancelled.status_code, 200, cancelled.text)

        # Adding a provider on the providers page unblocks it without a restart.
        created = client.post(
            "/v1/providers",
            json={"name": "echo", "provider_kind": "echo", "model_name": "echo-worker", "base_url": "http://localhost", "activate": True},
        )
        self.assertEqual(created.status_code, 201, created.text)
        started = client.post("/v1/runs", json={"document_id": document_id, "run_type": "translate_full", "requested_by": "t"})
        self.assertEqual(started.status_code, 201, started.text)


if __name__ == "__main__":
    unittest.main()
