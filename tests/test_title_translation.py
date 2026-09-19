"""Book titles and chapter labels in Chinese exports."""

from __future__ import annotations

import unittest

from sqlalchemy import select

from book_agent.domain.enums import DocumentStatus, SourceType
from book_agent.domain.event_kinds import LLM_CALL_COMPLETED
from book_agent.domain.models import Document, Event
from book_agent.export.titles import localize_chapter_label, localized_structural_title_fallback
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.services.glossary_service import GlossaryService
from book_agent.services.title_translation import TitleTranslationService
from book_agent.translation.contracts import TranslationUsage


class _Client:
    def __init__(self, reply=None, error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.prompts: list[str] = []

    def generate_structured_object(self, *, model_name, system_prompt, user_prompt, response_schema, schema_name):
        self.prompts.append(user_prompt)
        if self.error is not None:
            raise self.error
        return self.reply, TranslationUsage(token_in=40, token_out=12, total_tokens=52)


class TitleTranslationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.PDF_TEXT,
                file_fingerprint="title-fp",
                title="Dear Traders, There is Magic in RSI",
                title_src="Dear Traders, There is Magic in RSI",
                status=DocumentStatus.ACTIVE,
            )
            session.add(document)
            session.commit()
            self.document_id = document.id

    def _translate(self, client):
        with self.session_factory() as session:
            title = TitleTranslationService(session, client, model_name="m").ensure_document_title(self.document_id)
            session.commit()
            return title, session.get(Document, self.document_id)

    def test_translates_once_with_the_glossary_and_records_the_call(self) -> None:
        with self.session_factory() as session:
            GlossaryService(session).lock_term(self.document_id, "RSI", "RSI")
            session.commit()
        client = _Client({"title": "亲爱的交易者，RSI 里藏着魔力"})
        title, document = self._translate(client)
        self.assertEqual(title, "亲爱的交易者，RSI 里藏着魔力")
        self.assertEqual(document.title_tgt, title)
        self.assertEqual(document.metadata_json["document_title_tgt_resolution_source"], "model_title_translation")
        self.assertIn("RSI => RSI", client.prompts[0])
        again, _ = self._translate(_Client(error=AssertionError("must not be called")))
        self.assertEqual(again, title)
        with self.session_factory() as session:
            (event,) = session.scalars(select(Event).where(Event.kind == LLM_CALL_COMPLETED)).all()
            self.assertEqual(event.payload["call_kind"], "title.translate")

    def test_unusable_answers_and_failures_leave_the_title_alone(self) -> None:
        for client in (_Client({"title": "Dear Traders"}), _Client({}), _Client(error=RuntimeError("provider down"))):
            title, document = self._translate(client)
            self.assertIsNone(title)
            self.assertIsNone(document.title_tgt)
        self.assertIsNone(self._translate(None)[0])


class ChapterLabelTests(unittest.TestCase):
    def test_english_chapter_labels_in_translated_headings_become_chinese(self) -> None:
        self.assertEqual(localize_chapter_label("CHAPTER 11：额外技巧"), "第11章：额外技巧")
        self.assertEqual(localize_chapter_label("Chapter 3 - 计算方法"), "第3章：计算方法")
        self.assertEqual(localize_chapter_label("CHAPTER 7"), "第7章")
        # Untranslated headings keep their English label; Chinese ones are untouched.
        self.assertEqual(localize_chapter_label("CHAPTER 2: BIRTH OF RSI"), "CHAPTER 2: BIRTH OF RSI")
        self.assertEqual(localize_chapter_label("第1章：挑战"), "第1章：挑战")

    def test_structural_chapter_names_have_chinese_labels(self) -> None:
        self.assertEqual(localized_structural_title_fallback("Front Matter"), "卷首")
        self.assertEqual(localized_structural_title_fallback("Introduction"), "引言")
        self.assertEqual(localized_structural_title_fallback("Dedication"), "献词")


if __name__ == "__main__":
    unittest.main()
