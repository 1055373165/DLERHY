"""Prepaid credit: top-ups, usage charged at the price multiplier, and runs stopping when it runs out."""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import update

from book_agent.app.main import create_app
from book_agent.core.config import get_settings
from book_agent.domain.models.auth import DEFAULT_ORG_ID
from book_agent.domain.models.ops import DocumentRun, Event
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.services import prepaid_credit
from book_agent.services.api_keys import ApiKeyService
from book_agent.services.run_control import RunControlService, RunControlTransitionError
from book_agent.services.run_execution import RunExecutionService
from tests.test_org_tenancy import _Db


class _Prepaid(_Db):
    def setUp(self) -> None:
        super().setUp()
        patcher = patch.dict(os.environ, {"BOOK_AGENT_BILLING_PRICE_MULTIPLIER": "1.5"})
        patcher.start()
        self.addCleanup(patcher.stop)
        get_settings.cache_clear()
        self.addCleanup(get_settings.cache_clear)
        prepaid_credit.invalidate_cache()
        self.addCleanup(prepaid_credit.invalidate_cache)

    def _status(self):
        with self.session_factory() as session:
            return prepaid_credit.credit_status(session, self.other_org_id, price_multiplier=1.5)

    def _credit(self, amount: float, **kwargs):
        with self.session_factory() as session:
            entry, created = prepaid_credit.add_credit(session, self.other_org_id, amount, **kwargs)
            session.commit()
            return created


class CreditLedgerTests(_Prepaid):
    def test_balance_is_credits_minus_usage_since_going_prepaid_at_the_multiplier(self) -> None:
        run_id = self._queued_run(self.other_org_id)
        self._spend(run_id, 100.0)  # before the first top-up: not charged
        with self.session_factory() as session:  # SQLite timestamps have one-second resolution
            session.execute(update(Event).values(occurred_at=datetime.now(timezone.utc) - timedelta(hours=1)))
            session.commit()
        self.assertFalse(self._status().prepaid)

        self.assertTrue(self._credit(10, reference="pay_1"))
        self.assertFalse(self._credit(10, reference="pay_1"))  # a retried webhook credits once
        self._spend(run_id, 4.0)
        status = self._status()
        self.assertEqual((status.prepaid, status.credited_usd, status.charged_usd, status.balance_usd), (True, 10.0, 6.0, 4.0))
        self.assertFalse(status.exhausted)

        self._credit(-1, kind="refund", note="partial refund")
        self.assertEqual(self._status().balance_usd, 3.0)
        with self.session_factory() as session, self.assertRaises(ValueError):
            prepaid_credit.add_credit(session, self.other_org_id, -5, kind="top_up")

    def test_an_empty_balance_refuses_resume_and_pauses_running_runs(self) -> None:
        self._credit(3)
        running = self._queued_run(self.other_org_id)
        queued = self._queued_run(self.other_org_id)
        with self.session_factory() as session:
            RunControlService(RunControlRepository(session)).resume_run(running, actor_id="t")
            session.commit()
        self._spend(running, 2.5)  # charged 3.75 > 3
        with self.session_factory() as session:
            repository = RunControlRepository(session)
            result = RunExecutionService(repository, RunControlService(repository)).enforce_budget_guardrails(run_id=running)
            session.commit()
        self.assertEqual(result.stop_reason, prepaid_credit.STOP_REASON)
        with self.session_factory() as session:
            run = session.get(DocumentRun, running)
            self.assertEqual((run.status.value, run.stop_reason), ("paused", prepaid_credit.STOP_REASON))
            with self.assertRaisesRegex(RunControlTransitionError, "预付余额已用完"):
                RunControlService(RunControlRepository(session)).resume_run(queued, actor_id="t")

        self._credit(5, reference="pay_2")
        with self.session_factory() as session:
            RunControlService(RunControlRepository(session)).resume_run(running, actor_id="t")
            session.commit()

    def test_prepaid_organisations_need_a_priced_provider(self) -> None:
        with self.session_factory() as session:
            prepaid_credit.ensure_priced_for_prepaid(session, self.other_org_id, prices_known=False)  # not prepaid yet
        self._credit(1)
        with self.session_factory() as session:
            prepaid_credit.ensure_priced_for_prepaid(session, self.other_org_id, prices_known=True)
            with self.assertRaisesRegex(prepaid_credit.UnpricedProviderForPrepaid, "单价"):
                prepaid_credit.ensure_priced_for_prepaid(session, self.other_org_id, prices_known=False)


class CreditRoutesTests(_Prepaid):
    def setUp(self) -> None:
        super().setUp()
        with self.session_factory() as session:
            service = ApiKeyService(session)
            self.keys = {
                "admin": service.create(org_id=DEFAULT_ORG_ID, name="a", role="admin").plaintext,
                "acme_admin": service.create(org_id=self.other_org_id, name="aa", role="admin").plaintext,
                "acme_editor": service.create(org_id=self.other_org_id, name="ae", role="editor").plaintext,
            }
            session.commit()
        patcher = patch.dict(os.environ, {"BOOK_AGENT_AUTH_MODE": "api_key", "BOOK_AGENT_RUN_EXECUTOR_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        get_settings.cache_clear()
        app = create_app()
        app.state.session_factory = self.session_factory
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def _h(self, who: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.keys[who]}"}

    def test_instance_admins_credit_and_members_read_their_balance(self) -> None:
        path = f"/v1/orgs/{self.other_org_id}/credit"
        body = {"amount_usd": 20, "reference": "pay_42", "note": "bank transfer"}
        self.assertEqual(self.client.post(path, json=body, headers=self._h("acme_admin")).status_code, 403)
        first = self.client.post(path, json=body, headers=self._h("admin"))
        self.assertEqual(first.status_code, 201, first.text)
        again = self.client.post(path, json=body, headers=self._h("admin"))
        self.assertEqual(again.status_code, 200)
        self.assertEqual(len(again.json()["entries"]), 1)

        run_id = self._queued_run(self.other_org_id)
        self._spend(run_id, 2.0)
        mine = self.client.get("/v1/orgs/current/credit", headers=self._h("acme_editor")).json()
        self.assertEqual((mine["prepaid"], mine["balance_usd"], mine["charged_usd"]), (True, 17.0, 3.0))
        usage = self.client.get("/v1/orgs/current/usage", headers=self._h("acme_editor")).json()
        self.assertEqual((usage["cost_usd"], usage["charged_usd"]), (2.0, 3.0))
        self.assertEqual(
            self.client.post(path, json={"amount_usd": -1}, headers=self._h("admin")).status_code, 422
        )


if __name__ == "__main__":
    unittest.main()
