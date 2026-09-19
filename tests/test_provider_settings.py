"""Provider prices and request overrides are set per credential (UI/API) and reach the worker."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

from book_agent.core.config import Settings, get_settings
from book_agent.domain.models.provider_credential import ProviderCredential
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.workers.factory import build_worker_from_credential

ROOT = Path(__file__).resolve().parents[1]


class ProviderSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        tempdir = tempfile.TemporaryDirectory(dir=ROOT / ".test-tmp")
        self.addCleanup(tempdir.cleanup)
        engine = build_engine(
            f"sqlite+pysqlite:///{Path(tempdir.name) / 'p.db'}", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        self.session_factory = build_session_factory(engine=engine)
        patcher = patch.dict(
            os.environ,
            {"BOOK_AGENT_AUTH_MODE": "disabled", "BOOK_AGENT_RUN_EXECUTOR_ENABLED": "false", "BOOK_AGENT_PROVIDER_ALLOW_PRIVATE_HOSTS": "true"},
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        get_settings.cache_clear()
        self.addCleanup(get_settings.cache_clear)
        from book_agent.app.main import create_app

        app = create_app()
        app.state.session_factory = self.session_factory
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def _create(self, **extra):
        body = {
            "name": "deepseek",
            "provider_kind": "openai_compatible",
            "model_name": "deepseek-v4-flash",
            "base_url": "https://api.deepseek.example/v1",
            "api_key": "sk-test-1234567890",
            **extra,
        }
        return self.client.post("/v1/providers", json=body)

    def test_prices_and_overrides_round_trip_and_reach_the_worker(self) -> None:
        created = self._create(
            input_cost_per_1m_tokens=0.27,
            input_cache_hit_cost_per_1m_tokens=0.07,
            output_cost_per_1m_tokens=1.1,
            request_overrides={"thinking": {"type": "disabled"}},
        )
        self.assertEqual(created.status_code, 201, created.text)
        body = created.json()
        self.assertEqual(
            (body["input_cost_per_1m_tokens"], body["output_cost_per_1m_tokens"], body["request_overrides"]),
            (0.27, 1.1, {"thinking": {"type": "disabled"}}),
        )
        with self.session_factory() as session:
            record = session.get(ProviderCredential, body["id"])
            worker = build_worker_from_credential(record, Settings(_env_file=None, translation_backend="echo"))
        self.assertEqual(worker.client.request_overrides, {"thinking": {"type": "disabled"}})
        self.assertEqual(worker.client.output_cost_per_1m_tokens, 1.1)
        # 1M input + 1M output tokens at these prices.
        self.assertAlmostEqual(
            worker.client._estimate_cost_usd(
                token_in=1_000_000, token_out=1_000_000, prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=1_000_000
            ),
            1.37,
            places=4,
        )

        # A PATCH changes only what it names; null clears a price.
        patched = self.client.patch(f"/v1/providers/{body['id']}", json={"output_cost_per_1m_tokens": None})
        self.assertEqual(patched.status_code, 200, patched.text)
        self.assertIsNone(patched.json()["output_cost_per_1m_tokens"])
        self.assertEqual(patched.json()["input_cost_per_1m_tokens"], 0.27)
        self.assertEqual(patched.json()["request_overrides"], {"thinking": {"type": "disabled"}})
        cleared = self.client.patch(f"/v1/providers/{body['id']}", json={"request_overrides": {}})
        self.assertEqual(cleared.json()["request_overrides"], {})

    def test_overrides_cannot_replace_fields_the_client_builds(self) -> None:
        refused = self._create(request_overrides={"messages": [], "temperature": 0.2})
        self.assertIn(refused.status_code, (409, 422))
        self.assertIn("messages", refused.json()["detail"])
        created = self._create(request_overrides={"temperature": 0.2})
        self.assertEqual(created.status_code, 201)
        bad = self.client.patch(f"/v1/providers/{created.json()['id']}", json={"request_overrides": {"model": "x"}})
        self.assertEqual(bad.status_code, 422)

    def test_settings_prices_remain_the_fallback(self) -> None:
        created = self._create()
        with self.session_factory() as session:
            record = session.get(ProviderCredential, created.json()["id"])
            worker = build_worker_from_credential(
                record,
                Settings(_env_file=None, translation_backend="echo", translation_output_cost_per_1m_tokens=2.0),
            )
        self.assertEqual(worker.client.output_cost_per_1m_tokens, 2.0)


if __name__ == "__main__":
    unittest.main()
