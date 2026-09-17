"""OpenTelemetry traces: no-op when off; spans for HTTP, provider requests, agent steps and tool calls when on."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

from book_agent.domain.enums import AgentItemKind, AgentTurnStatus, DocumentStatus, SourceType
from book_agent.domain.models import Document
from book_agent.harness.kernel.budget import TurnBudget
from book_agent.harness.kernel.model import AgentStep, ToolCall
from book_agent.harness.kernel.turn import AgentTurnRunner
from book_agent.harness.tools.permissions import PermissionPolicy
from book_agent.harness.tools.registry import ToolError, ToolPermission, ToolRegistry, ToolSpec
from book_agent.infra import tracing
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.agent import AgentLedgerRepository
from tests.test_harness_kernel import LookupArgs, _FakeModel, _usage
from tests.test_llm_client_transport import _OK_BODY, _client, _structured

try:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
except ImportError:  # pragma: no cover - the dev group installs the SDK
    InMemorySpanExporter = None

ROOT = Path(__file__).resolve().parents[1]


class DisabledTracingTests(unittest.TestCase):
    def test_helpers_are_no_ops_when_tracing_is_off(self) -> None:
        tracing.configure_tracing(False)
        self.assertFalse(tracing.enabled())
        with tracing.span("anything", a=1) as current:
            self.assertIsNone(current)
            tracing.annotate_current(b=2)
            tracing.record_error(RuntimeError("ignored"))


@unittest.skipIf(InMemorySpanExporter is None, "opentelemetry-sdk is not installed")
class EnabledTracingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.exporter = InMemorySpanExporter()
        self.assertTrue(tracing.configure_tracing(True, exporter=self.exporter))
        self.addCleanup(tracing.configure_tracing, False)

    def _spans(self, name_prefix: str):
        return [span for span in self.exporter.get_finished_spans() if span.name.startswith(name_prefix)]

    def test_http_requests_continue_the_callers_trace(self) -> None:
        from book_agent.app.main import create_app
        from book_agent.core.config import get_settings

        tempdir = tempfile.TemporaryDirectory(dir=ROOT / ".test-tmp")
        self.addCleanup(tempdir.cleanup)
        engine = build_engine(
            f"sqlite+pysqlite:///{Path(tempdir.name) / 't.db'}", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        patcher = patch.dict(os.environ, {"BOOK_AGENT_AUTH_MODE": "disabled", "BOOK_AGENT_OTEL_TRACES_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        get_settings.cache_clear()
        self.addCleanup(get_settings.cache_clear)
        app = create_app()
        # create_app configured tracing from settings (off); switch the in-memory exporter back on.
        tracing.configure_tracing(True, exporter=self.exporter)
        app.state.session_factory = build_session_factory(engine=engine)
        with TestClient(app) as client:
            trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
            response = client.get("/v1/health", headers={"traceparent": f"00-{trace_id}-00f067aa0ba902b7-01"})
        self.assertEqual(response.status_code, 200)
        (span,) = self._spans("GET /v1/health")
        self.assertEqual(format(span.context.trace_id, "032x"), trace_id)
        self.assertEqual(span.parent.span_id, 0x00F067AA0BA902B7)
        self.assertEqual(span.attributes["http.route"], "/v1/health")
        self.assertEqual(span.attributes["http.response.status_code"], 200)

    def test_provider_requests_record_model_retries_and_tokens(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(503, text="busy")
            return httpx.Response(200, text=_OK_BODY, headers={"content-type": "application/json"})

        client, _ = _client(handler)
        _structured(client)
        (span,) = self._spans("llm.request")
        self.assertEqual(span.attributes["gen_ai.request.model"], "m")
        self.assertEqual(span.attributes["server.address"], "provider.example")
        self.assertEqual(span.attributes["book_agent.retries"], 1)
        self.assertEqual(span.attributes["gen_ai.usage.input_tokens"], 100)
        self.assertEqual(span.attributes["gen_ai.usage.output_tokens"], 10)

    def test_agent_turns_emit_step_and_tool_spans(self) -> None:
        engine = build_engine("sqlite+pysqlite:///:memory:")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        session_factory = build_session_factory(engine=engine)
        with session_factory() as session:
            document = Document(source_type=SourceType.EPUB, file_fingerprint="trace-fp", title="Trace", status=DocumentStatus.ACTIVE)
            session.add(document)
            session.flush()
            ledger = AgentLedgerRepository(session)
            turn = ledger.create_turn(
                document_id=document.id,
                agent_kind="terminology",
                scope_type="document",
                scope_id=document.id,
                model_name="fake-model",
                budget=TurnBudget().to_json(),
            )
            ledger.append_item(turn.id, kind=AgentItemKind.USER, content={"text": "go"})
            session.commit()
            turn_id = turn.id

        def lookup(ctx, args):
            if args.term == "boom":
                raise ToolError("no such term")
            return {"term": args.term}

        model = _FakeModel(
            [
                AgentStep(
                    text=None,
                    tool_calls=[ToolCall("c1", "lookup_term", {"term": "agent"}), ToolCall("c2", "lookup_term", {"term": "boom"})],
                    usage=_usage(),
                ),
                AgentStep(text="done", usage=_usage()),
            ]
        )
        runner = AgentTurnRunner(
            session_factory=session_factory,
            model=model,
            registry=ToolRegistry([ToolSpec("lookup_term", "Look up.", LookupArgs, ToolPermission.READ, lookup)]),
            policy=PermissionPolicy(),
            compaction_threshold_tokens=None,
        )
        self.assertEqual(runner.run(turn_id).status, AgentTurnStatus.SUCCEEDED)

        steps = self._spans("agent.step")
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0].attributes["book_agent.tool_calls"], 2)
        self.assertEqual(steps[0].attributes["gen_ai.request.model"], "fake-model")
        tools = self._spans("agent.tool lookup_term")
        self.assertEqual([span.attributes["book_agent.tool_ok"] for span in tools], [True, False])
        self.assertEqual(tools[1].attributes["book_agent.tool_error"], "no such term")
        self.assertEqual(tools[0].attributes["book_agent.turn_id"], turn_id)

    def test_run_loop_ticks_are_spans_tagged_with_the_run(self) -> None:
        from tests.test_multi_instance import RunOwnershipTests

        fixture = RunOwnershipTests("test_stop_releases_owned_runs")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        run_id = fixture._running_run()
        executor = fixture._executor("host-a:1:aaaa")
        with patch.object(executor, "_enforce_budget_guardrails", return_value=True):
            executor._run_loop(run_id)
        (tick,) = self._spans("executor.tick")
        self.assertEqual(tick.attributes["book_agent.run_id"], run_id)


if __name__ == "__main__":
    unittest.main()
