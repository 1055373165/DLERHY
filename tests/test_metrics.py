"""Prometheus metrics: registry format, LLM event counters, HTTP middleware, endpoint access."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

from book_agent.app.main import create_app
from book_agent.core.config import get_settings
from book_agent.domain.event_kinds import LLM_CALL_COMPLETED, LLM_CALL_FAILED
from book_agent.domain.models.auth import DEFAULT_ORG_ID
from book_agent.infra import metrics
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.events import emit_event
from book_agent.services.api_keys import ApiKeyService

ROOT = Path(__file__).resolve().parents[1]


class RegistryTests(unittest.TestCase):
    def test_counter_histogram_and_gauge_exposition(self) -> None:
        counter = metrics.Counter("t_total", "help", ("kind",))
        counter.inc(kind='a"b')
        counter.inc(2, kind='a"b')
        counter.inc(-5, kind="ignored")
        histogram = metrics.Histogram("t_seconds", "help", buckets=(0.1, 1.0))
        histogram.observe(0.05)
        histogram.observe(0.5)
        text = "\n".join(counter.render() + histogram.render() + metrics.render_gauge("t_gauge", "help", [({"state": "x"}, 3.0)]))
        self.assertIn('# TYPE t_total counter', text)
        self.assertIn('t_total{kind="a\\"b"} 3', text)
        self.assertNotIn("ignored", text)
        self.assertIn('t_seconds_bucket{le="0.1"} 1', text)
        self.assertIn('t_seconds_bucket{le="1"} 2', text)
        self.assertIn('t_seconds_bucket{le="+Inf"} 2', text)
        self.assertIn("t_seconds_count 2", text)
        self.assertIn('t_gauge{state="x"} 3', text)

    def test_llm_events_feed_call_token_and_cost_counters(self) -> None:
        engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        before_calls = metrics.LLM_CALLS.value(call_kind="metrics-test", status="completed")
        before_tokens = metrics.LLM_TOKENS.value(call_kind="metrics-test", direction="out")
        before_failed = metrics.LLM_CALLS.value(call_kind="metrics-test", status="failed")
        with build_session_factory(engine=engine)() as session:
            emit_event(session, kind=LLM_CALL_COMPLETED, payload={"call_kind": "metrics-test", "token_in": 10, "token_out": 7, "cost_usd": 0.01, "latency_ms": 250})
            emit_event(session, kind=LLM_CALL_FAILED, payload={"call_kind": "metrics-test"})
        engine.dispose()
        self.assertEqual(metrics.LLM_CALLS.value(call_kind="metrics-test", status="completed"), before_calls + 1)
        self.assertEqual(metrics.LLM_TOKENS.value(call_kind="metrics-test", direction="out"), before_tokens + 7)
        self.assertEqual(metrics.LLM_CALLS.value(call_kind="metrics-test", status="failed"), before_failed + 1)
        self.assertGreaterEqual(metrics.LLM_LATENCY.count(call_kind="metrics-test"), 1)


class MetricsEndpointTests(unittest.TestCase):
    def _client(self, env: dict[str, str]) -> TestClient:
        tempdir = tempfile.TemporaryDirectory(dir=ROOT / ".test-tmp")
        self.addCleanup(tempdir.cleanup)
        engine = build_engine(f"sqlite+pysqlite:///{Path(tempdir.name) / 'm.db'}", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        get_settings.cache_clear()
        self.addCleanup(get_settings.cache_clear)
        app = create_app()
        app.state.session_factory = build_session_factory(engine=engine)
        self.session_factory = app.state.session_factory
        client = TestClient(app)
        self.addCleanup(client.close)
        return client

    def test_open_in_development_with_http_route_templates_and_gauges(self) -> None:
        client = self._client({"BOOK_AGENT_AUTH_MODE": "disabled"})
        client.get("/v1/documents/00000000-0000-0000-0000-000000000000")
        body = client.get("/metrics").text
        self.assertIn('route="/v1/documents/{document_id}"', body)
        self.assertIn("book_agent_http_request_duration_seconds_bucket", body)
        self.assertIn("book_agent_metrics_database_up 1", body)
        self.assertIn("# TYPE book_agent_runs gauge", body)
        self.assertNotIn('route="/metrics"', body)

    def test_token_or_admin_key_required_when_protected(self) -> None:
        client = self._client({"BOOK_AGENT_METRICS_TOKEN": "scrape-secret", "BOOK_AGENT_AUTH_MODE": "disabled"})
        self.assertEqual(client.get("/metrics").status_code, 401)
        self.assertEqual(client.get("/metrics", headers={"Authorization": "Bearer scrape-secret"}).status_code, 200)

        client = self._client({"BOOK_AGENT_METRICS_TOKEN": "", "BOOK_AGENT_AUTH_MODE": "api_key"})
        with self.session_factory() as session:
            service = ApiKeyService(session)
            viewer = service.create(org_id=DEFAULT_ORG_ID, name="v", role="viewer").plaintext
            admin = service.create(org_id=DEFAULT_ORG_ID, name="a", role="admin").plaintext
            session.commit()
        self.assertEqual(client.get("/metrics", headers={"Authorization": f"Bearer {viewer}"}).status_code, 401)
        self.assertEqual(client.get("/metrics", headers={"Authorization": f"Bearer {admin}"}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
