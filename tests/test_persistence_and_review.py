import tempfile
import unittest
import zipfile
from pathlib import Path
import sys

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.core.ids import stable_id
from book_agent.domain.enums import ActionType, ExportType, JobScopeType, LockLevel, MemoryScopeType, SnapshotType, TermStatus, TermType
from book_agent.domain.enums import PacketStatus, PacketType
from book_agent.domain.models import ArtifactInvalidation, Block, Chapter, ChapterQualitySummary, Export, MemorySnapshot, Sentence, TermEntry
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.infra.repositories.export import ExportRepository
from book_agent.infra.repositories.ops import OpsRepository
from book_agent.infra.repositories.review import ReviewRepository
from book_agent.infra.repositories.translation import TranslationRepository
from book_agent.orchestrator.bootstrap import BootstrapOrchestrator
from book_agent.domain.models.translation import AlignmentEdge, TargetSegment, TranslationPacket, TranslationRun
from book_agent.services.actions import IssueActionExecutor
from book_agent.services.chapter_concept_lock import ChapterConceptLockService
from book_agent.services.export import ExportGateError, ExportService
from book_agent.services.review import ReviewService
from book_agent.services.translation import TranslationService
from book_agent.services.workflows import DocumentWorkflowService
from book_agent.workers.contracts import AlignmentSuggestion, TranslationTargetSegment, TranslationWorkerOutput
from book_agent.workers.translator import TranslationTask, TranslationWorkerMetadata


CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml" />
  </rootfiles>
</container>
"""

CONTENT_OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package version="3.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Business Strategy Handbook</dc:title>
    <dc:creator>Test Author</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav" />
    <item id="chap1" href="chapter1.xhtml" media-type="application/xhtml+xml" />
  </manifest>
  <spine>
    <itemref idref="chap1" />
  </spine>
</package>
"""

NAV_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <body>
    <nav epub:type="toc">
      <ol>
        <li><a href="chapter1.xhtml">Chapter One</a></li>
      </ol>
    </nav>
  </body>
</html>
"""

CHAPTER_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>Pricing power matters. Strategy compounds.</p>
    <blockquote>A quoted paragraph.</blockquote>
  </body>
</html>
"""

IMAGE_ONLY_FIGURE_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <div class="figure-container">
      <img src="images/cover.png" alt="cover art" />
    </div>
  </body>
</html>
"""

CONTEXT_ENGINEERING_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>Context engineering determines how context is created.</p>
    <p>Context engineering also determines how context is maintained.</p>
  </body>
</html>
"""

LITERALISM_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>This broader challenge is what some are beginning to call context engineering, which is the deliberate design of how context is created, maintained, and applied to shape reasoning.</p>
    <p>In essence, the weight of evidence shows relying on external content supplied at inference time from up-to-date, relevant sources tends to yield more reliable and contextually accurate outputs.</p>
  </body>
</html>
"""

STALE_BRIEF_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>A recipe book offers instructions for many meals.</p>
    <p>A chef adapts when your pantry is missing ingredients.</p>
    <p>Memory helps agents act consistently over time.</p>
    <p>Context engineering determines how context is created.</p>
    <p>Context engineering also determines how context is maintained.</p>
  </body>
</html>
"""

AGENTIC_AI_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>Agentic AI continuously improves by ingesting feedback.</p>
    <p>Agentic AI also adapts over time.</p>
  </body>
