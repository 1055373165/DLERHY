"""The free pilot: its model calls are ours, never charged to the organisation, and still counted as pilot cost."""

from __future__ import annotations

import unittest

from book_agent.domain.enums import DocumentRunType
from book_agent.domain.models.ops import DocumentRun
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.services import org_budget
from book_agent.services.billing_category import PILOT
from book_agent.services.run_control import RunControlService
from book_agent.services.run_execution import RunExecutionService
from book_agent.services.usage_statement import pilot_cost_statement, usage_statement
from tests import test_prepaid_credit
from tests.test_prepaid_credit import _Prepaid


class PilotBillingTests(_Prepaid):
    def _pilot_run(self) -> str:
        document_id = self._document(self.other_org_id)
        with self.session_factory() as session:
            run = RunControlService(RunControlRepository(session)).create_run(
                document_id=document_id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                requested_by="pilot-test",
                billing_category=PILOT,
            )
            session.commit()
            return run.run_id

    def test_pilot_calls_are_not_charged_and_are_reported_as_pilot_cost(self) -> None:
        self._credit(10, reference="pay_1")
        paid = self._queued_run(self.other_org_id)
        pilot = self._pilot_run()
        self._spend(paid, 2.0)
        self._spend(pilot, 3.0)

        status = self._status()
        self.assertEqual((status.charged_usd, status.balance_usd), (3.0, 7.0))  # 2.0 × 1.5, the pilot's 3.0 left out
        with self.session_factory() as session:
            self.assertEqual(org_budget.budget_status(session, self.other_org_id).spent_usd, 2.0)
            statement = usage_statement(session, self.other_org_id, price_multiplier=1.5)
            self.assertEqual((statement.cost_usd, statement.charged_usd, statement.pilot_cost_usd), (2.0, 3.0, 3.0))
            self.assertIn("pilot_cost_usd", statement.to_csv().splitlines()[0])
            ours = pilot_cost_statement(session)
        self.assertEqual((ours.pilot_count, ours.cost_usd), (1, 3.0))
        self.assertEqual(ours.pilots[0].org_id, self.other_org_id)

    def test_an_empty_balance_or_budget_does_not_stop_the_pilot(self) -> None:
        self._credit(1)
        paid = self._queued_run(self.other_org_id)
        self._spend(paid, 5.0)  # balance now negative
        with self.session_factory() as session:
            org_budget.set_monthly_budget(session, self.other_org_id, 1.0)
            session.commit()
        pilot = self._pilot_run()
        with self.session_factory() as session:
            repository = RunControlRepository(session)
            control = RunControlService(repository)
            control.resume_run(pilot, actor_id="t")
            session.commit()
            result = RunExecutionService(repository, control).enforce_budget_guardrails(run_id=pilot)
            session.commit()
            self.assertFalse(result.budget_exceeded)
            self.assertEqual(session.get(DocumentRun, pilot).status.value, "running")

    def test_a_retried_pilot_stays_a_pilot(self) -> None:
        pilot = self._pilot_run()
        with self.session_factory() as session:
            control = RunControlService(RunControlRepository(session))
            control.resume_run(pilot, actor_id="t")
            control.pause_run(pilot, actor_id="t")
            retried = control.retry_run(pilot, actor_id="t")
            session.commit()
            self.assertEqual(session.get(DocumentRun, retried.run_id).billing_category, PILOT)

    def test_unknown_billing_category_is_refused(self) -> None:
        document_id = self._document(self.other_org_id)
        with self.session_factory() as session, self.assertRaises(ValueError):
            RunControlService(RunControlRepository(session)).create_run(
                document_id=document_id, run_type=DocumentRunType.TRANSLATE_FULL, requested_by="t", billing_category="free"
            )


class PilotCostRouteTests(test_prepaid_credit.CreditRoutesTests):
    """Only instance admins see what pilots cost us."""

    # Run in tests/test_prepaid_credit.py already.
    test_instance_admins_credit_and_members_read_their_balance = None
    _pilot_run = PilotBillingTests._pilot_run

    def test_instance_admins_read_pilot_costs(self) -> None:
        self._spend(self._pilot_run(), 0.25)
        self.assertEqual(self.client.get("/v1/orgs/pilot-costs", headers=self._h("acme_admin")).status_code, 403)
        response = self.client.get("/v1/orgs/pilot-costs", headers=self._h("admin"))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((response.json()["pilot_count"], response.json()["cost_usd"]), (1, 0.25))


if __name__ == "__main__":
    unittest.main()
