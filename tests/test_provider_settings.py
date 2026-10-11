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

    def test_peak_hours_cost_the_multiplier_and_off_peak_hours_the_listed_price(self) -> None:
        from datetime import datetime, timezone

        from book_agent.workers.providers import openai_compatible
        from book_agent.workers.providers.pricing import price_multiplier_at

        # DeepSeek V4 Flash: $0.15 / $0.60 per 1M off-peak, twice that at peak; off-peak 16:30-00:30 UTC.
        created = self._create(
            input_cost_per_1m_tokens=0.15,
            input_cache_hit_cost_per_1m_tokens=0.003,
            output_cost_per_1m_tokens=0.6,
            peak_price_multiplier=2,
            off_peak_window_utc="16:30-00:30",
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual((created.json()["peak_price_multiplier"], created.json()["off_peak_window_utc"]), (2.0, "16:30-00:30"))
        with self.session_factory() as session:
            worker = build_worker_from_credential(
                session.get(ProviderCredential, created.json()["id"]), Settings(_env_file=None, translation_backend="echo")
            )

        def cost_at(hour: int, minute: int = 0) -> float:
            moment = datetime(2026, 9, 25, hour, minute, tzinfo=timezone.utc)
            with patch.object(openai_compatible, "datetime", type("Clock", (), {"now": staticmethod(lambda tz=None: moment)})):
                return worker.client._estimate_cost_usd(
                    token_in=1_000_000, token_out=1_000_000, prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=1_000_000
                )

        self.assertAlmostEqual(cost_at(17), 0.75, places=6)  # off-peak
        self.assertAlmostEqual(cost_at(0, 15), 0.75, places=6)  # the window wraps past midnight
        self.assertAlmostEqual(cost_at(0, 30), 1.5, places=6)  # peak from 00:30
        self.assertAlmostEqual(cost_at(8), 1.5, places=6)
        # Without a window every call costs the listed price.
        self.assertEqual(price_multiplier_at(datetime.now(timezone.utc), peak_multiplier=2, off_peak_window_utc=None), 1.0)

        bad = self.client.patch(f"/v1/providers/{created.json()['id']}", json={"off_peak_window_utc": "25:00-01:00"})
        self.assertEqual(bad.status_code, 422)
        cleared = self.client.patch(f"/v1/providers/{created.json()['id']}", json={"off_peak_window_utc": None})
        self.assertIsNone(cleared.json()["off_peak_window_utc"])
        self.assertEqual(cleared.json()["peak_price_multiplier"], 2.0)

    def test_the_peak_schedule_prices_weekday_windows_and_wins_over_the_window(self) -> None:
        """DeepSeek's pricing page (2026-10-11): peak Monday to Friday 01:00-04:00 and 06:00-10:00 UTC,
        every other hour and the weekend off-peak. The stored "16:30-00:30" window priced 10:00-16:30
        and weekends as peak."""
        from datetime import datetime, timezone

        from book_agent.workers.providers import openai_compatible

        created = self._create(
            input_cost_per_1m_tokens=0.15,
            output_cost_per_1m_tokens=0.6,
            peak_price_multiplier=2,
            off_peak_window_utc="16:30-00:30",
            peak_schedule_utc="mon-fri 01:00-04:00,06:00-10:00",
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["peak_schedule_utc"], "mon-fri 01:00-04:00,06:00-10:00")
        with self.session_factory() as session:
            worker = build_worker_from_credential(
                session.get(ProviderCredential, created.json()["id"]), Settings(_env_file=None, translation_backend="echo")
            )

        def cost_at(day: int, hour: int, minute: int = 0) -> float:
            moment = datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc)  # 2026-10-12 is a Monday
            with patch.object(openai_compatible, "datetime", type("Clock", (), {"now": staticmethod(lambda tz=None: moment)})):
                return worker.client._estimate_cost_usd(
                    token_in=1_000_000, token_out=1_000_000, prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=1_000_000
                )

        self.assertAlmostEqual(cost_at(12, 0, 59), 0.75, places=6)
        self.assertAlmostEqual(cost_at(12, 1), 1.5, places=6)
        self.assertAlmostEqual(cost_at(12, 4), 0.75, places=6)  # between the two windows
        self.assertAlmostEqual(cost_at(12, 9, 59), 1.5, places=6)
        self.assertAlmostEqual(cost_at(12, 12), 0.75, places=6)  # peak under the old window, off-peak now
        self.assertAlmostEqual(cost_at(17, 8), 0.75, places=6)  # Saturday
        self.assertAlmostEqual(cost_at(16, 8), 1.5, places=6)  # Friday

        for bad in ("weekdays 01:00-04:00", "mon-fri 04:00-01:00", "mon-fri", "mon-fri 25:00-26:00"):
            refused = self.client.patch(f"/v1/providers/{created.json()['id']}", json={"peak_schedule_utc": bad})
            self.assertEqual(refused.status_code, 422, bad)
        cleared = self.client.patch(f"/v1/providers/{created.json()['id']}", json={"peak_schedule_utc": None})
        self.assertIsNone(cleared.json()["peak_schedule_utc"])
        self.assertEqual(cleared.json()["off_peak_window_utc"], "16:30-00:30")

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


    def test_configuring_a_provider_resumes_runs_that_waited_for_one(self) -> None:
        from book_agent.domain.enums import DocumentRunStatus, DocumentRunType, DocumentStatus, SourceType
        from book_agent.domain.models import Document
        from book_agent.infra.repositories.run_control import RunControlRepository
        from book_agent.services.run_control import RunControlService

        woken: list[str | None] = []
        self.client.app.state.document_run_executor = type(
            "Executor", (), {"wake": lambda self, run_id=None: woken.append(run_id), "stop": lambda self: True}
        )()
        with self.session_factory() as session:
            run_ids = {}
            for reason in ("provider.not_configured", "user.paused"):
                document = Document(source_type=SourceType.WEB, file_fingerprint=reason, title=reason, status=DocumentStatus.ACTIVE)
                session.add(document)
                session.flush()
                control = RunControlService(RunControlRepository(session))
                run_id = control.create_run(document_id=document.id, run_type=DocumentRunType.TRANSLATE_FULL, requested_by="t").run_id
                run = RunControlRepository(session).get_run(run_id)
                run.status = DocumentRunStatus.PAUSED
                run.stop_reason = reason
                run_ids[reason] = run_id
            session.commit()

        created = self._create(activate=True)
        self.assertEqual(created.status_code, 201, created.text)
        with self.session_factory() as session:
            repository = RunControlRepository(session)
            self.assertEqual(repository.get_run(run_ids["provider.not_configured"]).status, DocumentRunStatus.RUNNING)
            self.assertEqual(repository.get_run(run_ids["user.paused"]).status, DocumentRunStatus.PAUSED)
        self.assertEqual(woken, [run_ids["provider.not_configured"]])

if __name__ == "__main__":
    unittest.main()