</html>
"""


class DuplicateWorker:
    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(
            worker_name="DuplicateWorker",
            model_name="duplicate-test",
            prompt_version="test.duplication.v1",
        )

    def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
        target_segments = []
        alignments = []
        for sentence in task.current_sentences:
            temp_id = f"temp-{sentence.id}"
            if any(token in sentence.source_text for token in ["Pricing power", "Strategy compounds"]):
                text = "完全重复的译文片段。"
            else:
                text = f"译文::{sentence.source_text}"
            target_segments.append(
                TranslationTargetSegment(
                    temp_id=temp_id,
                    text_zh=text,
                    segment_type="sentence",
                    source_sentence_ids=[sentence.id],
                    confidence=0.95,
                )
            )
            alignments.append(
                AlignmentSuggestion(
                    source_sentence_ids=[sentence.id],
                    target_temp_ids=[temp_id],
                    relation_type="1:1",
                    confidence=0.95,
                )
            )
        return TranslationWorkerOutput(
            packet_id=task.context_packet.packet_id,
            target_segments=target_segments,
            alignment_suggestions=alignments,
        )


class SplitSegmentWorker:
    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(
            worker_name="SplitSegmentWorker",
            model_name="split-segment-test",
            prompt_version="test.split-segment.v1",
        )

    def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
        target_segments = []
        alignments = []
        for sentence in task.current_sentences:
            if "Pricing power" in sentence.source_text:
                temp_intro = f"temp-{sentence.id}-a"
                temp_tail = f"temp-{sentence.id}-b"
                target_segments.extend(
                    [
                        TranslationTargetSegment(
                            temp_id=temp_intro,
                            text_zh="定价权很重要。",
                            segment_type="sentence",
                            source_sentence_ids=[sentence.id],
                            confidence=0.95,
                        ),
                        TranslationTargetSegment(
                            temp_id=temp_tail,
                            text_zh="它会持续复利。",
                            segment_type="sentence",
                            source_sentence_ids=[sentence.id],
                            confidence=0.95,
                        ),
                    ]
                )
                alignments.append(
                    AlignmentSuggestion(
                        source_sentence_ids=[sentence.id],
                        target_temp_ids=[temp_intro, temp_tail],
                        relation_type="1:n",
                        confidence=0.95,
                    )
                )
                continue

            temp_id = f"temp-{sentence.id}"
            target_segments.append(
                TranslationTargetSegment(
                    temp_id=temp_id,
                    text_zh=f"译文::{sentence.source_text}",
                    segment_type="sentence",
                    source_sentence_ids=[sentence.id],
                    confidence=0.95,
                )
            )
            alignments.append(
                AlignmentSuggestion(
                    source_sentence_ids=[sentence.id],
                    target_temp_ids=[temp_id],
                    relation_type="1:1",
                    confidence=0.95,
                )
            )

        return TranslationWorkerOutput(
            packet_id=task.context_packet.packet_id,
            target_segments=target_segments,
            alignment_suggestions=alignments,
        )


class ParagraphFlowWorker:
    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(
            worker_name="ParagraphFlowWorker",
            model_name="paragraph-flow-test",
            prompt_version="test.paragraph-flow.v1",
        )

    def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
        mapping = {
            "Chapter One": "第一章",
            "Pricing power matters.": "定价能力很重要。",
            "Strategy compounds.": "战略会持续复利。",
            "A quoted paragraph.": "这是一段引用。",
        }
        target_segments = []
        alignments = []
        for sentence in task.current_sentences:
            temp_id = f"temp-{sentence.id}"
            target_segments.append(
                TranslationTargetSegment(
                    temp_id=temp_id,
                    text_zh=mapping.get(sentence.source_text, f"译文::{sentence.source_text}"),
                    segment_type="sentence",
                    source_sentence_ids=[sentence.id],
                    confidence=0.95,
                )
            )
            alignments.append(
                AlignmentSuggestion(
                    source_sentence_ids=[sentence.id],
                    target_temp_ids=[temp_id],
                    relation_type="1:1",
                    confidence=0.95,
                )
            )
        return TranslationWorkerOutput(
            packet_id=task.context_packet.packet_id,
            target_segments=target_segments,
            alignment_suggestions=alignments,
        )


class AgenticLiteralWorker:
    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(
            worker_name="AgenticLiteralWorker",
            model_name="agentic-literal-test",
            prompt_version="test.agentic-literal.v1",
        )

    def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
        target_segments = []
        alignments = []
        for sentence in task.current_sentences:
            temp_id = f"temp-{sentence.id}"
            if "Agentic AI" in sentence.source_text:
                text = "智能体AI通过吸收反馈持续改进。"
            else:
                text = f"译文::{sentence.source_text}"
            target_segments.append(
                TranslationTargetSegment(
                    temp_id=temp_id,
                    text_zh=text,
                    segment_type="sentence",
                    source_sentence_ids=[sentence.id],
                    confidence=0.95,
                )
            )
            alignments.append(
                AlignmentSuggestion(
                    source_sentence_ids=[sentence.id],
                    target_temp_ids=[temp_id],
                    relation_type="1:1",
                    confidence=0.95,
                )
            )
        return TranslationWorkerOutput(
            packet_id=task.context_packet.packet_id,
            target_segments=target_segments,
            alignment_suggestions=alignments,
        )


class LiteralismWorker:
    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(
            worker_name="LiteralismWorker",
            model_name="literalism-test",
            prompt_version="test.literalism.v1",
        )

    def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
        target_segments = []
        alignments = []
        for sentence in task.current_sentences:
            if "context engineering" in sentence.source_text:
                text = (
                    "这一更广泛的挑战正被一些人称为情境工程，即对情境如何被创建、维护并应用于塑造推理过程进行有意识的设计。"
                )
            elif "weight of evidence" in sentence.source_text:
                text = (
                    "本质上，证据权重表明，在推理时依赖来自最新相关来源的外部内容，往往能产生更可靠且上下文更准确的输出。"
                )
            else:
                text = f"译文::{sentence.source_text}"
            temp_id = f"temp-{sentence.id}"
            target_segments.append(
                TranslationTargetSegment(
                    temp_id=temp_id,
                    text_zh=text,
                    segment_type="sentence",
                    source_sentence_ids=[sentence.id],
                    confidence=0.95,
                )
            )
            alignments.append(
                AlignmentSuggestion(
                    source_sentence_ids=[sentence.id],
                    target_temp_ids=[temp_id],
                    relation_type="1:1",
                    confidence=0.95,
                )
            )
        return TranslationWorkerOutput(
            packet_id=task.context_packet.packet_id,
            target_segments=target_segments,
            alignment_suggestions=alignments,
        )


class PersistenceAndReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _bootstrap_to_db(self) -> tuple[str, str]:
        with tempfile.TemporaryDirectory() as tmpdir:
            epub_path = Path(tmpdir) / "sample.epub"
            with zipfile.ZipFile(epub_path, "w") as archive:
                archive.writestr("mimetype", "application/epub+zip")
                archive.writestr("META-INF/container.xml", CONTAINER_XML)
                archive.writestr("OEBPS/content.opf", CONTENT_OPF)
                archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
                archive.writestr("OEBPS/chapter1.xhtml", CHAPTER_XHTML)

            artifacts = BootstrapOrchestrator().bootstrap_epub(epub_path)

        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()
        return artifacts.document.id, artifacts.translation_packets[0].id

    def _bootstrap_custom_epub_to_db(
        self,
        chapters: list[tuple[str, str, str]],
        *,
        extra_files: dict[str, bytes] | None = None,
    ) -> str:
        manifest_items = ['    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav" />']
        spine_items: list[str] = []
        toc_items: list[str] = []
        for index, (title, href, _content) in enumerate(chapters, start=1):
            item_id = f"chap{index}"
            manifest_items.append(f'    <item id="{item_id}" href="{href}" media-type="application/xhtml+xml" />')
            spine_items.append(f'    <itemref idref="{item_id}" />')
            toc_items.append(f'        <li><a href="{href}">{title}</a></li>')

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
        nav_xhtml = "\n".join(
            [
                '<?xml version="1.0" encoding="UTF-8"?>',
                '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">',
                "  <body>",
                '    <nav epub:type="toc">',
                "      <ol>",
                *toc_items,
                "      </ol>",
                "    </nav>",
                "  </body>",
                "</html>",
            ]
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            epub_path = Path(tmpdir) / "sample.epub"
            with zipfile.ZipFile(epub_path, "w") as archive:
                archive.writestr("mimetype", "application/epub+zip")
                archive.writestr("META-INF/container.xml", CONTAINER_XML)
                archive.writestr("OEBPS/content.opf", content_opf)
                archive.writestr("OEBPS/nav.xhtml", nav_xhtml)
                for _title, href, content in chapters:
                    archive.writestr(f"OEBPS/{href}", content)
                for relative_path, payload in (extra_files or {}).items():
                    archive.writestr(relative_path, payload)
            artifacts = BootstrapOrchestrator().bootstrap_epub(epub_path)

        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()
        return artifacts.document.id

    def test_bootstrap_persists_to_sqlite(self) -> None:
        document_id, _ = self._bootstrap_to_db()
        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            self.assertEqual(bundle.document.id, document_id)
            self.assertEqual(len(bundle.chapters), 1)
            self.assertEqual(len(bundle.chapters[0].blocks), 3)
            self.assertEqual(len(bundle.chapters[0].sentences), 4)
            self.assertEqual(len(bundle.chapters[0].translation_packets), 3)

    def test_translation_and_review_generate_issue_and_action(self) -> None:
        document_id, packet_id = self._bootstrap_to_db()

        with self.session_factory() as session:
            translation_service = TranslationService(TranslationRepository(session))
            translation_service.execute_packet(packet_id)

            document_bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = document_bundle.chapters[0].chapter.id
            sentence_id = next(
                sentence.id
                for sentence in document_bundle.chapters[0].sentences
                if "Pricing power" in sentence.source_text
            )
            session.add(
                TermEntry(
                    id="00000000-0000-0000-0000-000000000001",
                    document_id=document_id,
                    scope_type=MemoryScopeType.GLOBAL,
                    scope_id=None,
                    source_term="pricing power",
                    target_term="定价权",
                    term_type=TermType.CONCEPT,
                    lock_level=LockLevel.LOCKED,
                    status=TermStatus.ACTIVE,
                    evidence_sentence_id=sentence_id,
                    version=1,
                )
            )
            session.commit()

        with self.session_factory() as session:
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            self.assertGreaterEqual(len(review_artifacts.issues), 1)
            self.assertTrue(any(issue.issue_type == "TERM_CONFLICT" for issue in review_artifacts.issues))
            self.assertTrue(any(action.action_type == ActionType.UPDATE_TERMBASE_THEN_RERUN_TARGETED for action in review_artifacts.actions))
            self.assertTrue(any(action.scope_type == JobScopeType.CHAPTER for action in review_artifacts.actions))
            persisted_summary = session.query(ChapterQualitySummary).filter_by(chapter_id=chapter_id).one()
            self.assertGreaterEqual(persisted_summary.issue_count, 1)
            self.assertEqual(persisted_summary.action_count, len(review_artifacts.actions))
            self.assertFalse(persisted_summary.term_ok)

    def test_action_execution_and_export(self) -> None:
        document_id, packet_id = self._bootstrap_to_db()

        with self.session_factory() as session:
            TranslationService(TranslationRepository(session)).execute_packet(packet_id)
            document_bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = document_bundle.chapters[0].chapter.id
            sentence_id = next(
                sentence.id
                for sentence in document_bundle.chapters[0].sentences
                if "Pricing power" in sentence.source_text
            )
            session.add(
                TermEntry(
                    id="00000000-0000-0000-0000-000000000002",
                    document_id=document_id,
                    scope_type=MemoryScopeType.GLOBAL,
                    scope_id=None,
                    source_term="pricing power",
                    target_term="定价权",
                    term_type=TermType.CONCEPT,
                    lock_level=LockLevel.LOCKED,
                    status=TermStatus.ACTIVE,
                    evidence_sentence_id=sentence_id,
                    version=1,
                )
            )
            session.commit()

        with tempfile.TemporaryDirectory() as outdir:
            with self.session_factory() as session:
                chapter_id = BootstrapRepository(session).load_document_bundle(document_id).chapters[0].chapter.id
                review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
                action_id = review_artifacts.actions[0].id
                execution = IssueActionExecutor(OpsRepository(session)).execute(action_id)
                review_export = ExportService(ExportRepository(session), output_root=outdir).export_review_package(chapter_id)
                session.commit()

                self.assertGreaterEqual(len(execution.invalidations), 1)
                self.assertTrue(review_export.file_path.exists())
                with self.assertRaises(ExportGateError):
                    ExportService(ExportRepository(session), output_root=outdir).export_bilingual_html(chapter_id)
                self.assertGreaterEqual(session.query(ArtifactInvalidation).count(), 1)
                self.assertGreaterEqual(session.query(Export).count(), 1)

    def test_review_detects_packet_context_failure_and_routes_to_packet_rebuild(self) -> None:
        document_id, packet_id = self._bootstrap_to_db()

        with self.session_factory() as session:
            TranslationService(TranslationRepository(session)).execute_packet(packet_id)
            packet = session.get(TranslationPacket, packet_id)
            assert packet is not None
            packet_json = dict(packet.packet_json)
            packet_json["open_questions"] = ["speaker_reference_ambiguous"]
            packet.packet_json = packet_json
            session.merge(packet)
            session.commit()

        with self.session_factory() as session:
            chapter_id = BootstrapRepository(session).load_document_bundle(document_id).chapters[0].chapter.id
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            context_issue = next(issue for issue in review_artifacts.issues if issue.issue_type == "CONTEXT_FAILURE")
            context_action = next(action for action in review_artifacts.actions if action.issue_id == context_issue.id)

            self.assertEqual(context_issue.root_cause_layer.value, "packet")
            self.assertEqual(context_action.action_type, ActionType.REBUILD_PACKET_THEN_RERUN)
            self.assertEqual(context_action.scope_type, JobScopeType.PACKET)
            self.assertEqual(context_action.scope_id, packet_id)

    def test_review_reports_unlocked_key_concept_from_chapter_memory(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", CONTEXT_ENGINEERING_XHTML)]
        )

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            packet_ids = [packet.id for packet in bundle.chapters[0].translation_packets]
            service = TranslationService(TranslationRepository(session))
            for packet_id in packet_ids:
                service.execute_packet(packet_id)
            session.commit()

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            concept_issue = next(
                issue for issue in review_artifacts.issues if issue.issue_type == "UNLOCKED_KEY_CONCEPT"
            )
            concept_action = next(
                action for action in review_artifacts.actions if action.issue_id == concept_issue.id
            )

            self.assertEqual(concept_issue.root_cause_layer.value, "memory")
            self.assertFalse(concept_issue.blocking)
            self.assertEqual(concept_action.action_type, ActionType.UPDATE_TERMBASE_THEN_RERUN_TARGETED)
            self.assertEqual(concept_action.scope_type, JobScopeType.CHAPTER)
            self.assertEqual(concept_action.scope_id, chapter_id)
            self.assertIn("context engineering", concept_issue.evidence_json["source_term"].lower())

    def test_review_skips_unlocked_key_concept_when_locked_term_entry_exists(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", CONTEXT_ENGINEERING_XHTML)]
        )

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter = bundle.chapters[0].chapter
            packet_ids = [packet.id for packet in bundle.chapters[0].translation_packets]
            service = TranslationService(TranslationRepository(session))
            for packet_id in packet_ids:
                service.execute_packet(packet_id)
            session.flush()
            sentence_id = next(
                sentence.id
                for sentence in bundle.chapters[0].sentences
                if "Context engineering" in sentence.source_text
            )
            session.add(
                TermEntry(
                    id=stable_id("term-entry", document_id, chapter.id, "context engineering", 1),
                    document_id=document_id,
                    scope_type=MemoryScopeType.CHAPTER,
                    scope_id=chapter.id,
                    source_term="context engineering",
                    target_term="上下文工程",
                    term_type=TermType.CONCEPT,
                    lock_level=LockLevel.LOCKED,
                    status=TermStatus.ACTIVE,
                    evidence_sentence_id=sentence_id,
                    version=1,
                )
            )
            session.commit()

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            unlocked_issues = [
                issue for issue in review_artifacts.issues if issue.issue_type == "UNLOCKED_KEY_CONCEPT"
            ]
            self.assertEqual(unlocked_issues, [])

    def test_review_reports_style_drift_literalism_as_non_blocking_advisory(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", LITERALISM_XHTML)]
        )

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            packet_ids = [packet.id for packet in bundle.chapters[0].translation_packets]
            service = TranslationService(TranslationRepository(session), worker=LiteralismWorker())
            for packet_id in packet_ids:
                service.execute_packet(packet_id)
            session.commit()

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            style_issues = [issue for issue in review_artifacts.issues if issue.issue_type == "STYLE_DRIFT"]
            self.assertGreaterEqual(len(style_issues), 3)
            self.assertTrue(all(issue.root_cause_layer.value == "packet" for issue in style_issues))
            self.assertTrue(all(not issue.blocking for issue in style_issues))
            style_actions = [
                action
                for action in review_artifacts.actions
                if action.issue_id in {issue.id for issue in style_issues}
            ]
            self.assertTrue(style_actions)
            self.assertTrue(all(action.action_type == ActionType.RERUN_PACKET for action in style_actions))
            self.assertTrue(all(action.scope_type == JobScopeType.PACKET for action in style_actions))
            self.assertTrue(
                any(issue.evidence_json.get("preferred_hint") == "上下文工程" for issue in style_issues)
            )
            self.assertTrue(
                any("证据权重" in str(issue.evidence_json.get("actual_target_text") or "") for issue in style_issues)
            )
            self.assertTrue(
                any(issue.evidence_json.get("preferred_hint") == "更符合上下文的输出" for issue in style_issues)
            )

    def test_review_reports_term_conflict_for_locked_chapter_concept_entry(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", AGENTIC_AI_XHTML)]
        )

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            packet_ids = [packet.id for packet in bundle.chapters[0].translation_packets]
            service = TranslationService(TranslationRepository(session), worker=AgenticLiteralWorker())
            for packet_id in packet_ids:
                service.execute_packet(packet_id)
            ChapterConceptLockService(session).lock_concept(
                chapter_id=chapter_id,
                source_term="agentic AI",
                canonical_zh="智能体式AI",
            )
            session.commit()

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            term_issues = [issue for issue in review_artifacts.issues if issue.issue_type == "TERM_CONFLICT"]
            self.assertTrue(term_issues)
            self.assertTrue(
                any(issue.evidence_json.get("expected_target_term") == "智能体式AI" for issue in term_issues)
            )
            self.assertTrue(
                any("智能体AI" in str(issue.evidence_json.get("actual_target_text") or "") for issue in term_issues)
            )
            unlocked_issues = [
                issue for issue in review_artifacts.issues if issue.issue_type == "UNLOCKED_KEY_CONCEPT"
            ]
            self.assertEqual(unlocked_issues, [])

    def test_review_reports_stale_chapter_brief_when_late_concept_is_missing(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", STALE_BRIEF_XHTML)]
        )

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            packet_ids = [packet.id for packet in bundle.chapters[0].translation_packets]
            service = TranslationService(TranslationRepository(session))
            for packet_id in packet_ids:
                service.execute_packet(packet_id)
            session.commit()

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            stale_issue = next(
                issue for issue in review_artifacts.issues if issue.issue_type == "STALE_CHAPTER_BRIEF"
            )
            stale_action = next(
                action for action in review_artifacts.actions if action.issue_id == stale_issue.id
            )

            self.assertEqual(stale_issue.root_cause_layer.value, "memory")
            self.assertFalse(stale_issue.blocking)
            self.assertEqual(stale_action.action_type, ActionType.REBUILD_CHAPTER_BRIEF)
            self.assertEqual(stale_action.scope_type, JobScopeType.CHAPTER)
            self.assertEqual(stale_action.scope_id, chapter_id)
            self.assertIn("context engineering", ",".join(stale_issue.evidence_json["missing_concepts"]).lower())

    def test_review_skips_image_only_cover_packet_missing_title_context_failure(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [
                ("Cover", "cover.xhtml", IMAGE_ONLY_FIGURE_XHTML),
                ("Chapter One", "chapter1.xhtml", CHAPTER_XHTML),
            ],
            extra_files={"OEBPS/images/cover.png": b"fake-cover"},
        )

        with self.session_factory() as session:
            cover_chapter = session.scalars(
                select(Chapter)
                .where(Chapter.document_id == document_id)
                .order_by(Chapter.ordinal)
            ).first()
            self.assertIsNotNone(cover_chapter)
            assert cover_chapter is not None
            cover_chapter.title_src = None

            cover_sentence = session.scalars(
                select(Sentence).where(Sentence.chapter_id == cover_chapter.id)
            ).one()

            chapter_brief = session.scalars(
                select(MemorySnapshot).where(
                    MemorySnapshot.document_id == document_id,
                    MemorySnapshot.scope_type == MemoryScopeType.CHAPTER,
                    MemorySnapshot.scope_id == cover_chapter.id,
                    MemorySnapshot.snapshot_type == SnapshotType.CHAPTER_BRIEF,
                )
            ).one()
            brief_json = dict(chapter_brief.content_json)
            brief_json["open_questions"] = ["missing_chapter_title"]
            chapter_brief.content_json = brief_json
            session.commit()

            cover_block = session.scalars(
                select(Block).where(Block.chapter_id == cover_chapter.id).order_by(Block.ordinal)
            ).first()
            self.assertIsNotNone(cover_block)
            assert cover_block is not None

            cover_packet = TranslationPacket(
                id=stable_id("packet", cover_chapter.id, "legacy-cover"),
                chapter_id=cover_chapter.id,
                block_start_id=cover_block.id,
                block_end_id=cover_block.id,
                packet_type=PacketType.TRANSLATE,
                book_profile_version=1,
                chapter_brief_version=1,
                termbase_version=1,
                entity_snapshot_version=1,
                style_snapshot_version=1,
                packet_json={
                    "current_blocks": [
                        {
                            "sentence_ids": [cover_sentence.id],
                        }
                    ],
                    "open_questions": ["missing_chapter_title"],
                },
                status=PacketStatus.BUILT,
            )
            session.add(cover_packet)
            session.commit()

            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(cover_chapter.id)
            self.assertEqual(review_artifacts.issues, [])
            with tempfile.TemporaryDirectory() as outdir:
                ExportService(ExportRepository(session), output_root=outdir).export_bilingual_html(cover_chapter.id)

    def test_final_export_auto_followup_can_rebuild_packet_context_failure(self) -> None:
        with tempfile.TemporaryDirectory() as outdir:
            with tempfile.TemporaryDirectory() as tmpdir:
                epub_path = Path(tmpdir) / "sample.epub"
                with zipfile.ZipFile(epub_path, "w") as archive:
                    archive.writestr("mimetype", "application/epub+zip")
                    archive.writestr("META-INF/container.xml", CONTAINER_XML)
                    archive.writestr("OEBPS/content.opf", CONTENT_OPF)
                    archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
                    archive.writestr("OEBPS/chapter1.xhtml", CHAPTER_XHTML)

                with self.session_factory() as session:
                    workflow = DocumentWorkflowService(session, export_root=outdir)
                    summary = workflow.bootstrap_epub(epub_path)
                    document_id = summary.document_id
                    workflow.translate_document(document_id)
                    review = workflow.review_document(document_id)
                    self.assertEqual(review.total_issue_count, 0)

                    packet = session.scalars(
                        select(TranslationPacket).where(TranslationPacket.chapter_id.is_not(None))
                    ).first()
                    self.assertIsNotNone(packet)
                    assert packet is not None
                    packet_json = dict(packet.packet_json)
                    packet_json["open_questions"] = ["speaker_reference_ambiguous"]
                    packet.packet_json = packet_json
                    session.merge(packet)
                    session.flush()

                    review = workflow.review_document(document_id)
                    self.assertGreaterEqual(review.total_issue_count, 1)

                    export = workflow.export_document(
                        document_id,
                        ExportType.BILINGUAL_HTML,
                        auto_execute_followup_on_gate=True,
                    )
                    self.assertTrue(export.auto_followup_requested)
                    self.assertTrue(export.auto_followup_applied)
                    self.assertEqual(export.auto_followup_executions[0].action_type, "REBUILD_PACKET_THEN_RERUN")
                    self.assertTrue(export.auto_followup_executions[0].issue_resolved)
                    self.assertTrue(Path(export.chapter_results[0].file_path).exists())

    def test_bilingual_html_keeps_multi_sentence_paragraph_in_single_flow(self) -> None:
        with tempfile.TemporaryDirectory() as outdir:
            with tempfile.TemporaryDirectory() as tmpdir:
                epub_path = Path(tmpdir) / "sample.epub"
                with zipfile.ZipFile(epub_path, "w") as archive:
                    archive.writestr("mimetype", "application/epub+zip")
                    archive.writestr("META-INF/container.xml", CONTAINER_XML)
                    archive.writestr("OEBPS/content.opf", CONTENT_OPF)
                    archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
                    archive.writestr("OEBPS/chapter1.xhtml", CHAPTER_XHTML)

                with self.session_factory() as session:
                    workflow = DocumentWorkflowService(
                        session,
                        export_root=outdir,
                        translation_worker=ParagraphFlowWorker(),
                    )
                    summary = workflow.bootstrap_epub(epub_path)
                    document_id = summary.document_id
                    workflow.translate_document(document_id)
                    workflow.review_document(document_id)
                    export = workflow.export_document(document_id, ExportType.BILINGUAL_HTML)
                    chapter_html = Path(export.chapter_results[0].file_path).read_text(encoding="utf-8")
                    self.assertIn("定价能力很重要。战略会持续复利。", chapter_html)
                    self.assertNotIn("定价能力很重要。<br/>战略会持续复利。", chapter_html)

    def test_review_detects_chapter_brief_context_failure_and_routes_to_brief_rebuild(self) -> None:
        document_id, packet_id = self._bootstrap_to_db()

        with self.session_factory() as session:
            TranslationService(TranslationRepository(session)).execute_packet(packet_id)
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            chapter_brief = session.scalars(
                session.query(MemorySnapshot).filter(
                    MemorySnapshot.document_id == document_id,
                    MemorySnapshot.scope_type == MemoryScopeType.CHAPTER,
                    MemorySnapshot.scope_id == chapter_id,
                    MemorySnapshot.snapshot_type == SnapshotType.CHAPTER_BRIEF,
                ).statement
            ).first()
            assert chapter_brief is not None
            content_json = dict(chapter_brief.content_json)
            content_json["open_questions"] = ["entity_state_missing"]
            chapter_brief.content_json = content_json
            session.merge(chapter_brief)
            session.commit()

        with self.session_factory() as session:
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            context_issue = next(issue for issue in review_artifacts.issues if issue.issue_type == "CONTEXT_FAILURE")
            context_action = next(action for action in review_artifacts.actions if action.issue_id == context_issue.id)

            self.assertEqual(context_issue.root_cause_layer.value, "memory")
            self.assertEqual(context_action.action_type, ActionType.REBUILD_CHAPTER_BRIEF)
            self.assertEqual(context_action.scope_type, JobScopeType.CHAPTER)
            self.assertEqual(context_action.scope_id, chapter_id)

    def test_review_detects_duplication_and_routes_to_packet_rebuild(self) -> None:
        document_id, _ = self._bootstrap_to_db()

        with self.session_factory() as session:
            document_bundle = BootstrapRepository(session).load_document_bundle(document_id)
            packet_id = next(
                packet.id
                for packet in document_bundle.chapters[0].translation_packets
                if any(len(block.get("sentence_ids", [])) >= 2 for block in packet.packet_json.get("current_blocks", []))
            )

        with self.session_factory() as session:
            TranslationService(TranslationRepository(session), worker=DuplicateWorker()).execute_packet(packet_id)
            chapter_id = BootstrapRepository(session).load_document_bundle(document_id).chapters[0].chapter.id
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            duplication_issue = next(issue for issue in review_artifacts.issues if issue.issue_type == "DUPLICATION")
            duplication_action = next(action for action in review_artifacts.actions if action.issue_id == duplication_issue.id)

            self.assertEqual(duplication_issue.root_cause_layer.value, "packet")
            self.assertEqual(duplication_action.action_type, ActionType.REBUILD_PACKET_THEN_RERUN)
            self.assertEqual(duplication_action.scope_type, JobScopeType.PACKET)
            self.assertEqual(duplication_action.scope_id, packet_id)

    def test_review_detects_recoverable_alignment_failure_and_routes_to_realign(self) -> None:
        document_id, _ = self._bootstrap_to_db()

        with self.session_factory() as session:
            document_bundle = BootstrapRepository(session).load_document_bundle(document_id)
            packet_id = next(
                packet.id
                for packet in document_bundle.chapters[0].translation_packets
                if any(len(block.get("sentence_ids", [])) >= 2 for block in packet.packet_json.get("current_blocks", []))
            )

        with self.session_factory() as session:
            TranslationService(TranslationRepository(session)).execute_packet(packet_id)
            latest_run_id = session.scalars(
                select(TranslationRun.id)
                .where(TranslationRun.packet_id == packet_id)
                .order_by(TranslationRun.attempt.desc())
            ).first()
            target_segment_id = session.scalars(
                select(TargetSegment.id)
                .where(TargetSegment.translation_run_id == latest_run_id)
                .order_by(TargetSegment.ordinal)
            ).first()
            session.execute(delete(AlignmentEdge).where(AlignmentEdge.target_segment_id == target_segment_id))
            session.commit()

        with self.session_factory() as session:
            chapter_id = BootstrapRepository(session).load_document_bundle(document_id).chapters[0].chapter.id
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            alignment_issue = next(issue for issue in review_artifacts.issues if issue.issue_type == "ALIGNMENT_FAILURE")
            alignment_action = next(action for action in review_artifacts.actions if action.issue_id == alignment_issue.id)

            self.assertEqual(alignment_issue.root_cause_layer.value, "alignment")
            self.assertEqual(alignment_action.action_type, ActionType.REALIGN_ONLY)
            self.assertEqual(alignment_action.scope_type, JobScopeType.PACKET)
            self.assertEqual(alignment_action.scope_id, packet_id)

    def test_review_detects_orphan_target_segment_and_routes_to_realign(self) -> None:
        document_id, _ = self._bootstrap_to_db()

        with self.session_factory() as session:
            document_bundle = BootstrapRepository(session).load_document_bundle(document_id)
            packet_id = next(
                packet.id
                for packet in document_bundle.chapters[0].translation_packets
                if any(len(block.get("sentence_ids", [])) >= 2 for block in packet.packet_json.get("current_blocks", []))
            )

        with self.session_factory() as session:
            TranslationService(TranslationRepository(session), worker=SplitSegmentWorker()).execute_packet(packet_id)
            latest_run_id = session.scalars(
                select(TranslationRun.id)
                .where(TranslationRun.packet_id == packet_id)
                .order_by(TranslationRun.attempt.desc())
            ).first()
            orphan_target_id = session.scalars(
                select(TargetSegment.id)
                .where(TargetSegment.translation_run_id == latest_run_id)
                .order_by(TargetSegment.ordinal.desc())
            ).first()
            session.execute(delete(AlignmentEdge).where(AlignmentEdge.target_segment_id == orphan_target_id))
            session.commit()

        with self.session_factory() as session:
            chapter_id = BootstrapRepository(session).load_document_bundle(document_id).chapters[0].chapter.id
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            alignment_issue = next(issue for issue in review_artifacts.issues if issue.issue_type == "ALIGNMENT_FAILURE")
            alignment_action = next(action for action in review_artifacts.actions if action.issue_id == alignment_issue.id)

            self.assertIn(orphan_target_id, alignment_issue.evidence_json["orphan_target_segment_ids"])
            self.assertEqual(alignment_action.action_type, ActionType.REALIGN_ONLY)
            self.assertEqual(alignment_action.scope_type, JobScopeType.PACKET)
            self.assertEqual(alignment_action.scope_id, packet_id)


if __name__ == "__main__":
    unittest.main()
