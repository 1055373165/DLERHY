"""What the first paid book run exposed: reasoning models that spend the output limit on reasoning,
billed calls lost with a rolled-back transaction, unbounded instant retries, and a terminology stage
that failed outright when extraction did."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
from sqlalchemy import select

from book_agent.domain.event_kinds import LLM_CALL_COMPLETED, LLM_CALL_FAILED
from book_agent.domain.enums import WorkItemStatus
from book_agent.domain.models import AgentItem, Event
from book_agent.harness.agents.terminology import TerminologyAgent
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories import run_control as run_control_repository
from book_agent.infra.repositories.events import emit_event
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.services import run_execution
from book_agent.translation.contracts import TranslationUsage
from book_agent.workers.failures import FailureDisposition, classify_failure
from book_agent.workers.llm_calls import observed_llm_call
from book_agent.workers.providers.openai_compatible import ProviderOutputTruncated, ProviderResponseFormatError
from tests.test_llm_client_transport import _client, _structured
import tests.test_terminology_agent as terminology_fixture


def _chat(content: str, *, finish: str, completion: int, reasoning: int) -> httpx.Response:
    body = {
        "id": "r1",
        "choices": [{"message": {"content": content, "reasoning_content": "thinking..."}, "finish_reason": finish}],
        "usage": {
            "prompt_tokens": 300,
            "completion_tokens": completion,
            "total_tokens": 300 + completion,
            "completion_tokens_details": {"reasoning_tokens": reasoning},
        },
    }
    return httpx.Response(200, text=json.dumps(body), headers={"content-type": "application/json"})


class ProviderOutputTests(unittest.TestCase):
    def test_all_reasoning_truncation_pauses_the_run_with_usage_attached(self) -> None:
        client, _ = _client(lambda request: _chat("", finish="length", completion=8192, reasoning=8192))
        with self.assertRaises(ProviderOutputTruncated) as caught:
            _structured(client)
        error = caught.exception
        self.assertTrue(error.reasoning_only)
        self.assertEqual((error.usage.token_in, error.usage.token_out), (300, 8192))
        self.assertIn("disable reasoning", str(error))
        classification = classify_failure(error)
        self.assertEqual(classification.disposition, FailureDisposition.PAUSE)
        self.assertEqual(classification.pause_reason, "provider.reasoning_exhausted_output")

    def test_partial_truncation_retries_and_bad_json_keeps_its_usage(self) -> None:
        client, _ = _client(lambda request: _chat('{"terms": [', finish="length", completion=900, reasoning=100))
        with self.assertRaises(ProviderOutputTruncated) as caught:
            _structured(client)
        self.assertFalse(caught.exception.reasoning_only)
        self.assertEqual(classify_failure(caught.exception).reason, "provider.output_truncated")

        client, _ = _client(lambda request: _chat("sorry, no JSON", finish="stop", completion=12, reasoning=0))
        with self.assertRaises(ProviderResponseFormatError) as caught:
            _structured(client)
        self.assertNotIsInstance(caught.exception, ProviderOutputTruncated)
        self.assertEqual(caught.exception.usage.token_out, 12)


class AccountingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def test_failed_calls_record_the_tokens_they_were_billed(self) -> None:
        with self.session_factory() as session:
            with self.assertRaises(ProviderResponseFormatError):
                with observed_llm_call(session, call_kind="glossary.extract", model="m"):
                    raise ProviderResponseFormatError("bad", usage=TranslationUsage(token_in=40, token_out=8192, total_tokens=8232))
            session.commit()
            (event,) = session.scalars(select(Event)).all()
            self.assertEqual((event.kind, event.payload["token_out"]), (LLM_CALL_FAILED, 8192))

    def test_model_call_events_survive_a_rollback_and_are_not_duplicated_on_commit(self) -> None:
        with self.session_factory() as session:
            emit_event(session, kind=LLM_CALL_COMPLETED, payload={"call_kind": "agent.terminology", "token_out": 7})
            session.rollback()
        with self.session_factory() as session:
            emit_event(session, kind=LLM_CALL_COMPLETED, payload={"call_kind": "translate", "token_out": 3})
            session.commit()
            session.rollback()  # nothing pending any more
        with self.session_factory() as session:
            events = session.scalars(select(Event).order_by(Event.id)).all()
            self.assertEqual([event.payload["call_kind"] for event in events], ["agent.terminology", "translate"])
            self.assertTrue(events[0].payload["recorded_after_rollback"])
            self.assertNotIn("recorded_after_rollback", events[1].payload)


class RetryPolicyTests(unittest.TestCase):
    def test_failed_attempts_back_off_but_reclaimed_leases_do_not(self) -> None:
        repository = RunControlRepository(session=None)
        now = datetime.now(timezone.utc)
        failed = SimpleNamespace(status=WorkItemStatus.RETRYABLE_FAILED, attempt=3, updated_at=now, error_detail_json={"retry_backoff": True})
        self.assertEqual(run_control_repository.retry_backoff_seconds(3), 8.0)
        self.assertFalse(repository._is_work_item_claimable(failed, now=now + timedelta(seconds=7)))
        self.assertTrue(repository._is_work_item_claimable(failed, now=now + timedelta(seconds=8)))
        reclaimed = SimpleNamespace(status=WorkItemStatus.RETRYABLE_FAILED, attempt=3, updated_at=now, error_detail_json={})
        self.assertTrue(repository._is_work_item_claimable(reclaimed, now=now))
        self.assertEqual(run_control_repository.retry_backoff_seconds(30), run_control_repository.RETRY_BACKOFF_MAX_SECONDS)

    def test_attempts_are_capped_when_the_budget_says_nothing(self) -> None:
        self.assertGreater(run_execution.DEFAULT_MAX_ATTEMPTS_PER_WORK_ITEM, 1)


class _FailingExtraction:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def generate_structured_object(self, **kwargs):
        raise self.error


class TerminologyDegradeTests(terminology_fixture.TerminologyAgentTests):
    """Runs against the terminology agent fixture; only the tests below are new."""

    def test_extraction_failure_leaves_a_turn_without_candidates(self) -> None:
        error = ProviderResponseFormatError("no JSON", usage=TranslationUsage(token_in=10, token_out=20, total_tokens=30))
        with self.session_factory() as session:
            seed = TerminologyAgent(session).start_turn(
                document_id=self.document_id, model_name="m", extraction_client=_FailingExtraction(error), mode="sampled"
            )
            session.commit()
            self.assertEqual(seed.candidate_count, 0)
            brief = [item.content_json.get("text", "") for item in session.scalars(select(AgentItem).where(AgentItem.turn_id == seed.turn_id))]
            self.assertTrue(any("extraction failed (ProviderResponseFormatError: no JSON)" in text for text in brief))
            (failed,) = session.scalars(select(Event).where(Event.kind == LLM_CALL_FAILED)).all()
            self.assertEqual(failed.payload["token_out"], 20)

    def test_a_misconfigured_provider_still_stops_the_stage(self) -> None:
        error = ProviderOutputTruncated("all reasoning", reasoning_only=True)
        with self.session_factory() as session:
            with self.assertRaises(ProviderOutputTruncated):
                TerminologyAgent(session).start_turn(
                    document_id=self.document_id, model_name="m", extraction_client=_FailingExtraction(error), mode="sampled"
                )


# The inherited fixture tests already run in tests/test_terminology_agent.py.
for _name in [name for name in dir(terminology_fixture.TerminologyAgentTests) if name.startswith("test_")]:
    setattr(TerminologyDegradeTests, _name, None)


if __name__ == "__main__":
    unittest.main()
