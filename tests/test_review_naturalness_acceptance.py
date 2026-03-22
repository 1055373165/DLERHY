# ruff: noqa: E402

import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.enums import ChapterStatus
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.infra.repositories.review import ReviewRepository
from book_agent.infra.repositories.translation import TranslationRepository
from book_agent.orchestrator.bootstrap import BootstrapOrchestrator
from book_agent.services.chapter_concept_lock import ChapterConceptLockService
from book_agent.services.review import ReviewService
from book_agent.services.translation import TranslationService
from book_agent.services.workflows import DocumentWorkflowService
from tests.test_persistence_and_review import (
    CONTAINER_XML,
    CONSISTENCY_CARE_LITERALISM_XHTML,
    DURABLE_SUBSTRATE_LITERALISM_XHTML,
    GuidanceAwareLiteralismWorker,
    GuidanceAwareMixedWorker,
    KNOWLEDGE_TIMELINE_LITERALISM_XHTML,
    LITERALISM_XHTML,
    LiteralismWorker,
    MIXED_AUTO_FOLLOWUP_XHTML,
    NAV_XHTML,
    RESPONSIBILITY_LITERALISM_XHTML,
)


class ReviewNaturalnessAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _bootstrap_custom_epub_to_db(
        self,
        chapters: list[tuple[str, str, str]],
    ) -> str:
        manifest_items = ['    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav" />']
        spine_items: list[str] = []
        for index, (_title, href, _content) in enumerate(chapters, start=1):
            item_id = f"chap{index}"
            manifest_items.append(f'    <item id="{item_id}" href="{href}" media-type="application/xhtml+xml" />')
            spine_items.append(f'    <itemref idref="{item_id}" />')

        content_opf = "\n".join(
            [
                '<?xml version="1.0" encoding="UTF-8"?>',
                '<package version="3.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId">',
                '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">',
                '    <dc:title>Business Strategy Handbook</dc:title>',
                '    <dc:creator>Test Author</dc:creator>',
                '    <dc:language>en</dc:language>',
                "  </metadata>",
                "  <manifest>",
                *manifest_items,
                "  </manifest>",
                "  <spine>",
                *spine_items,
                "  </spine>",
                "</package>",
            ]
        )

        tmpdir = Path(tempfile.mkdtemp(prefix="book-agent-naturalness-"))
        self.addCleanup(lambda: shutil.rmtree(tmpdir, ignore_errors=True))
        epub_path = tmpdir / "sample.epub"
        with zipfile.ZipFile(epub_path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", content_opf)
            archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
            for _title, href, content in chapters:
                archive.writestr(f"OEBPS/{href}", content)

        artifacts = BootstrapOrchestrator().bootstrap_epub(epub_path)
        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()
        return artifacts.document.id

    def test_locked_benchmark_families_emit_expected_naturalness_signals(self) -> None:
        benchmarks = [
            (LITERALISM_XHTML, "context_engineering_literal", "上下文工程"),
            (
                KNOWLEDGE_TIMELINE_LITERALISM_XHTML,
                "knowledge_timeline_literal",
                "已知内容、知晓这些内容的时间点，以及其对行动的重要性",
            ),
            (
                DURABLE_SUBSTRATE_LITERALISM_XHTML,
                "durable_substrate_literal",
                "使上下文得以持久存在的基础",
            ),
            (
                RESPONSIBILITY_LITERALISM_XHTML,
                "profound_responsibility_literal",
                "强烈的责任感 / 很强的责任意识",
            ),
            (
                CONSISTENCY_CARE_LITERALISM_XHTML,
                "consistency_care_service_literal",
                "长期稳定、周到地照应 / 始终如一地细心照应",
            ),
        ]

        for source_xhtml, expected_rule, expected_hint in benchmarks:
            document_id = self._bootstrap_custom_epub_to_db([("Chapter One", "chapter1.xhtml", source_xhtml)])

            with self.session_factory() as session:
                bundle = BootstrapRepository(session).load_document_bundle(document_id)
                chapter_id = bundle.chapters[0].chapter.id
                packet_ids = [packet.id for packet in bundle.chapters[0].translation_packets]
                service = TranslationService(TranslationRepository(session), worker=LiteralismWorker())
                for packet_id in packet_ids:
                    service.execute_packet(packet_id)
                session.commit()

            with self.session_factory() as session:
                chapter_id = BootstrapRepository(session).load_document_bundle(document_id).chapters[0].chapter.id
                review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
                naturalness = review_artifacts.summary.naturalness_summary

                self.assertIsNotNone(naturalness)
                assert naturalness is not None
                self.assertTrue(naturalness.advisory_only)
                self.assertGreaterEqual(naturalness.style_drift_issue_count, 1)
                self.assertGreaterEqual(naturalness.affected_packet_count, 1)
                self.assertIn(expected_rule, naturalness.dominant_style_rules)
                self.assertIn(expected_hint, naturalness.preferred_hints)

    def test_guided_followup_clears_literalism_benchmark_under_locked_contract(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db([("Chapter One", "chapter1.xhtml", LITERALISM_XHTML)])

        with self.session_factory() as session:
            workflow = DocumentWorkflowService(
                session,
                translation_worker=GuidanceAwareLiteralismWorker(),
            )
            workflow.translate_document(document_id)
            review = workflow.review_document(
                document_id,
                auto_execute_packet_followups=True,
                max_auto_followup_attempts=2,
            )
            session.commit()

            self.assertTrue(review.auto_followup_requested)
            self.assertTrue(review.auto_followup_applied)
            self.assertEqual(review.total_issue_count, 0)
            self.assertEqual(len(review.chapter_results), 1)
            self.assertEqual(review.chapter_results[0].status, ChapterStatus.QA_CHECKED.value)
            self.assertIsNone(review.chapter_results[0].naturalness_summary)

    def test_mixed_benchmark_keeps_term_priority_before_style_followup(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", MIXED_AUTO_FOLLOWUP_XHTML)]
        )

        with self.session_factory() as session:
            workflow = DocumentWorkflowService(
                session,
                translation_worker=GuidanceAwareMixedWorker(),
            )
            workflow.translate_document(document_id)
            session.commit()

            bundle = workflow.bootstrap_repository.load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            ChapterConceptLockService(session).lock_concept(
                chapter_id=chapter_id,
                source_term="agentic AI",
                canonical_zh="代理式AI",
            )

            review = workflow.review_document(
                document_id,
                auto_execute_packet_followups=True,
                max_auto_followup_attempts=1,
            )
            session.commit()

            self.assertTrue(review.auto_followup_applied)
            self.assertEqual(len(review.auto_followup_executions or []), 1)
            self.assertEqual(review.auto_followup_executions[0].issue_type, "TERM_CONFLICT")

            final_review = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            remaining_term_issues = [
                issue for issue in final_review.issues if issue.issue_type == "TERM_CONFLICT"
            ]
            remaining_style_issues = [
                issue for issue in final_review.issues if issue.issue_type == "STYLE_DRIFT"
            ]
            self.assertEqual(remaining_term_issues, [])
            self.assertTrue(remaining_style_issues)
            self.assertIsNotNone(final_review.summary.naturalness_summary)
            assert final_review.summary.naturalness_summary is not None
            self.assertGreaterEqual(final_review.summary.naturalness_summary.style_drift_issue_count, 1)


if __name__ == "__main__":
    unittest.main()
