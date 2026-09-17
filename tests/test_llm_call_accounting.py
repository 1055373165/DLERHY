"""Every model call outside packet translation leaves an ``llm.call.*`` event."""

from __future__ import annotations

import unittest
from typing import Any

from sqlalchemy import select

from book_agent.domain.enums import ProviderKind, ProviderTestStatus
from book_agent.domain.event_kinds import LLM_CALL_COMPLETED, LLM_CALL_FAILED
from book_agent.domain.models import Event
from book_agent.domain.models.provider_credential import ProviderCredential
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.services import provider_credentials as credentials
from book_agent.services.glossary_extraction import GlossaryExtractionService
from book_agent.translation.contracts import TranslationUsage
from book_agent.workers.llm_calls import (
    CALL_KIND_GLOSSARY_EXTRACT,
    CALL_KIND_PROVIDER_TEST,
    observed_llm_call,
    record_llm_usage,
)
from book_agent.workers.providers.openai_compatible import ProviderHTTPError


class _FakeClient:
    def __init__(self, payload: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.payload = payload or {"terms": []}
        self.error = error

    def generate_structured_object(self, *, model_name, system_prompt, user_prompt, response_schema, schema_name="x"):
        if self.error is not None:
            raise self.error
        return self.payload, TranslationUsage(token_in=40, token_out=8, total_tokens=48, cost_usd=0.001, latency_ms=12)


class LLMCallAccountingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        self.session = self.session_factory()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _events(self) -> list[Event]:
        return list(self.session.scalars(select(Event).order_by(Event.id)))

    def test_observed_call_records_usage_with_call_kind(self) -> None:
        with observed_llm_call(self.session, call_kind="terminology.survey", model="m", chapter_id="ch1") as call:
            call.complete(TranslationUsage(token_in=10, token_out=5, total_tokens=15, cost_usd=0.5, latency_ms=3))

        (event,) = self._events()
        self.assertEqual(event.kind, LLM_CALL_COMPLETED)
        self.assertEqual(event.chapter_id, "ch1")
        self.assertEqual(event.payload["call_kind"], "terminology.survey")
        self.assertEqual(event.payload["token_in"], 10)
        self.assertEqual(event.payload["cost_usd"], 0.5)
        self.assertEqual(event.actor_kind, "agent")

    def test_observed_call_records_failure_and_reraises(self) -> None:
        with self.assertRaises(ProviderHTTPError):
            with observed_llm_call(self.session, call_kind="concept.resolve", model="m"):
                raise ProviderHTTPError(429, "slow")

        (event,) = self._events()
        self.assertEqual(event.kind, LLM_CALL_FAILED)
        self.assertEqual(event.payload["call_kind"], "concept.resolve")
        self.assertEqual(event.payload["error_code"], "provider.http_429")
        self.assertEqual(event.payload["error_class"], "ProviderHTTPError")

    def test_record_llm_usage_accepts_duck_typed_usage(self) -> None:
        class Usage:
            token_in = 3
            token_out = 1

        record_llm_usage(self.session, call_kind="provider.test", model="m", usage=Usage())
        (event,) = self._events()
        self.assertEqual(event.payload["total_tokens"], 4)
        self.assertIsNone(event.payload["cost_usd"])

    def test_glossary_extraction_emits_one_event_per_chunk(self) -> None:
        from book_agent.domain.enums import (
            BlockType,
            ChapterStatus,
            DocumentStatus,
            ProtectedPolicy,
            SourceType,
        )
        from book_agent.domain.models import Block, Chapter, Document, Sentence

        from book_agent.core.ids import stable_id

        document_id = stable_id("test-document", "accounting")
        chapter_id = stable_id("test-chapter", "accounting")
        block_id = stable_id("test-block", "accounting")
        document = Document(
            id=document_id, source_type=SourceType.EPUB, file_fingerprint="fp", title="t", status=DocumentStatus.ACTIVE
        )
        chapter = Chapter(
            id=chapter_id, document_id=document_id, ordinal=1, title_src="One", status=ChapterStatus.TRANSLATED
        )
        block = Block(
            id=block_id,
            chapter_id=chapter_id,
            ordinal=1,
            block_type=BlockType.PARAGRAPH,
            source_text="Alpha beta.",
            protected_policy=ProtectedPolicy.TRANSLATE,
        )
        sentence = Sentence(
            id=stable_id("test-sentence", "accounting"),
            block_id=block_id,
            chapter_id=chapter_id,
            document_id=document_id,
            ordinal_in_block=1,
            source_text="Alpha beta.",
        )
        self.session.add_all([document, chapter, block, sentence])
        self.session.flush()

        GlossaryExtractionService(self.session, _FakeClient(), model_name="m").extract(document_id)

        events = [event for event in self._events() if event.kind == LLM_CALL_COMPLETED]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].payload["call_kind"], CALL_KIND_GLOSSARY_EXTRACT)
        self.assertEqual(events[0].chapter_id, chapter_id)
        self.assertEqual(events[0].payload["token_in"], 40)

    def test_provider_smoke_test_outcome_is_billed_when_recorded(self) -> None:
        record = ProviderCredential(
            id="p1",
            name="prov",
            provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            model_name="model-x",
            base_url="https://provider.example/v1",
            streaming=False,
            max_output_tokens=64,
            timeout_seconds=10,
            max_retries=0,
            retry_backoff_seconds_x10=10,
            is_active=True,
        )
        self.session.add(record)
        self.session.flush()

        ok = credentials.TestOutcome(
            status=ProviderTestStatus.OK,
            message="connection ok",
            elapsed_ms=5,
            sample_output="你好",
            usage=TranslationUsage(token_in=7, token_out=2, total_tokens=9),
            model_name="model-x",
        )
        credentials.record_test_outcome(self.session, "p1", ok)
        failed = credentials.TestOutcome(
            status=ProviderTestStatus.FAILED,
            message="HTTP 401",
            elapsed_ms=5,
            sample_output=None,
            error=ProviderHTTPError(401, "nope"),
            model_name="model-x",
        )
        credentials.record_test_outcome(self.session, "p1", failed)

        events = self._events()
        self.assertEqual([event.kind for event in events], [LLM_CALL_COMPLETED, LLM_CALL_FAILED])
        self.assertEqual({event.payload["call_kind"] for event in events}, {CALL_KIND_PROVIDER_TEST})
        self.assertEqual(events[0].payload["model"], "model-x")
        self.assertEqual(events[1].payload["error_code"], "provider.http_401")
        self.assertEqual(record.last_test_status, ProviderTestStatus.FAILED)


if __name__ == "__main__":
    unittest.main()
