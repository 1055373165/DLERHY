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
        self.assertEqual(localize_chapter_label("CHAPTER 11：额外技巧"), "第 11 章 额外技巧")
        self.assertEqual(localize_chapter_label("Chapter 3 - 计算方法"), "第 3 章 计算方法")
        self.assertEqual(localize_chapter_label("CHAPTER 7"), "第 7 章")
        # Untranslated headings keep their English label; Chinese ones take the one form.
        self.assertEqual(localize_chapter_label("CHAPTER 2: BIRTH OF RSI"), "CHAPTER 2: BIRTH OF RSI")
        self.assertEqual(localize_chapter_label("第1章：挑战"), "第 1 章 挑战")

    def test_numbered_titles_take_one_form(self) -> None:
        from book_agent.export.titles import normalize_numbered_title

        # Build an AI Agent: four forms of the same kind of title in one reading copy.
        cases = [
            ("1 什么是 AI 智能体？", "1 What is an AI agent?", "第 1 章 什么是 AI 智能体？"),
            ("第 3 章 使能行动：工具使用", "3 Enabling actions: Tool use", "第 3 章 使能行动：工具使用"),
            ("第 5 章\u3000使用 RAG 构建知识库", "5 Building knowledge bases with RAG", "第 5 章 使用 RAG 构建知识库"),
            ("6 为你的智能体添加记忆", "6 Adding memory to your agent", "第 6 章 为你的智能体添加记忆"),
            ("第十章 评估智能体", None, "第 10 章 评估智能体"),
            ("第二部分：进阶", None, "第 2 部分 进阶"),
            ("附录\nOpenAI API 密钥", "appendix OpenAI API Key", "附录 OpenAI API 密钥"),
            # Section numbers, untranslated titles and front matter keep their form.
            ("1.2 为什么需要智能体", "1.2 Why agents", "1.2 为什么需要智能体"),
            ("Part 1 Building your first LLM agent", "Part 1 Building your first LLM agent", "Part 1 Building your first LLM agent"),
            ("前言", "preface", "前言"),
        ]
        for title, source, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(normalize_numbered_title(title, source_title=source), expected)

    def test_numbered_chapters_whose_title_starts_with_a_digit_are_chapters(self) -> None:
        from book_agent.export.titles import extract_main_chapter_number, looks_like_frontmatter_title

        # The RSI book's Chapter 9 was folded into Chapter 8 because "20" is not a capital letter.
        self.assertEqual(extract_main_chapter_number("CHAPTER 9: 20 POPULAR RSI TRADING STRATEGIES"), 9)
        self.assertEqual(extract_main_chapter_number("Chapter 2: Birth of RSI"), 2)
        self.assertIsNone(extract_main_chapter_number("Chapter 3 of this book explains the formula"))
        # A Complete Guide To Volume Price Analysis spells its numbers out: read as no chapter, "Chapter One"
        # was folded into the "Foreward" before it in the reading copy.
        self.assertEqual(extract_main_chapter_number("Chapter One : There’s Nothing New In Trading"), 1)
        self.assertEqual(extract_main_chapter_number("CHAPTER TWELVE: Volume And Price"), 12)
        self.assertIsNone(extract_main_chapter_number("Chapter one of this book explains the formula"))
        # Manning outlines number a chapter without the word "Chapter"; the merged export folded the
        # RAG book's chapter 2 into chapter 1 until this was read as a chapter number.
        self.assertEqual(extract_main_chapter_number("2 RAG systems and their design"), 2)
        self.assertEqual(extract_main_chapter_number("10 Progression of RAG systems"), 10)
        self.assertIsNone(extract_main_chapter_number("1.2 Why RAG?"))
        self.assertIsNone(extract_main_chapter_number("3 ways to split a document"))
        # Its introduction carries a subtitle; it is still front matter.
        self.assertTrue(looks_like_frontmatter_title("INTRODUCTION: WHY RSI IS SO MAGICAL?"))
        self.assertTrue(looks_like_frontmatter_title("Preface"))
        self.assertFalse(looks_like_frontmatter_title("Introduction to Momentum Trading"))

    def test_part_pages_are_recognised(self) -> None:
        from book_agent.export.titles import looks_like_part_title

        self.assertTrue(looks_like_part_title("Part 1 Foundations"))
        self.assertTrue(looks_like_part_title("PART II"))
        self.assertFalse(looks_like_part_title("Partitioning a vector index"))

    def test_structural_chapter_names_have_chinese_labels(self) -> None:
        self.assertEqual(localized_structural_title_fallback("Front Matter"), "卷首")
        self.assertEqual(localized_structural_title_fallback("Introduction"), "引言")
        self.assertEqual(localized_structural_title_fallback("Dedication"), "献词")

    def test_a_title_with_emphasis_reads_without_asterisks(self) -> None:
        # The Illustrated Guide's "Praise for <em>An Illustrated Guide to AI Agents</em>".
        from types import SimpleNamespace

        from book_agent.export.titles import resolve_chapter_title

        document = SimpleNamespace(source_type=SourceType.EPUB)
        chapter = SimpleNamespace(
            title_src="Praise for *An Illustrated Guide to AI Agents*",
            title_tgt="赞誉：《AI 智能体图解指南》（*An Illustrated Guide to AI Agents*）",
        )
        self.assertEqual(
            resolve_chapter_title(document, chapter, []),
            "赞誉：《AI 智能体图解指南》（An Illustrated Guide to AI Agents）",
        )

    def test_the_title_heading_with_emphasis_is_not_repeated_in_the_body(self) -> None:
        from book_agent.export.models import MergedRenderBlock
        from book_agent.services.export import ExportService

        def block(block_id: str, block_type: str, target: str) -> MergedRenderBlock:
            return MergedRenderBlock(
                block_id=block_id, chapter_id="c", block_type=block_type, render_mode="zh_primary_with_optional_source",
                artifact_kind=None, title=None, source_text=target, target_text=target, source_metadata={},
                source_sentence_ids=[], target_segment_ids=[], is_expected_source_only=False, notice=None,
            )

        blocks = [
            block("h", "heading", "赞誉：《AI 智能体图解指南》（*An Illustrated Guide to AI Agents*）"),
            block("p", "paragraph", "两位作者是杰出的构建者。"),
        ]
        body = ExportService._merged_body_blocks(
            ExportService.__new__(ExportService), blocks, "赞誉：《AI 智能体图解指南》（An Illustrated Guide to AI Agents）"
        )
        self.assertEqual([item.block_id for item in body], ["p"])


if __name__ == "__main__":
    unittest.main()
