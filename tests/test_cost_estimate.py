"""The pre-run cost estimate: tokens from the book, prices from the provider, modes change the total."""

from __future__ import annotations

import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

from book_agent.core.config import get_settings
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.orchestrator.bootstrap import BootstrapOrchestrator
from book_agent.services import provider_credentials
from book_agent.domain.enums import ProviderKind
from tests.test_translation_worker_abstraction import CHAPTER_XHTML, CONTAINER_XML, CONTENT_OPF, NAV_XHTML

ROOT = Path(__file__).resolve().parents[1]


class CostEstimateTests(unittest.TestCase):
    def setUp(self) -> None:
        tempdir = tempfile.TemporaryDirectory(dir=ROOT / ".test-tmp")
        self.addCleanup(tempdir.cleanup)
        root = Path(tempdir.name)
        engine = build_engine(f"sqlite+pysqlite:///{root / 'e.db'}", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        self.session_factory = build_session_factory(engine=engine)
        epub = root / "b.epub"
        with zipfile.ZipFile(epub, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", CONTENT_OPF)
            archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
            archive.writestr("OEBPS/chapter1.xhtml", CHAPTER_XHTML)
        artifacts = BootstrapOrchestrator().bootstrap_epub(epub)
        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()
        self.document_id = artifacts.document.id
        patcher = patch.dict(os.environ, {"BOOK_AGENT_AUTH_MODE": "disabled", "BOOK_AGENT_RUN_EXECUTOR_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        get_settings.cache_clear()
        self.addCleanup(get_settings.cache_clear)
        from book_agent.app.main import create_app

        app = create_app()
        app.state.session_factory = self.session_factory
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def _estimate(self, **params):
        response = self.client.get(f"/v1/documents/{self.document_id}/cost-estimate", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_tokens_without_prices_then_a_cost_range_with_them(self) -> None:
        estimate = self._estimate()
        self.assertGreater(estimate["packet_count"], 0)
        self.assertGreater(estimate["token_out"], 0)
        self.assertGreater(estimate["token_in"], estimate["token_out"])
        low, high = estimate["token_in_range"]
        self.assertLess(low, estimate["token_in"])
        self.assertGreater(high, estimate["token_in"])
        self.assertIsNone(estimate["cost_usd"])
        self.assertTrue(any("单价" in note for note in estimate["notes"]))
        self.assertTrue(any("思考" in note for note in estimate["notes"]))

        with self.session_factory() as session:
            record = provider_credentials.create_credential(
                session,
                name="priced",
                provider_kind=ProviderKind.ECHO,
                model_name="m",
                base_url="http://localhost",
                api_key=None,
                streaming=False,
                max_output_tokens=1024,
                timeout_seconds=30,
                max_retries=0,
                retry_backoff_seconds=1.0,
                activate=True,
                prices={"input_cost_per_1m_tokens": 1.0, "output_cost_per_1m_tokens": 4.0},
            )
            session.commit()
            name = record.name
        priced = self._estimate()
        expected = priced["token_in"] / 1e6 * 1.0 + priced["token_out"] / 1e6 * 4.0
        self.assertAlmostEqual(priced["cost_usd"], expected, places=3)
        self.assertEqual(priced["price_source"], f"provider:{name}")
        # An echo provider does not think, so there is no thinking warning.
        self.assertFalse(any("思考" in note for note in priced["notes"]))

    def test_run_modes_change_the_estimate(self) -> None:
        default = self._estimate()
        lean = self._estimate(terminology="skip", model_review="skip")
        thorough = self._estimate(terminology="thorough", model_review="full")
        self.assertLess(lean["token_in"], default["token_in"])
        self.assertGreater(thorough["token_in"], default["token_in"])
        # A short book pays for terminology in proportion to its length, not the full-book cost,
        # but the agents' own prompts set a floor (measured on a real 5-packet run).
        self.assertLess(default["breakdown"]["terminology"]["token_in"], 300_000)
        self.assertGreaterEqual(default["breakdown"]["terminology"]["token_in"], 10_000)
        self.assertGreaterEqual(default["breakdown"]["model_review"]["token_in"], 50_000)
        self.assertEqual(lean["breakdown"]["terminology"]["token_in"], 0)
        self.assertEqual(lean["breakdown"]["model_review"]["token_in"], 0)
        self.assertEqual(self.client.get("/v1/documents/00000000-0000-0000-0000-000000000000/cost-estimate").status_code, 404)


if __name__ == "__main__":
    unittest.main()
