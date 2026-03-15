import os
import json
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
import sys

from sqlalchemy import delete, func, select

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
os.environ.setdefault("BOOK_AGENT_TRANSLATION_BACKEND", "echo")
os.environ.setdefault("BOOK_AGENT_TRANSLATION_MODEL", "echo-worker")
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.core.config import get_settings
from book_agent.domain.enums import (
    ActionActorType,
    ActionStatus,
    ActionType,
    ActorType,
    Detector,
    DocumentRunType,
    ExportStatus,
    ExportType,
    IssueStatus,
    JobScopeType,
    LockLevel,
    MemoryScopeType,
    RootCauseLayer,
    Severity,
    SnapshotType,
    TermStatus,
    TermType,
    WorkItemScopeType,
    WorkItemStage,
    WorkItemStatus,
    WorkerLeaseStatus,
)
from book_agent.domain.models import Chapter, MemorySnapshot, Sentence, TermEntry
from book_agent.domain.models.ops import RunAuditEvent, WorkItem, WorkerLease
from book_agent.domain.models.review import Export, IssueAction, ReviewIssue
from book_agent.domain.models.translation import AlignmentEdge, TargetSegment, TranslationPacket, TranslationRun
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.infra.db.session import build_session_factory, session_scope
from book_agent.services.export import ExportGateError
from book_agent.services.run_control import RunBudgetSummary, RunControlService
from book_agent.services.run_execution import RunExecutionService
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


def _content_opf(title: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<package version="3.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{title}</dc:title>
    <dc:creator>Integration Test</dc:creator>
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


def _chapter_xhtml(marker: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>Pricing power matters in {marker}. Strategy compounds.</p>
    <blockquote>A quoted paragraph.</blockquote>
  </body>
</html>
"""

EMPTY_CHAPTER_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <section id="frontmatter">
      <img src="cover.png" alt="Cover" />
    </section>
  </body>
</html>
"""

LITERAL_TAG_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>Tokens starting with <think>. Keep the token literal.</p>
  </body>
</html>
"""

CODE_CHAPTER_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>Use the example carefully.</p>
    <pre>python agent.py --dry-run</pre>
  </body>
</html>
"""

STRUCTURED_ARTIFACT_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:m="http://www.w3.org/1998/Math/MathML">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <figure id="fig-1">
      <img src="images/agent-loop.png" alt="Agent loop architecture" />
      <figcaption>Figure 1.1 Agent loop architecture</figcaption>
    </figure>
    <table id="tbl-1">
      <tr><th>Tier</th><th>Latency</th></tr>
      <tr><td>Basic</td><td>Slow</td></tr>
    </table>
    <m:math id="eq-1"><m:mi>x</m:mi><m:mo>=</m:mo><m:mn>1</m:mn></m:math>
    <p>https://example.com/agent-docs</p>
  </body>
</html>
"""


def _content_opf_with_chapters(title: str, chapters: list[tuple[str, str]]) -> str:
    manifest_items = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav" />',
        *[
            f'<item id="chap{index}" href="{href}" media-type="application/xhtml+xml" />'
            for index, (_chapter_title, href) in enumerate(chapters, start=1)
        ],
    ]
    spine_items = [
        f'<itemref idref="chap{index}" />'
        for index, _chapter in enumerate(chapters, start=1)
    ]
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<package version="3.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{title}</dc:title>
    <dc:creator>Integration Test</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    {' '.join(manifest_items)}
  </manifest>
  <spine>
    {' '.join(spine_items)}
  </spine>
</package>
"""


def _nav_xhtml_with_chapters(chapters: list[tuple[str, str]]) -> str:
    nav_items = "\n".join(
        f'        <li><a href="{href}">{title}</a></li>'
        for title, href in chapters
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <body>
    <nav epub:type="toc">
      <ol>
{nav_items}
      </ol>
    </nav>
  </body>
</html>
"""


class PacketAwareTermWorker:
    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(
            worker_name="PacketAwareTermWorker",
            model_name="packet-aware-integration",
            prompt_version="test.packet-aware.integration.v1",
            runtime_config={"mode": "integration"},
        )

    def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
        relevant_terms = {
            term.source_term.lower(): term.target_term
            for term in task.context_packet.relevant_terms
        }
        target_segments = []
        alignments = []
        for sentence in task.current_sentences:
            if "Pricing power" in sentence.source_text:
                target_term = relevant_terms.get("pricing power", "定价能力")
                text = f"{target_term}很重要。"
            else:
                text = f"译文::{sentence.source_text}"
            temp_id = f"temp-{sentence.id}"
            target_segments.append(
                TranslationTargetSegment(
                    temp_id=temp_id,
                    text_zh=text,
                    segment_type="sentence",
                    source_sentence_ids=[sentence.id],
                    confidence=0.99,
                )
            )
            alignments.append(
                AlignmentSuggestion(
                    source_sentence_ids=[sentence.id],
                    target_temp_ids=[temp_id],
                    relation_type="1:1",
                    confidence=0.99,
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
            model_name="split-segment-integration",
            prompt_version="test.split-segment.integration.v1",
            runtime_config={"mode": "integration"},
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
                            confidence=0.99,
                        ),
                        TranslationTargetSegment(
                            temp_id=temp_tail,
                            text_zh="它会持续复利。",
                            segment_type="sentence",
                            source_sentence_ids=[sentence.id],
                            confidence=0.99,
                        ),
                    ]
                )
                alignments.append(
                    AlignmentSuggestion(
                        source_sentence_ids=[sentence.id],
                        target_temp_ids=[temp_intro, temp_tail],
                        relation_type="1:n",
                        confidence=0.99,
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
                    confidence=0.99,
                )
            )
            alignments.append(
                AlignmentSuggestion(
                    source_sentence_ids=[sentence.id],
                    target_temp_ids=[temp_id],
                    relation_type="1:1",
                    confidence=0.99,
                )
            )

        return TranslationWorkerOutput(
            packet_id=task.context_packet.packet_id,
            target_segments=target_segments,
            alignment_suggestions=alignments,
        )


class LiteralTagWorker:
    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(
            worker_name="LiteralTagWorker",
            model_name="literal-tag-integration",
            prompt_version="test.literal-tag.integration.v1",
            runtime_config={"mode": "integration"},
        )

    def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
        target_segments = []
        alignments = []
        for sentence in task.current_sentences:
            if "<think>" in sentence.source_text:
                text = "以<think>开头的标记要保留字面形式。"
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
                    confidence=0.99,
                )
            )
        return TranslationWorkerOutput(
            packet_id=task.context_packet.packet_id,
            target_segments=target_segments,
            alignment_suggestions=alignments,
        )


@unittest.skipUnless(
    os.getenv("BOOK_AGENT_RUN_PG_TESTS") == "1",
    "Set BOOK_AGENT_RUN_PG_TESTS=1 to run PostgreSQL integration tests.",
)
class PostgresWorkflowIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.settings = get_settings()
        if not cls.settings.database_url.startswith("postgresql"):
            raise unittest.SkipTest("PostgreSQL integration tests require a PostgreSQL database URL.")
        cls.session_factory = build_session_factory(database_url=cls.settings.database_url)

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.export_root = Path(self.tempdir.name) / "exports"
        self.export_root.mkdir(parents=True, exist_ok=True)

    def _write_epub(self, label: str) -> Path:
        epub_path = Path(self.tempdir.name) / f"{label}.epub"
        title = f"Smoke {label} {uuid4()}"
        with zipfile.ZipFile(epub_path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", _content_opf(title))
            archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
            archive.writestr("OEBPS/chapter1.xhtml", _chapter_xhtml(title))
        return epub_path

    def _write_epub_with_chapters(self, label: str, chapters: list[tuple[str, str, str]]) -> Path:
        epub_path = Path(self.tempdir.name) / f"{label}.epub"
        title = f"Smoke {label} {uuid4()}"
        toc_entries = [(chapter_title, href) for chapter_title, href, _content in chapters]
        with zipfile.ZipFile(epub_path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", _content_opf_with_chapters(title, toc_entries))
            archive.writestr("OEBPS/nav.xhtml", _nav_xhtml_with_chapters(toc_entries))
            for _chapter_title, href, content in chapters:
                archive.writestr(f"OEBPS/{href}", content)
        return epub_path

    def test_postgres_document_workflow_happy_path(self) -> None:
        epub_path = self._write_epub("happy-path")

        with session_scope(self.session_factory) as session:
            service = DocumentWorkflowService(session, export_root=self.export_root)
            summary = service.bootstrap_epub(epub_path)
            translate = service.translate_document(summary.document_id)
            review = service.review_document(summary.document_id)
            export = service.export_document(summary.document_id, ExportType.BILINGUAL_HTML)

            self.assertEqual(summary.chapter_count, 1)
            self.assertEqual(translate.translated_packet_count, 3)
            self.assertEqual(review.total_issue_count, 0)
            self.assertEqual(export.document_status, "exported")
            self.assertTrue(Path(export.chapter_results[0].file_path).exists())

    def test_postgres_export_succeeds_with_empty_frontmatter_chapter(self) -> None:
        epub_path = self._write_epub_with_chapters(
            "empty-frontmatter",
            [
                ("Welcome", "welcome.xhtml", EMPTY_CHAPTER_XHTML),
                ("Chapter One", "chapter1.xhtml", _chapter_xhtml("empty-frontmatter")),
            ],
        )

        with session_scope(self.session_factory) as session:
            service = DocumentWorkflowService(session, export_root=self.export_root)
            summary = service.bootstrap_epub(epub_path)
            document_id = summary.document_id

            self.assertEqual(summary.chapter_count, 2)
            self.assertTrue(any(chapter.packet_count == 0 for chapter in summary.chapters))

            translate = service.translate_document(document_id)
            self.assertEqual(translate.translated_packet_count, 3)

            review = service.review_document(document_id)
            self.assertEqual(review.total_issue_count, 0)
            self.assertEqual(len(review.chapter_results), 2)

            review_export = service.export_document(document_id, ExportType.REVIEW_PACKAGE)
            self.assertEqual(len(review_export.chapter_results), 2)

            bilingual_export = service.export_document(document_id, ExportType.BILINGUAL_HTML)
            self.assertEqual(bilingual_export.document_status, "exported")
            self.assertEqual(len(bilingual_export.chapter_results), 2)

            refreshed = service.get_document_summary(document_id)
            frontmatter = next(chapter for chapter in refreshed.chapters if chapter.packet_count == 0)
            self.assertEqual(frontmatter.sentence_count, 0)
            self.assertEqual(frontmatter.status, "exported")
            assert frontmatter.quality_summary is not None
            self.assertTrue(frontmatter.quality_summary.coverage_ok)

    def test_postgres_targeted_rebuild_refreshes_termbase_and_packets(self) -> None:
        epub_path = self._write_epub("targeted-rebuild")

        with session_scope(self.session_factory) as session:
            service = DocumentWorkflowService(session, export_root=self.export_root)
            summary = service.bootstrap_epub(epub_path)
            document_id = summary.document_id
            chapter_id = summary.chapters[0].chapter_id

            service.translate_document(document_id)

            sentence_id = session.scalars(
                select(Sentence.id).where(
                    Sentence.document_id == document_id,
                    Sentence.source_text.like("Pricing power%"),
                )
            ).one()
            session.add(
                TermEntry(
                    id=str(uuid4()),
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
            session.flush()

            review = service.review_document(document_id)
            self.assertGreaterEqual(review.total_issue_count, 1)
            action_id = session.scalars(
                select(IssueAction.id)
                .join(ReviewIssue, IssueAction.issue_id == ReviewIssue.id)
                .where(ReviewIssue.document_id == document_id)
                .order_by(IssueAction.created_at.desc())
            ).first()
            self.assertIsNotNone(action_id)

        with session_scope(self.session_factory) as session:
            service = DocumentWorkflowService(
                session,
                export_root=self.export_root,
                translation_worker=PacketAwareTermWorker(),
            )
            result = service.execute_action(action_id, run_followup=True)

            self.assertIsNotNone(result.rerun_execution)
            assert result.rerun_execution is not None
            self.assertTrue(result.rerun_execution.issue_resolved)
            self.assertIsNotNone(result.rerun_execution.rebuild_artifacts)
            assert result.rerun_execution.rebuild_artifacts is not None
            self.assertGreaterEqual(len(result.rerun_execution.rebuild_artifacts.rebuilt_packet_ids), 1)
            self.assertGreaterEqual(len(result.rerun_execution.rebuild_artifacts.rebuilt_snapshot_ids), 1)
            self.assertIn(
                "termbase",
                [snapshot.snapshot_type for snapshot in result.rerun_execution.rebuild_artifacts.rebuilt_snapshots],
            )
            self.assertGreater(result.rerun_execution.rebuild_artifacts.termbase_version or 0, 1)

            packets = session.scalars(
                select(TranslationPacket).where(TranslationPacket.chapter_id == chapter_id)
            ).all()
            self.assertTrue(
                any(
                    packet.termbase_version and packet.termbase_version > 1
                    and any(term.get("source_term") == "pricing power" for term in packet.packet_json.get("relevant_terms", []))
                    for packet in packets
                )
            )

            termbase_snapshots = session.scalars(
                select(MemorySnapshot).where(
                    MemorySnapshot.document_id == document_id,
                    MemorySnapshot.snapshot_type == SnapshotType.TERMBASE,
                )
            ).all()
            self.assertGreaterEqual(len(termbase_snapshots), 2)
            self.assertTrue(any(snapshot.status.value == "superseded" for snapshot in termbase_snapshots))
            self.assertTrue(any(snapshot.status.value == "active" and snapshot.version > 1 for snapshot in termbase_snapshots))

    def test_postgres_targeted_rebuild_rebuilds_chapter_brief(self) -> None:
        epub_path = self._write_epub("chapter-brief-rebuild")

        with session_scope(self.session_factory) as session:
            service = DocumentWorkflowService(session, export_root=self.export_root)
            summary = service.bootstrap_epub(epub_path)
            document_id = summary.document_id
            chapter_id = summary.chapters[0].chapter_id

            service.translate_document(document_id)

            sentence_id = session.scalars(
                select(Sentence.id).where(
                    Sentence.document_id == document_id,
                    Sentence.source_text.like("Pricing power%"),
                )
            ).one()
            packet_id = session.scalars(
                select(TranslationPacket.id).where(TranslationPacket.chapter_id == chapter_id)
            ).first()
            issue = ReviewIssue(
                id=str(uuid4()),
                document_id=document_id,
                chapter_id=chapter_id,
                sentence_id=sentence_id,
                packet_id=packet_id,
                issue_type="CONTEXT_FAILURE",
                root_cause_layer=RootCauseLayer.MEMORY,
                severity=Severity.MEDIUM,
                blocking=False,
                detector=Detector.HUMAN,
                confidence=1.0,
                evidence_json={"reason": "chapter_brief_stale"},
                status=IssueStatus.OPEN,
            )
            action = IssueAction(
                id=str(uuid4()),
                issue_id=issue.id,
                action_type=ActionType.REBUILD_CHAPTER_BRIEF,
                scope_type=JobScopeType.CHAPTER,
                scope_id=chapter_id,
                status=ActionStatus.PLANNED,
                reason_json={"issue_type": "CONTEXT_FAILURE"},
                created_by=ActionActorType.SYSTEM,
            )
            session.add(issue)
            session.flush()
            session.add(action)
            session.flush()
            action_id = action.id

        with session_scope(self.session_factory) as session:
            service = DocumentWorkflowService(session, export_root=self.export_root)
            result = service.execute_action(action_id, run_followup=True)

            self.assertIsNotNone(result.rerun_execution)
            assert result.rerun_execution is not None
            self.assertTrue(result.rerun_execution.issue_resolved)
            self.assertIsNotNone(result.rerun_execution.rebuild_artifacts)
            assert result.rerun_execution.rebuild_artifacts is not None
            self.assertIn(
                "chapter_brief",
                [snapshot.snapshot_type for snapshot in result.rerun_execution.rebuild_artifacts.rebuilt_snapshots],
            )
            self.assertGreater(result.rerun_execution.rebuild_artifacts.chapter_brief_version or 0, 1)

            packets = session.scalars(
                select(TranslationPacket).where(TranslationPacket.chapter_id == chapter_id)
            ).all()
            self.assertTrue(all(packet.chapter_brief_version and packet.chapter_brief_version > 1 for packet in packets))
            chapter_brief_snapshots = session.scalars(
                select(MemorySnapshot).where(
                    MemorySnapshot.document_id == document_id,
                    MemorySnapshot.snapshot_type == SnapshotType.CHAPTER_BRIEF,
                    MemorySnapshot.scope_id == chapter_id,
                )
            ).all()
            self.assertGreaterEqual(len(chapter_brief_snapshots), 2)
            self.assertTrue(any(snapshot.status.value == "superseded" for snapshot in chapter_brief_snapshots))
            self.assertTrue(
                any(snapshot.status.value == "active" and snapshot.version > 1 for snapshot in chapter_brief_snapshots)
            )

    def test_postgres_review_ignores_missing_title_context_failure_for_zero_sentence_frontmatter(self) -> None:
        epub_path = self._write_epub_with_chapters(
            "zero-sentence-context-failure",
            [
                ("Welcome", "welcome.xhtml", EMPTY_CHAPTER_XHTML),
                ("Chapter One", "chapter1.xhtml", _chapter_xhtml("zero-sentence-context-failure")),
            ],
        )

        with session_scope(self.session_factory) as session:
            service = DocumentWorkflowService(session, export_root=self.export_root)
            summary = service.bootstrap_epub(epub_path)
            document_id = summary.document_id
            frontmatter = next(chapter for chapter in summary.chapters if chapter.packet_count == 0)
            chapter_brief = session.scalars(
                select(MemorySnapshot).where(
                    MemorySnapshot.document_id == document_id,
                    MemorySnapshot.scope_type == MemoryScopeType.CHAPTER,
                    MemorySnapshot.scope_id == frontmatter.chapter_id,
                    MemorySnapshot.snapshot_type == SnapshotType.CHAPTER_BRIEF,
                )
            ).one()
            content_json = dict(chapter_brief.content_json)
            content_json["open_questions"] = ["missing_chapter_title"]
            chapter_brief.content_json = content_json
            session.merge(chapter_brief)
            session.flush()

            translate = service.translate_document(document_id)
            self.assertEqual(translate.translated_packet_count, 3)

            review = service.review_document(document_id)
            self.assertEqual(review.total_issue_count, 0)

            issue = session.scalars(
                select(ReviewIssue).where(
                    ReviewIssue.document_id == document_id,
                    ReviewIssue.chapter_id == frontmatter.chapter_id,
                    ReviewIssue.issue_type == "CONTEXT_FAILURE",
                )
            ).first()
            self.assertIsNone(issue)

            review_export = service.export_document(document_id, ExportType.REVIEW_PACKAGE)
            self.assertEqual(len(review_export.chapter_results), 2)
            bilingual_export = service.export_document(document_id, ExportType.BILINGUAL_HTML)
            self.assertEqual(bilingual_export.document_status, "exported")

    def test_postgres_literal_source_tag_is_not_reported_as_format_pollution(self) -> None:
        epub_path = self._write_epub_with_chapters(
            "literal-tag",
            [
                ("Chapter One", "chapter1.xhtml", LITERAL_TAG_XHTML),
            ],
        )

        with session_scope(self.session_factory) as session:
            service = DocumentWorkflowService(
                session,
                export_root=self.export_root,
                translation_worker=LiteralTagWorker(),
            )
            summary = service.bootstrap_epub(epub_path)
            document_id = summary.document_id

            translate = service.translate_document(document_id)
            self.assertGreaterEqual(translate.translated_packet_count, 1)

            review = service.review_document(document_id)
            self.assertEqual(review.total_issue_count, 0)
            self.assertTrue(all(result.format_ok for result in review.chapter_results))

            issue = session.scalars(
                select(ReviewIssue).where(
                    ReviewIssue.document_id == document_id,
                    ReviewIssue.issue_type == "FORMAT_POLLUTION",
                )
            ).first()
            self.assertIsNone(issue)

    def test_postgres_merged_html_export_renders_prose_and_code_with_different_modes(self) -> None:
        epub_path = self._write_epub_with_chapters(
            "merged-html",
            [
                ("Chapter One", "chapter1.xhtml", CODE_CHAPTER_XHTML),
            ],
        )

        with session_scope(self.session_factory) as session:
            service = DocumentWorkflowService(session, export_root=self.export_root)
            summary = service.bootstrap_epub(epub_path)
            document_id = summary.document_id

            translate = service.translate_document(document_id)
            self.assertGreaterEqual(translate.translated_packet_count, 1)

            review = service.review_document(document_id)
            self.assertEqual(review.total_issue_count, 0)

            export = service.export_document(document_id, ExportType.MERGED_HTML)
            self.assertEqual(export.document_status, "exported")
            self.assertIsNotNone(export.file_path)
            assert export.file_path is not None
            merged_html = Path(export.file_path).read_text(encoding="utf-8")
            self.assertIn("Reading Map", merged_html)
            self.assertIn("Back to top", merged_html)
            self.assertIn("href='#chapter-", merged_html)
            self.assertIn("ZH::Use the example carefully.", merged_html)
            self.assertIn("代码保持原样", merged_html)
            self.assertIn("python agent.py --dry-run", merged_html)
            self.assertEqual(export.chapter_results, [])

    def test_postgres_merged_html_skips_empty_untitled_frontmatter_chapter(self) -> None:
        epub_path = self._write_epub_with_chapters(
            "merged-html-empty-frontmatter",
            [
                ("", "welcome.xhtml", EMPTY_CHAPTER_XHTML),
                ("Chapter One", "chapter1.xhtml", _chapter_xhtml("merged-html-empty-frontmatter")),
            ],
        )

        with session_scope(self.session_factory) as session:
            service = DocumentWorkflowService(session, export_root=self.export_root)
            summary = service.bootstrap_epub(epub_path)
            document_id = summary.document_id
            frontmatter = next(chapter for chapter in summary.chapters if chapter.packet_count == 0)
            frontmatter_chapter = session.get(Chapter, frontmatter.chapter_id)
            assert frontmatter_chapter is not None
            frontmatter_chapter.title_src = None
            frontmatter_chapter.title_tgt = None
            session.merge(frontmatter_chapter)
            session.flush()

            translate = service.translate_document(document_id)
            self.assertGreaterEqual(translate.translated_packet_count, 1)
            review = service.review_document(document_id)
            self.assertEqual(review.total_issue_count, 0)

            export = service.export_document(document_id, ExportType.MERGED_HTML)
            self.assertEqual(export.document_status, "exported")
            assert export.file_path is not None
            merged_html = Path(export.file_path).read_text(encoding="utf-8")
            self.assertNotIn(frontmatter.chapter_id, merged_html)
            self.assertIn("Chapter One", merged_html)

    def test_postgres_merged_html_renders_structured_artifacts_with_special_modes(self) -> None:
        epub_path = self._write_epub_with_chapters(
            "merged-html-structured-artifacts",
            [
                ("Chapter One", "chapter1.xhtml", STRUCTURED_ARTIFACT_XHTML),
            ],
        )

        with session_scope(self.session_factory) as session:
            service = DocumentWorkflowService(session, export_root=self.export_root)
            summary = service.bootstrap_epub(epub_path)
            document_id = summary.document_id

            translate = service.translate_document(document_id)
            self.assertGreaterEqual(translate.translated_packet_count, 1)
            review = service.review_document(document_id)
            self.assertEqual(review.total_issue_count, 0)

            export = service.export_document(document_id, ExportType.MERGED_HTML)
            self.assertEqual(export.document_status, "exported")
            assert export.file_path is not None
            merged_html = Path(export.file_path).read_text(encoding="utf-8")
            self.assertIn("图片锚点保留", merged_html)
            self.assertIn("images/agent-loop.png", merged_html)
            self.assertIn("公式保持原样", merged_html)
            self.assertIn("x=1", merged_html)
            self.assertIn("保留原始结构，优先保证可复制与结构保真", merged_html)
            self.assertIn("Tier | Latency", merged_html)
            self.assertIn("Basic | Slow", merged_html)
            self.assertIn("参考标识保留", merged_html)
            self.assertIn("https://example.com/agent-docs", merged_html)

    def test_postgres_realign_only_restores_missing_alignment_edges(self) -> None:
        epub_path = self._write_epub("realign-only")

        with session_scope(self.session_factory) as session:
            service = DocumentWorkflowService(session, export_root=self.export_root)
            summary = service.bootstrap_epub(epub_path)
            document_id = summary.document_id
            chapter_id = summary.chapters[0].chapter_id
            service.translate_document(document_id)

            packet_id = next(
                packet.id
                for packet in session.scalars(
                    select(TranslationPacket).where(TranslationPacket.chapter_id == chapter_id)
                ).all()
                if any(len(block.get("sentence_ids", [])) >= 2 for block in packet.packet_json.get("current_blocks", []))
            )
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
            session.flush()

            review = service.review_document(document_id)
            self.assertGreaterEqual(review.total_issue_count, 1)
            action_id = session.scalars(
                select(IssueAction.id)
                .join(ReviewIssue, IssueAction.issue_id == ReviewIssue.id)
                .where(ReviewIssue.document_id == document_id, ReviewIssue.issue_type == "ALIGNMENT_FAILURE")
                .order_by(IssueAction.created_at.desc())
            ).first()
            self.assertIsNotNone(action_id)

        with session_scope(self.session_factory) as session:
            service = DocumentWorkflowService(session, export_root=self.export_root)
            result = service.execute_action(action_id, run_followup=True)

            self.assertIsNotNone(result.rerun_execution)
            assert result.rerun_execution is not None
            self.assertTrue(result.rerun_execution.issue_resolved)
            self.assertEqual(result.rerun_execution.translation_run_ids, [])
            self.assertEqual(result.rerun_execution.translated_packet_ids, [packet_id])

            restored_edge_count = session.scalar(
                select(func.count(AlignmentEdge.id)).where(AlignmentEdge.target_segment_id == target_segment_id)
            )
            self.assertGreaterEqual(restored_edge_count or 0, 1)

    def test_postgres_review_package_contains_quality_and_repair_evidence(self) -> None:
        epub_path = self._write_epub("review-package-evidence")

        with session_scope(self.session_factory) as session:
            service = DocumentWorkflowService(session, export_root=self.export_root)
            summary = service.bootstrap_epub(epub_path)
            document_id = summary.document_id

            service.translate_document(document_id)

            sentence_id = session.scalars(
                select(Sentence.id).where(
                    Sentence.document_id == document_id,
                    Sentence.source_text.like("Pricing power%"),
                )
            ).one()
            session.add(
                TermEntry(
                    id=str(uuid4()),
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
            session.flush()

            review = service.review_document(document_id)
            self.assertGreaterEqual(review.total_issue_count, 1)
            action_id = session.scalars(
                select(IssueAction.id)
                .join(ReviewIssue, IssueAction.issue_id == ReviewIssue.id)
                .where(ReviewIssue.document_id == document_id)
                .order_by(IssueAction.created_at.desc())
            ).first()
            self.assertIsNotNone(action_id)

        with session_scope(self.session_factory) as session:
            service = DocumentWorkflowService(
                session,
                export_root=self.export_root,
                translation_worker=PacketAwareTermWorker(),
            )
            result = service.execute_action(action_id, run_followup=True)
            self.assertIsNotNone(result.rerun_execution)
            assert result.rerun_execution is not None
            self.assertTrue(result.rerun_execution.issue_resolved)

            export = service.export_document(document_id, ExportType.REVIEW_PACKAGE)
            review_package_path = Path(export.chapter_results[0].file_path)
            review_package = json.loads(review_package_path.read_text(encoding="utf-8"))

            self.assertTrue(review_package["quality_summary"]["coverage_ok"])
            self.assertGreaterEqual(len(review_package["version_evidence"]["active_snapshots"]), 2)
            self.assertGreaterEqual(len(review_package["version_evidence"]["packet_context_versions"]), 1)
            self.assertTrue(
                any(event["action"] == "snapshot.rebuilt" for event in review_package["recent_repair_events"])
            )
            self.assertTrue(
                any(event["action"] == "packet.rebuilt" for event in review_package["recent_repair_events"])
            )

    def test_postgres_bilingual_export_writes_manifest_evidence(self) -> None:
        epub_path = self._write_epub("bilingual-manifest-evidence")

        with session_scope(self.session_factory) as session:
            service = DocumentWorkflowService(session, export_root=self.export_root)
            summary = service.bootstrap_epub(epub_path)
            document_id = summary.document_id

            service.translate_document(document_id)
            review = service.review_document(document_id)
            self.assertEqual(review.total_issue_count, 0)

            export = service.export_document(document_id, ExportType.BILINGUAL_HTML)
            chapter_export = export.chapter_results[0]
            self.assertIsNotNone(chapter_export.manifest_path)
            manifest_path = Path(chapter_export.manifest_path)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(manifest["export_type"], "bilingual_html")
            self.assertEqual(manifest["html_path"], chapter_export.file_path)
            self.assertTrue(manifest["quality_summary"]["coverage_ok"])
            self.assertGreaterEqual(len(manifest["version_evidence"]["packet_context_versions"]), 1)
            self.assertFalse(manifest["export_time_misalignment_evidence"]["has_anomalies"])
            self.assertEqual(manifest["row_summary"]["sentence_row_count"], 4)
            self.assertEqual(manifest["issue_summary"]["open_issue_count"], 0)

    def test_postgres_document_export_dashboard_lists_export_records(self) -> None:
        epub_path = self._write_epub("export-dashboard")

        with session_scope(self.session_factory) as session:
            service = DocumentWorkflowService(session, export_root=self.export_root)
            summary = service.bootstrap_epub(epub_path)
            document_id = summary.document_id

            service.translate_document(document_id)
            service.review_document(document_id)
            review_export = service.export_document(document_id, ExportType.REVIEW_PACKAGE)
            bilingual_export = service.export_document(document_id, ExportType.BILINGUAL_HTML)

            dashboard = service.get_document_export_dashboard(document_id)
            self.assertEqual(dashboard.document_id, document_id)
            self.assertEqual(dashboard.export_count, 2)
            self.assertEqual(dashboard.successful_export_count, 2)
            self.assertEqual(dashboard.filtered_export_count, 2)
            self.assertEqual(dashboard.record_count, 2)
            self.assertEqual(dashboard.offset, 0)
            self.assertIsNone(dashboard.limit)
            self.assertFalse(dashboard.has_more)
            self.assertIsNone(dashboard.applied_export_type_filter)
            self.assertIsNone(dashboard.applied_status_filter)
            self.assertEqual(dashboard.export_counts_by_type["review_package"], 1)
            self.assertEqual(dashboard.export_counts_by_type["bilingual_html"], 1)
            self.assertEqual(dashboard.latest_export_ids_by_type["review_package"], review_export.chapter_results[0].export_id)
            self.assertEqual(dashboard.latest_export_ids_by_type["bilingual_html"], bilingual_export.chapter_results[0].export_id)
            assert dashboard.translation_usage_summary is not None
            self.assertEqual(dashboard.translation_usage_summary.run_count, 3)
            self.assertEqual(dashboard.translation_usage_summary.succeeded_run_count, 3)
            self.assertEqual(len(dashboard.translation_usage_breakdown), 1)
            self.assertEqual(dashboard.translation_usage_breakdown[0].model_name, "echo-worker")
            self.assertEqual(dashboard.translation_usage_breakdown[0].worker_name, "EchoTranslationWorker")
            self.assertEqual(len(dashboard.translation_usage_timeline), 1)
            self.assertEqual(dashboard.translation_usage_timeline[0].bucket_granularity, "day")
            self.assertEqual(dashboard.translation_usage_timeline[0].run_count, 3)
            assert dashboard.translation_usage_highlights.top_cost_entry is not None
            assert dashboard.translation_usage_highlights.top_latency_entry is not None
            assert dashboard.translation_usage_highlights.top_volume_entry is not None
            self.assertEqual(dashboard.translation_usage_highlights.top_cost_entry.model_name, "echo-worker")
            self.assertEqual(dashboard.translation_usage_highlights.top_latency_entry.model_name, "echo-worker")
            self.assertEqual(dashboard.translation_usage_highlights.top_volume_entry.model_name, "echo-worker")
            self.assertEqual(dashboard.issue_hotspots, [])
            self.assertEqual(dashboard.issue_chapter_pressure, [])
            self.assertIsNone(dashboard.issue_chapter_highlights.top_open_chapter)
            self.assertIsNone(dashboard.issue_chapter_highlights.top_blocking_chapter)
            self.assertIsNone(dashboard.issue_chapter_highlights.top_resolved_chapter)
            self.assertEqual(dashboard.issue_chapter_breakdown, [])
            self.assertEqual(dashboard.issue_chapter_heatmap, [])
            self.assertEqual(dashboard.issue_chapter_queue, [])
            self.assertEqual(dashboard.issue_activity_timeline, [])
            self.assertEqual(dashboard.issue_activity_breakdown, [])
            self.assertIsNone(dashboard.issue_activity_highlights.top_regressing_entry)
            self.assertIsNone(dashboard.issue_activity_highlights.top_resolving_entry)
            self.assertIsNone(dashboard.issue_activity_highlights.top_blocking_entry)
            self.assertEqual(len(dashboard.records), 2)
            record_by_type = {record.export_type: record for record in dashboard.records}
            self.assertEqual(record_by_type["bilingual_html"].manifest_path, bilingual_export.chapter_results[0].manifest_path)
            self.assertEqual(record_by_type["review_package"].chapter_id, summary.chapters[0].chapter_id)
            assert record_by_type["bilingual_html"].export_auto_followup_summary is not None
            self.assertEqual(record_by_type["bilingual_html"].export_auto_followup_summary.executed_event_count, 0)
            assert record_by_type["bilingual_html"].translation_usage_summary is not None
            self.assertEqual(record_by_type["bilingual_html"].translation_usage_summary.run_count, 3)
            self.assertEqual(len(record_by_type["bilingual_html"].translation_usage_breakdown), 1)
            self.assertEqual(record_by_type["bilingual_html"].translation_usage_breakdown[0].model_name, "echo-worker")
            self.assertEqual(len(record_by_type["bilingual_html"].translation_usage_timeline), 1)
            self.assertEqual(record_by_type["bilingual_html"].translation_usage_timeline[0].run_count, 3)
            assert record_by_type["bilingual_html"].translation_usage_highlights is not None
            assert record_by_type["bilingual_html"].translation_usage_highlights.top_cost_entry is not None
            self.assertEqual(
                record_by_type["bilingual_html"].translation_usage_highlights.top_cost_entry.model_name,
                "echo-worker",
            )

    def test_postgres_document_export_dashboard_supports_filtering_and_pagination(self) -> None:
        epub_path = self._write_epub("export-dashboard-filtered")

        with session_scope(self.session_factory) as session:
            service = DocumentWorkflowService(session, export_root=self.export_root)
            summary = service.bootstrap_epub(epub_path)
            document_id = summary.document_id

            service.translate_document(document_id)
            service.review_document(document_id)
            review_export = service.export_document(document_id, ExportType.REVIEW_PACKAGE)
            service.export_document(document_id, ExportType.BILINGUAL_HTML)

            paged_dashboard = service.get_document_export_dashboard(
                document_id,
                limit=1,
                offset=0,
            )
            self.assertEqual(paged_dashboard.export_count, 2)
            self.assertEqual(paged_dashboard.successful_export_count, 2)
            self.assertEqual(paged_dashboard.filtered_export_count, 2)
            self.assertEqual(paged_dashboard.record_count, 1)
            self.assertEqual(paged_dashboard.offset, 0)
            self.assertEqual(paged_dashboard.limit, 1)
            self.assertTrue(paged_dashboard.has_more)
            self.assertIsNone(paged_dashboard.applied_export_type_filter)
            self.assertIsNone(paged_dashboard.applied_status_filter)
            assert paged_dashboard.translation_usage_summary is not None
            self.assertEqual(paged_dashboard.translation_usage_summary.run_count, 3)
            self.assertEqual(paged_dashboard.translation_usage_breakdown[0].model_name, "echo-worker")
            self.assertEqual(paged_dashboard.translation_usage_timeline[0].run_count, 3)
            assert paged_dashboard.translation_usage_highlights.top_volume_entry is not None
            self.assertEqual(paged_dashboard.translation_usage_highlights.top_volume_entry.model_name, "echo-worker")
            self.assertEqual(paged_dashboard.issue_hotspots, [])
            self.assertEqual(paged_dashboard.issue_chapter_pressure, [])
            self.assertIsNone(paged_dashboard.issue_chapter_highlights.top_open_chapter)
            self.assertIsNone(paged_dashboard.issue_chapter_highlights.top_blocking_chapter)
            self.assertIsNone(paged_dashboard.issue_chapter_highlights.top_resolved_chapter)
            self.assertEqual(paged_dashboard.issue_chapter_breakdown, [])
            self.assertEqual(paged_dashboard.issue_chapter_heatmap, [])
            self.assertEqual(paged_dashboard.issue_chapter_queue, [])
            self.assertEqual(paged_dashboard.issue_activity_timeline, [])
            self.assertEqual(paged_dashboard.issue_activity_breakdown, [])
            self.assertIsNone(paged_dashboard.issue_activity_highlights.top_regressing_entry)
            self.assertIsNone(paged_dashboard.issue_activity_highlights.top_resolving_entry)
            self.assertIsNone(paged_dashboard.issue_activity_highlights.top_blocking_entry)

            filtered_dashboard = service.get_document_export_dashboard(
                document_id,
                export_type=ExportType.REVIEW_PACKAGE,
                status=ExportStatus.SUCCEEDED,
                limit=1,
                offset=1,
            )
            self.assertEqual(filtered_dashboard.export_count, 2)
            self.assertEqual(filtered_dashboard.successful_export_count, 2)
            self.assertEqual(filtered_dashboard.filtered_export_count, 1)
            self.assertEqual(filtered_dashboard.record_count, 0)
            self.assertEqual(filtered_dashboard.offset, 1)
            self.assertEqual(filtered_dashboard.limit, 1)
            self.assertFalse(filtered_dashboard.has_more)
            self.assertEqual(filtered_dashboard.applied_export_type_filter, "review_package")
            self.assertEqual(filtered_dashboard.applied_status_filter, "succeeded")
            self.assertEqual(len(filtered_dashboard.records), 0)

            first_filtered_dashboard = service.get_document_export_dashboard(
                document_id,
                export_type=ExportType.REVIEW_PACKAGE,
                status=ExportStatus.SUCCEEDED,
                limit=1,
                offset=0,
            )
            self.assertEqual(len(first_filtered_dashboard.records), 1)
            self.assertEqual(first_filtered_dashboard.records[0].export_id, review_export.chapter_results[0].export_id)

    def test_postgres_document_chapter_worklist_returns_filtered_queue(self) -> None:
        epub_path = self._write_epub("chapter-worklist")

        with session_scope(self.session_factory) as session:
            service = DocumentWorkflowService(session, export_root=self.export_root)
            summary = service.bootstrap_epub(epub_path)
            document_id = summary.document_id

            service.translate_document(document_id)
            service.review_document(document_id)

            sentence_id = session.scalars(
                select(Sentence.id).where(
                    Sentence.document_id == document_id,
                    Sentence.source_text.like("Pricing power%"),
                )
            ).one()
            session.execute(delete(AlignmentEdge).where(AlignmentEdge.sentence_id == sentence_id))
            session.flush()

            with self.assertRaises(ExportGateError):
                service.export_document(document_id, ExportType.BILINGUAL_HTML)

            issue = session.scalars(
                select(ReviewIssue).where(
                    ReviewIssue.document_id == document_id,
                    ReviewIssue.issue_type == "ALIGNMENT_FAILURE",
                    ReviewIssue.root_cause_layer == RootCauseLayer.EXPORT,
                )
            ).one()
            issue.created_at = datetime.now(timezone.utc) - timedelta(hours=5)
            session.flush()

            worklist = service.get_document_chapter_worklist(
                document_id,
                queue_priority="immediate",
                sla_status="breached",
                owner_ready=True,
                needs_immediate_attention=True,
                limit=1,
                offset=0,
            )
            self.assertEqual(worklist.document_id, document_id)
            self.assertEqual(worklist.worklist_count, 1)
            self.assertEqual(worklist.filtered_worklist_count, 1)
            self.assertEqual(worklist.entry_count, 1)
            self.assertEqual(worklist.offset, 0)
            self.assertEqual(worklist.limit, 1)
            self.assertFalse(worklist.has_more)
            self.assertEqual(worklist.applied_queue_priority_filter, "immediate")
            self.assertEqual(worklist.applied_sla_status_filter, "breached")
            self.assertTrue(worklist.applied_owner_ready_filter)
            self.assertTrue(worklist.applied_needs_immediate_attention_filter)
            self.assertEqual(worklist.queue_priority_counts["immediate"], 1)
            self.assertEqual(worklist.sla_status_counts["breached"], 1)
            self.assertEqual(worklist.immediate_attention_count, 1)
            self.assertEqual(worklist.owner_ready_count, 1)
            self.assertEqual(worklist.assigned_count, 0)
            self.assertEqual(worklist.owner_workload_summary, [])
            self.assertIsNone(worklist.owner_workload_highlights["top_loaded_owner"])
            self.assertIsNone(worklist.owner_workload_highlights["top_breached_owner"])
            self.assertIsNone(worklist.owner_workload_highlights["top_blocking_owner"])
            self.assertIsNone(worklist.owner_workload_highlights["top_immediate_owner"])
            assert worklist.highlights["top_breached_entry"] is not None
            self.assertIsNone(worklist.highlights["top_due_soon_entry"])
            assert worklist.highlights["top_oldest_entry"] is not None
            assert worklist.highlights["top_immediate_entry"] is not None
            self.assertEqual(
                worklist.highlights["top_breached_entry"].dominant_issue_type,
                "ALIGNMENT_FAILURE",
            )
            self.assertEqual(len(worklist.entries), 1)
            entry = worklist.entries[0]
            self.assertEqual(entry.queue_rank, 1)
            self.assertEqual(entry.queue_priority, "immediate")
            self.assertEqual(entry.sla_status, "breached")
            self.assertFalse(entry.is_assigned)
            self.assertIsNone(entry.assigned_owner_name)
            self.assertTrue(entry.needs_immediate_attention)
            self.assertTrue(entry.owner_ready)
            self.assertEqual(entry.dominant_issue_type, "ALIGNMENT_FAILURE")
            self.assertEqual(entry.dominant_root_cause_layer, "export")

            assignment = service.assign_document_chapter_worklist_owner(
                document_id,
                summary.chapters[0].chapter_id,
                owner_name="ops-alice",
                assigned_by="ops-lead",
                note="Take export alignment fix",
            )
            self.assertEqual(assignment.owner_name, "ops-alice")
            self.assertEqual(assignment.assigned_by, "ops-lead")

            detail = service.get_document_chapter_worklist_detail(document_id, summary.chapters[0].chapter_id)
            self.assertEqual(detail.document_id, document_id)
            self.assertEqual(detail.chapter_id, summary.chapters[0].chapter_id)
            self.assertEqual(detail.ordinal, 1)
            self.assertEqual(detail.title_src, "Chapter One")
            self.assertEqual(detail.packet_count, 3)
            self.assertEqual(detail.translated_packet_count, 3)
            self.assertEqual(detail.current_issue_count, 1)
            self.assertEqual(detail.current_open_issue_count, 1)
            self.assertEqual(detail.current_triaged_issue_count, 0)
            self.assertEqual(detail.current_active_blocking_issue_count, 1)
            assert detail.assignment is not None
            self.assertEqual(detail.assignment.owner_name, "ops-alice")
            self.assertEqual(detail.assignment.assigned_by, "ops-lead")
            self.assertEqual(len(detail.assignment_history), 1)
            self.assertEqual(detail.assignment_history[0].event_type, "set")
            self.assertEqual(detail.assignment_history[0].owner_name, "ops-alice")
            self.assertEqual(detail.assignment_history[0].performed_by, "ops-lead")
            assert detail.queue_entry is not None
            self.assertEqual(detail.queue_entry.queue_priority, "immediate")
            self.assertEqual(detail.queue_entry.sla_status, "breached")
            self.assertTrue(detail.queue_entry.is_assigned)
            self.assertEqual(detail.queue_entry.assigned_owner_name, "ops-alice")
            self.assertEqual(len(detail.issue_family_breakdown), 1)
            self.assertEqual(detail.issue_family_breakdown[0].issue_type, "ALIGNMENT_FAILURE")
            self.assertEqual(detail.issue_family_breakdown[0].root_cause_layer, "export")
            self.assertEqual(detail.recent_issues[0].issue_type, "ALIGNMENT_FAILURE")
            self.assertEqual(detail.recent_issues[0].status, "open")
            self.assertEqual(detail.recent_actions[0].issue_type, "ALIGNMENT_FAILURE")
            self.assertEqual(detail.recent_actions[0].action_type, "REALIGN_ONLY")

            assigned_worklist = service.get_document_chapter_worklist(
                document_id,
                assigned=True,
                assigned_owner_name="ops-alice",
            )
            self.assertEqual(assigned_worklist.assigned_count, 1)
            self.assertTrue(assigned_worklist.applied_assigned_filter)
            self.assertEqual(assigned_worklist.applied_assigned_owner_filter, "ops-alice")
            self.assertEqual(len(assigned_worklist.entries), 1)
            self.assertTrue(assigned_worklist.entries[0].is_assigned)
            self.assertEqual(len(assigned_worklist.owner_workload_summary), 1)
            owner_summary = assigned_worklist.owner_workload_summary[0]
            self.assertEqual(owner_summary.owner_name, "ops-alice")
            self.assertEqual(owner_summary.assigned_chapter_count, 1)
            self.assertEqual(owner_summary.immediate_count, 1)
            self.assertEqual(owner_summary.breached_count, 1)
            self.assertEqual(owner_summary.owner_ready_count, 1)
            self.assertEqual(owner_summary.total_open_issue_count, 1)
            self.assertEqual(owner_summary.total_active_blocking_issue_count, 1)
            assert assigned_worklist.owner_workload_highlights["top_loaded_owner"] is not None
            self.assertEqual(
                assigned_worklist.owner_workload_highlights["top_loaded_owner"].owner_name,
                "ops-alice",
            )
            assert assigned_worklist.owner_workload_highlights["top_breached_owner"] is not None
            self.assertEqual(
                assigned_worklist.owner_workload_highlights["top_breached_owner"].owner_name,
                "ops-alice",
            )
            assert assigned_worklist.owner_workload_highlights["top_blocking_owner"] is not None
            self.assertEqual(
                assigned_worklist.owner_workload_highlights["top_blocking_owner"].owner_name,
                "ops-alice",
            )
            assert assigned_worklist.owner_workload_highlights["top_immediate_owner"] is not None
            self.assertEqual(
                assigned_worklist.owner_workload_highlights["top_immediate_owner"].owner_name,
                "ops-alice",
            )

            cleared = service.clear_document_chapter_worklist_owner(
                document_id,
                summary.chapters[0].chapter_id,
                cleared_by="ops-lead",
                note="Requeue for pool",
            )
            self.assertEqual(cleared.owner_name, "ops-alice")

            detail_after_clear = service.get_document_chapter_worklist_detail(document_id, summary.chapters[0].chapter_id)
            self.assertIsNone(detail_after_clear.assignment)
            assert detail_after_clear.queue_entry is not None
            self.assertFalse(detail_after_clear.queue_entry.is_assigned)
            self.assertEqual(len(detail_after_clear.assignment_history), 2)
            self.assertEqual(detail_after_clear.assignment_history[0].event_type, "cleared")
            self.assertEqual(detail_after_clear.assignment_history[0].performed_by, "ops-lead")
            self.assertEqual(detail_after_clear.assignment_history[1].event_type, "set")
            worklist_after_clear = service.get_document_chapter_worklist(document_id)
            self.assertEqual(worklist_after_clear.owner_workload_summary, [])
            self.assertIsNone(worklist_after_clear.owner_workload_highlights["top_loaded_owner"])

    def test_postgres_document_export_detail_returns_persisted_usage_and_evidence(self) -> None:
        epub_path = self._write_epub("export-detail")

        with session_scope(self.session_factory) as session:
            service = DocumentWorkflowService(session, export_root=self.export_root)
            summary = service.bootstrap_epub(epub_path)
            document_id = summary.document_id

            service.translate_document(document_id)
            service.review_document(document_id)
            export = service.export_document(document_id, ExportType.BILINGUAL_HTML)
            export_id = export.chapter_results[0].export_id

            detail = service.get_document_export_detail(document_id, export_id)
            self.assertEqual(detail.document_id, document_id)
            self.assertEqual(detail.export_id, export_id)
            self.assertEqual(detail.export_type, "bilingual_html")
            self.assertEqual(detail.sentence_count, 4)
            self.assertEqual(detail.target_segment_count, 4)
            assert detail.translation_usage_summary is not None
            self.assertEqual(detail.translation_usage_summary.run_count, 3)
            self.assertEqual(detail.translation_usage_summary.succeeded_run_count, 3)
            self.assertEqual(detail.translation_usage_summary.total_cost_usd, 0.0)
            self.assertEqual(len(detail.translation_usage_breakdown), 1)
            self.assertEqual(detail.translation_usage_breakdown[0].model_name, "echo-worker")
            self.assertEqual(detail.translation_usage_breakdown[0].worker_name, "EchoTranslationWorker")
            self.assertEqual(len(detail.translation_usage_timeline), 1)
            self.assertEqual(detail.translation_usage_timeline[0].run_count, 3)
            assert detail.translation_usage_highlights is not None
            assert detail.translation_usage_highlights.top_cost_entry is not None
            assert detail.translation_usage_highlights.top_latency_entry is not None
            assert detail.translation_usage_highlights.top_volume_entry is not None
            self.assertEqual(detail.translation_usage_highlights.top_cost_entry.model_name, "echo-worker")
            assert detail.issue_status_summary is not None
            self.assertEqual(detail.issue_status_summary.issue_count, 0)
            self.assertEqual(detail.issue_status_summary.open_issue_count, 0)
            assert detail.export_time_misalignment_counts is not None
            self.assertEqual(detail.export_time_misalignment_counts.missing_target_sentence_count, 0)
            self.assertEqual(detail.version_evidence_summary.active_snapshot_versions["chapter_brief"], 1)
            self.assertEqual(detail.manifest_path, export.chapter_results[0].manifest_path)

    def test_postgres_final_export_blocks_post_review_misalignment_but_review_package_keeps_evidence(self) -> None:
        epub_path = self._write_epub("bilingual-manifest-post-review-misalignment")

        with session_scope(self.session_factory) as session:
            service = DocumentWorkflowService(session, export_root=self.export_root)
            summary = service.bootstrap_epub(epub_path)
            document_id = summary.document_id

            service.translate_document(document_id)
            review = service.review_document(document_id)
            self.assertEqual(review.total_issue_count, 0)

            sentence_id = session.scalars(
                select(Sentence.id).where(
                    Sentence.document_id == document_id,
                    Sentence.source_text.like("Pricing power%"),
                )
            ).one()
            session.execute(delete(AlignmentEdge).where(AlignmentEdge.sentence_id == sentence_id))
            session.flush()

            with self.assertRaisesRegex(ExportGateError, "export-time misalignment anomalies") as exc_info:
                service.export_document(document_id, ExportType.BILINGUAL_HTML)
            self.assertGreaterEqual(len(exc_info.exception.issue_ids), 1)
            self.assertGreaterEqual(len(exc_info.exception.followup_actions), 1)

            export_issue = session.scalars(
                select(ReviewIssue).where(
                    ReviewIssue.document_id == document_id,
                    ReviewIssue.root_cause_layer == RootCauseLayer.EXPORT,
                    ReviewIssue.issue_type == "ALIGNMENT_FAILURE",
                )
            ).first()
            self.assertIsNotNone(export_issue)
            assert export_issue is not None
            self.assertEqual(export_issue.packet_id is not None, True)
            self.assertIn(sentence_id, export_issue.evidence_json["missing_target_sentence_ids"])

            export_action = session.scalars(
                select(IssueAction).where(IssueAction.issue_id == export_issue.id)
            ).first()
            self.assertIsNotNone(export_action)
            assert export_action is not None
            self.assertEqual(export_action.action_type, ActionType.REALIGN_ONLY)
            self.assertIn(export_issue.id, exc_info.exception.issue_ids)
            self.assertIn(export_action.id, [action.action_id for action in exc_info.exception.followup_actions])

            review_export = service.export_document(document_id, ExportType.REVIEW_PACKAGE)
            review_package = json.loads(Path(review_export.chapter_results[0].file_path).read_text(encoding="utf-8"))

            self.assertTrue(review_package["export_time_misalignment_evidence"]["has_anomalies"])
            self.assertIn(sentence_id, review_package["export_time_misalignment_evidence"]["missing_target_sentence_ids"])

            dashboard = service.get_document_export_dashboard(document_id)
            alignment_hotspot = next(
                hotspot
                for hotspot in dashboard.issue_hotspots
                if hotspot.issue_type == "ALIGNMENT_FAILURE" and hotspot.root_cause_layer == "export"
            )
            self.assertEqual(alignment_hotspot.issue_count, 1)
            self.assertEqual(alignment_hotspot.open_issue_count, 1)
            self.assertEqual(alignment_hotspot.resolved_issue_count, 0)
            self.assertEqual(alignment_hotspot.blocking_issue_count, 1)
            self.assertEqual(alignment_hotspot.chapter_count, 1)
            self.assertIsNotNone(alignment_hotspot.latest_seen_at)
            self.assertEqual(len(dashboard.issue_chapter_pressure), 1)
            chapter_pressure = dashboard.issue_chapter_pressure[0]
            self.assertEqual(chapter_pressure.chapter_id, summary.chapters[0].chapter_id)
            self.assertEqual(chapter_pressure.ordinal, 1)
            self.assertEqual(chapter_pressure.title_src, "Chapter One")
            self.assertTrue(chapter_pressure.chapter_status)
            self.assertEqual(chapter_pressure.issue_count, 1)
            self.assertEqual(chapter_pressure.open_issue_count, 1)
            self.assertEqual(chapter_pressure.resolved_issue_count, 0)
            self.assertEqual(chapter_pressure.blocking_issue_count, 1)
            self.assertIsNotNone(chapter_pressure.latest_issue_at)
            assert dashboard.issue_chapter_highlights.top_open_chapter is not None
            assert dashboard.issue_chapter_highlights.top_blocking_chapter is not None
            self.assertEqual(dashboard.issue_chapter_highlights.top_open_chapter.chapter_id, summary.chapters[0].chapter_id)
            self.assertEqual(dashboard.issue_chapter_highlights.top_blocking_chapter.chapter_id, summary.chapters[0].chapter_id)
            self.assertIsNone(dashboard.issue_chapter_highlights.top_resolved_chapter)
            self.assertEqual(len(dashboard.issue_chapter_breakdown), 1)
            chapter_breakdown = dashboard.issue_chapter_breakdown[0]
            self.assertEqual(chapter_breakdown.chapter_id, summary.chapters[0].chapter_id)
            self.assertEqual(chapter_breakdown.issue_type, "ALIGNMENT_FAILURE")
            self.assertEqual(chapter_breakdown.root_cause_layer, "export")
            self.assertEqual(chapter_breakdown.issue_count, 1)
            self.assertEqual(chapter_breakdown.open_issue_count, 1)
            self.assertEqual(chapter_breakdown.active_blocking_issue_count, 1)
            self.assertEqual(len(dashboard.issue_chapter_heatmap), 1)
            chapter_heatmap = dashboard.issue_chapter_heatmap[0]
            self.assertEqual(chapter_heatmap.chapter_id, summary.chapters[0].chapter_id)
            self.assertEqual(chapter_heatmap.issue_family_count, 1)
            self.assertEqual(chapter_heatmap.dominant_issue_type, "ALIGNMENT_FAILURE")
            self.assertEqual(chapter_heatmap.dominant_root_cause_layer, "export")
            self.assertEqual(chapter_heatmap.active_blocking_issue_count, 1)
            self.assertEqual(chapter_heatmap.heat_score, 7)
            self.assertEqual(chapter_heatmap.heat_level, "high")
            self.assertEqual(len(dashboard.issue_chapter_queue), 1)
            chapter_queue = dashboard.issue_chapter_queue[0]
            self.assertEqual(chapter_queue.chapter_id, summary.chapters[0].chapter_id)
            self.assertEqual(chapter_queue.queue_rank, 1)
            self.assertEqual(chapter_queue.queue_priority, "immediate")
            self.assertEqual(chapter_queue.queue_driver, "active_blocking")
            self.assertTrue(chapter_queue.needs_immediate_attention)
            self.assertIsNotNone(chapter_queue.oldest_active_issue_at)
            assert chapter_queue.age_hours is not None
            self.assertGreaterEqual(chapter_queue.age_hours, 0)
            self.assertEqual(chapter_queue.age_bucket, "fresh")
            self.assertEqual(chapter_queue.sla_target_hours, 4)
            self.assertEqual(chapter_queue.sla_status, "on_track")
            self.assertTrue(chapter_queue.owner_ready)
            self.assertEqual(chapter_queue.owner_ready_reason, "clear_dominant_issue_family")
            self.assertIsNotNone(chapter_queue.latest_activity_bucket_start)
            self.assertEqual(chapter_queue.latest_created_issue_count, 1)
            self.assertEqual(chapter_queue.latest_resolved_issue_count, 0)
            self.assertEqual(chapter_queue.latest_net_issue_delta, 1)
            self.assertEqual(chapter_queue.regression_hint, "regressing")
            self.assertFalse(chapter_queue.flapping_hint)
            self.assertEqual(chapter_queue.dominant_issue_type, "ALIGNMENT_FAILURE")
            self.assertEqual(chapter_queue.dominant_root_cause_layer, "export")
            self.assertEqual(len(dashboard.issue_activity_timeline), 1)
            issue_timeline_entry = dashboard.issue_activity_timeline[0]
            self.assertEqual(issue_timeline_entry.bucket_granularity, "day")
            self.assertEqual(issue_timeline_entry.created_issue_count, 1)
            self.assertEqual(issue_timeline_entry.resolved_issue_count, 0)
            self.assertEqual(issue_timeline_entry.wontfix_issue_count, 0)
            self.assertEqual(issue_timeline_entry.blocking_created_issue_count, 1)
            self.assertEqual(issue_timeline_entry.net_issue_delta, 1)
            self.assertEqual(issue_timeline_entry.estimated_open_issue_count, 1)
            self.assertEqual(len(dashboard.issue_activity_breakdown), 1)
            breakdown_entry = dashboard.issue_activity_breakdown[0]
            self.assertEqual(breakdown_entry.issue_type, "ALIGNMENT_FAILURE")
            self.assertEqual(breakdown_entry.root_cause_layer, "export")
            self.assertEqual(breakdown_entry.issue_count, 1)
            self.assertEqual(breakdown_entry.open_issue_count, 1)
            self.assertEqual(breakdown_entry.blocking_issue_count, 1)
            self.assertIsNotNone(breakdown_entry.latest_seen_at)
            self.assertEqual(len(breakdown_entry.timeline), 1)
            self.assertEqual(breakdown_entry.timeline[0].created_issue_count, 1)
            self.assertEqual(breakdown_entry.timeline[0].resolved_issue_count, 0)
            self.assertEqual(breakdown_entry.timeline[0].net_issue_delta, 1)
            self.assertEqual(breakdown_entry.timeline[0].estimated_open_issue_count, 1)
            assert dashboard.issue_activity_highlights.top_regressing_entry is not None
            assert dashboard.issue_activity_highlights.top_blocking_entry is not None
            self.assertEqual(dashboard.issue_activity_highlights.top_regressing_entry.issue_type, "ALIGNMENT_FAILURE")
            self.assertEqual(dashboard.issue_activity_highlights.top_regressing_entry.root_cause_layer, "export")
            self.assertIsNone(dashboard.issue_activity_highlights.top_resolving_entry)
            self.assertEqual(dashboard.issue_activity_highlights.top_blocking_entry.issue_type, "ALIGNMENT_FAILURE")
            self.assertEqual(dashboard.issue_activity_highlights.top_blocking_entry.root_cause_layer, "export")

    def test_postgres_final_export_can_auto_followup_and_succeed(self) -> None:
        epub_path = self._write_epub("bilingual-auto-followup-export")

        with session_scope(self.session_factory) as session:
            service = DocumentWorkflowService(session, export_root=self.export_root)
            summary = service.bootstrap_epub(epub_path)
            document_id = summary.document_id

            service.translate_document(document_id)
            review = service.review_document(document_id)
            self.assertEqual(review.total_issue_count, 0)

            sentence_id = session.scalars(
                select(Sentence.id).where(
                    Sentence.document_id == document_id,
                    Sentence.source_text.like("Pricing power%"),
                )
            ).one()
            session.execute(delete(AlignmentEdge).where(AlignmentEdge.sentence_id == sentence_id))
            session.flush()

            export = service.export_document(
                document_id,
                ExportType.BILINGUAL_HTML,
                auto_execute_followup_on_gate=True,
            )
            self.assertTrue(export.auto_followup_requested)
            self.assertTrue(export.auto_followup_applied)
            self.assertEqual(export.auto_followup_attempt_count, 1)
            self.assertEqual(export.auto_followup_attempt_limit, 3)
            self.assertGreaterEqual(len(export.auto_followup_executions or []), 1)
            execution = export.auto_followup_executions[0]
            self.assertEqual(execution.action_type, "REALIGN_ONLY")
            self.assertTrue(execution.followup_executed)
            self.assertTrue(execution.issue_resolved)
            self.assertTrue(Path(export.chapter_results[0].file_path).exists())
            manifest = json.loads(Path(export.chapter_results[0].manifest_path).read_text(encoding="utf-8"))
            self.assertEqual(manifest["export_auto_followup_evidence"]["executed_event_count"], 1)
            self.assertEqual(manifest["export_auto_followup_evidence"]["stop_event_count"], 0)
            export_record = session.get(Export, export.chapter_results[0].export_id)
            self.assertIsNotNone(export_record)
            assert export_record is not None
            auto_followup_summary = export_record.input_version_bundle_json["export_auto_followup_summary"]
            self.assertEqual(auto_followup_summary["executed_event_count"], 1)
            self.assertEqual(auto_followup_summary["stop_event_count"], 0)
            self.assertIsNotNone(auto_followup_summary["latest_event_at"])
            self.assertIsNone(auto_followup_summary["last_stop_reason"])

            dashboard = service.get_document_export_dashboard(document_id)
            self.assertEqual(dashboard.total_auto_followup_executed_count, 1)
            bilingual_record = next(record for record in dashboard.records if record.export_type == "bilingual_html")
            assert bilingual_record.export_auto_followup_summary is not None
            self.assertEqual(bilingual_record.export_auto_followup_summary.executed_event_count, 1)
            self.assertEqual(bilingual_record.export_auto_followup_summary.stop_event_count, 0)
            self.assertEqual(len(dashboard.issue_activity_timeline), 1)
            issue_timeline_entry = dashboard.issue_activity_timeline[0]
            self.assertEqual(issue_timeline_entry.created_issue_count, 1)
            self.assertEqual(issue_timeline_entry.resolved_issue_count, 1)
            self.assertEqual(issue_timeline_entry.wontfix_issue_count, 0)
            self.assertEqual(issue_timeline_entry.blocking_created_issue_count, 1)
            self.assertEqual(issue_timeline_entry.net_issue_delta, 0)
            self.assertEqual(issue_timeline_entry.estimated_open_issue_count, 0)
            self.assertEqual(len(dashboard.issue_activity_breakdown), 1)
            breakdown_entry = dashboard.issue_activity_breakdown[0]
            self.assertEqual(breakdown_entry.issue_type, "ALIGNMENT_FAILURE")
            self.assertEqual(breakdown_entry.root_cause_layer, "export")
            self.assertEqual(breakdown_entry.issue_count, 1)
            self.assertEqual(breakdown_entry.open_issue_count, 0)
            self.assertEqual(breakdown_entry.blocking_issue_count, 1)
            self.assertEqual(len(breakdown_entry.timeline), 1)
            self.assertEqual(breakdown_entry.timeline[0].created_issue_count, 1)
            self.assertEqual(breakdown_entry.timeline[0].resolved_issue_count, 1)
            self.assertEqual(breakdown_entry.timeline[0].net_issue_delta, 0)
            self.assertEqual(breakdown_entry.timeline[0].estimated_open_issue_count, 0)
            self.assertIsNone(dashboard.issue_chapter_highlights.top_open_chapter)
            assert dashboard.issue_chapter_highlights.top_blocking_chapter is not None
            assert dashboard.issue_chapter_highlights.top_resolved_chapter is not None
            self.assertEqual(dashboard.issue_chapter_highlights.top_blocking_chapter.chapter_id, summary.chapters[0].chapter_id)
            self.assertEqual(dashboard.issue_chapter_highlights.top_resolved_chapter.chapter_id, summary.chapters[0].chapter_id)
            self.assertEqual(len(dashboard.issue_chapter_breakdown), 1)
            chapter_breakdown = dashboard.issue_chapter_breakdown[0]
            self.assertEqual(chapter_breakdown.issue_type, "ALIGNMENT_FAILURE")
            self.assertEqual(chapter_breakdown.root_cause_layer, "export")
            self.assertEqual(chapter_breakdown.open_issue_count, 0)
            self.assertEqual(chapter_breakdown.resolved_issue_count, 1)
            self.assertEqual(chapter_breakdown.blocking_issue_count, 1)
            self.assertEqual(chapter_breakdown.active_blocking_issue_count, 0)
            self.assertEqual(len(dashboard.issue_chapter_heatmap), 1)
            chapter_heatmap = dashboard.issue_chapter_heatmap[0]
            self.assertEqual(chapter_heatmap.chapter_id, summary.chapters[0].chapter_id)
            self.assertEqual(chapter_heatmap.issue_family_count, 1)
            self.assertEqual(chapter_heatmap.dominant_issue_type, "ALIGNMENT_FAILURE")
            self.assertEqual(chapter_heatmap.dominant_root_cause_layer, "export")
            self.assertEqual(chapter_heatmap.blocking_issue_count, 1)
            self.assertEqual(chapter_heatmap.active_blocking_issue_count, 0)
            self.assertEqual(chapter_heatmap.heat_score, 0)
            self.assertEqual(chapter_heatmap.heat_level, "none")
            self.assertEqual(dashboard.issue_chapter_queue, [])
            self.assertIsNone(dashboard.issue_activity_highlights.top_regressing_entry)
            assert dashboard.issue_activity_highlights.top_resolving_entry is not None
            assert dashboard.issue_activity_highlights.top_blocking_entry is not None
            self.assertEqual(dashboard.issue_activity_highlights.top_resolving_entry.issue_type, "ALIGNMENT_FAILURE")
            self.assertEqual(dashboard.issue_activity_highlights.top_resolving_entry.root_cause_layer, "export")
            self.assertEqual(dashboard.issue_activity_highlights.top_blocking_entry.issue_type, "ALIGNMENT_FAILURE")
            self.assertEqual(dashboard.issue_activity_highlights.top_blocking_entry.root_cause_layer, "export")

            restored_edge_count = session.scalar(
                select(func.count(AlignmentEdge.id)).where(AlignmentEdge.sentence_id == sentence_id)
            )
            self.assertGreaterEqual(restored_edge_count or 0, 1)
            export_issue_status = session.scalars(
                select(ReviewIssue.status).where(
                    ReviewIssue.document_id == document_id,
                    ReviewIssue.root_cause_layer == RootCauseLayer.EXPORT,
                    ReviewIssue.issue_type == "ALIGNMENT_FAILURE",
                )
            ).first()
            self.assertEqual(export_issue_status, IssueStatus.RESOLVED)

    def test_postgres_final_export_auto_followup_respects_attempt_limit(self) -> None:
        epub_path = self._write_epub("bilingual-auto-followup-cap")

        with session_scope(self.session_factory) as session:
            service = DocumentWorkflowService(session, export_root=self.export_root)
            summary = service.bootstrap_epub(epub_path)
            document_id = summary.document_id

            service.translate_document(document_id)
            review = service.review_document(document_id)
            self.assertEqual(review.total_issue_count, 0)

            sentence_ids = session.scalars(
                select(Sentence.id).where(
                    Sentence.document_id == document_id,
                    (Sentence.source_text == "Chapter One") | Sentence.source_text.like("Pricing power matters%"),
                )
            ).all()
            self.assertEqual(len(sentence_ids), 2)
            session.execute(delete(AlignmentEdge).where(AlignmentEdge.sentence_id.in_(sentence_ids)))
            session.flush()

            caught: ExportGateError | None = None
            try:
                service.export_document(
                    document_id,
                    ExportType.BILINGUAL_HTML,
                    auto_execute_followup_on_gate=True,
                    max_auto_followup_attempts=1,
                )
            except ExportGateError as exc:
                caught = exc

            self.assertIsNotNone(caught)
            assert caught is not None
            exc = caught
            self.assertTrue(exc.auto_followup_requested)
            self.assertEqual(exc.auto_followup_attempt_count, 1)
            self.assertEqual(exc.auto_followup_attempt_limit, 1)
            self.assertEqual(exc.auto_followup_stop_reason, "max_attempts_reached")
            self.assertEqual(len(exc.auto_followup_executions), 1)
            self.assertGreaterEqual(len(exc.followup_actions), 1)

            review_export = service.export_document(document_id, ExportType.REVIEW_PACKAGE)
            review_package = json.loads(Path(review_export.chapter_results[0].file_path).read_text(encoding="utf-8"))
            self.assertEqual(review_package["export_auto_followup_evidence"]["executed_event_count"], 1)
            self.assertEqual(review_package["export_auto_followup_evidence"]["stop_event_count"], 1)
            self.assertTrue(
                any(
                    event["action"] == "export.auto_followup.stopped"
                    and event["payload"]["stop_reason"] == "max_attempts_reached"
                    for event in review_package["export_auto_followup_evidence"]["events"]
                )
            )
            review_export_record = session.get(Export, review_export.chapter_results[0].export_id)
            self.assertIsNotNone(review_export_record)
            assert review_export_record is not None
            auto_followup_summary = review_export_record.input_version_bundle_json["export_auto_followup_summary"]
            self.assertEqual(auto_followup_summary["executed_event_count"], 1)
            self.assertEqual(auto_followup_summary["stop_event_count"], 1)
            self.assertEqual(auto_followup_summary["last_stop_reason"], "max_attempts_reached")

    def test_postgres_orphan_target_segment_routes_to_realign(self) -> None:
        epub_path = self._write_epub("orphan-target-segment")

        with session_scope(self.session_factory) as session:
            service = DocumentWorkflowService(
                session,
                export_root=self.export_root,
                translation_worker=SplitSegmentWorker(),
            )
            summary = service.bootstrap_epub(epub_path)
            document_id = summary.document_id
            chapter_id = summary.chapters[0].chapter_id
            service.translate_document(document_id)

            packet_id = next(
                packet.id
                for packet in session.scalars(
                    select(TranslationPacket).where(TranslationPacket.chapter_id == chapter_id)
                ).all()
                if any(len(block.get("sentence_ids", [])) >= 2 for block in packet.packet_json.get("current_blocks", []))
            )
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
            session.flush()

            review = service.review_document(document_id)
            self.assertGreaterEqual(review.total_issue_count, 1)
            issue = session.scalars(
                select(ReviewIssue)
                .where(ReviewIssue.document_id == document_id, ReviewIssue.issue_type == "ALIGNMENT_FAILURE")
                .order_by(ReviewIssue.created_at.desc())
            ).first()
            self.assertIsNotNone(issue)
            assert issue is not None
            self.assertIn(orphan_target_id, issue.evidence_json["orphan_target_segment_ids"])

            action_id = session.scalars(
                select(IssueAction.id)
                .join(ReviewIssue, IssueAction.issue_id == ReviewIssue.id)
                .where(ReviewIssue.id == issue.id)
            ).first()
            self.assertIsNotNone(action_id)

        with session_scope(self.session_factory) as session:
            service = DocumentWorkflowService(session, export_root=self.export_root)
            result = service.execute_action(action_id, run_followup=True)

            self.assertIsNotNone(result.rerun_execution)
            assert result.rerun_execution is not None
            self.assertTrue(result.rerun_execution.issue_resolved)
            self.assertEqual(result.rerun_execution.translation_run_ids, [])

    def test_postgres_run_control_create_summary_and_events(self) -> None:
        epub_path = self._write_epub("run-control")

        with session_scope(self.session_factory) as session:
            workflow_service = DocumentWorkflowService(session, export_root=self.export_root)
            summary = workflow_service.bootstrap_epub(epub_path)
            document_id = summary.document_id

            run_control = RunControlService(RunControlRepository(session))
            run = run_control.create_run(
                document_id=document_id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                requested_by="pg-ops-user",
                backend="openai_compatible",
                model_name="deepseek-chat",
                budget=RunBudgetSummary(
                    max_wall_clock_seconds=1800,
                    max_total_cost_usd=10.0,
                    max_total_token_in=None,
                    max_total_token_out=None,
                    max_retry_count_per_work_item=2,
                    max_consecutive_failures=5,
                    max_parallel_workers=2,
                    max_parallel_requests_per_provider=2,
                    max_auto_followup_attempts=1,
                ),
            )
            work_item = WorkItem(
                run_id=run.run_id,
                stage=WorkItemStage.TRANSLATE,
                scope_type=WorkItemScopeType.PACKET,
                scope_id=document_id,
                priority=50,
                status=WorkItemStatus.RUNNING,
                lease_owner="pg-worker-1",
                input_version_bundle_json={"packet_id": "pkt-1"},
                started_at=datetime.now(timezone.utc) - timedelta(minutes=3),
            )
            session.add(work_item)
            session.flush()
            session.add(
                WorkerLease(
                    run_id=run.run_id,
                    work_item_id=work_item.id,
                    worker_name="translate-worker",
                    worker_instance_id="pg-worker-1",
                    lease_token=f"lease-{uuid4()}",
                    status=WorkerLeaseStatus.ACTIVE,
                    lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
                    last_heartbeat_at=datetime.now(timezone.utc),
                )
            )
            session.add(
                RunAuditEvent(
                    run_id=run.run_id,
                    work_item_id=work_item.id,
                    event_type="work_item.started",
                    actor_type=ActorType.SYSTEM,
                    actor_id="pg-worker-1",
                    payload_json={"stage": "translate"},
                )
            )
            session.flush()

            paused = run_control.pause_run(run.run_id, actor_id="pg-ops-user", note="pause for smoke")
            self.assertEqual(paused.status, "paused")

            refreshed = run_control.get_run_summary(run.run_id)
            events = run_control.get_run_events(run.run_id, limit=10, offset=0)

            self.assertEqual(refreshed.work_items.total_count, 1)
            self.assertEqual(refreshed.work_items.status_counts["running"], 1)
            self.assertEqual(refreshed.worker_leases.status_counts["active"], 1)
            self.assertEqual(refreshed.events.event_count, 3)
            self.assertEqual(events.entries[0].event_type, "run.paused")

    def test_postgres_run_execution_claim_success_and_terminal_reconcile(self) -> None:
        epub_path = self._write_epub("run-execution")

        with session_scope(self.session_factory) as session:
            workflow_service = DocumentWorkflowService(session, export_root=self.export_root)
            summary = workflow_service.bootstrap_epub(epub_path)
            document_id = summary.document_id

            repository = RunControlRepository(session)
            run_control = RunControlService(repository)
            execution = RunExecutionService(repository, run_control)
            run = run_control.create_run(
                document_id=document_id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                requested_by="pg-runner",
                budget=RunBudgetSummary(
                    max_wall_clock_seconds=1800,
                    max_total_cost_usd=5.0,
                    max_total_token_in=None,
                    max_total_token_out=None,
                    max_retry_count_per_work_item=1,
                    max_consecutive_failures=3,
                    max_parallel_workers=1,
                    max_parallel_requests_per_provider=1,
                    max_auto_followup_attempts=1,
                ),
            )
            resumed = run_control.resume_run(run.run_id, actor_id="pg-runner", note="start execution smoke")

            packet_id = str(uuid4())
            seeded = execution.seed_translate_work_items(run_id=resumed.run_id, packet_ids=[packet_id])
            self.assertEqual(len(seeded), 1)

            claimed = execution.claim_next_translate_work_item(
                run_id=resumed.run_id,
                worker_name="pg.translate",
                worker_instance_id="pg-worker-1",
                lease_seconds=60,
            )
            self.assertIsNotNone(claimed)
            assert claimed is not None

            execution.start_work_item(lease_token=claimed.lease_token, lease_seconds=60)
            execution.complete_translate_success(
                lease_token=claimed.lease_token,
                packet_id=packet_id,
                translation_run_id=str(uuid4()),
                token_in=64,
                token_out=32,
                cost_usd=0.0012,
                latency_ms=220,
            )
            final_summary = execution.reconcile_run_terminal_state(run_id=resumed.run_id)

            self.assertEqual(final_summary.status, "succeeded")
            self.assertEqual(final_summary.work_items.status_counts["succeeded"], 1)
            self.assertEqual(final_summary.status_detail_json["usage_summary"]["token_in"], 64)
            self.assertEqual(final_summary.status_detail_json["usage_summary"]["token_out"], 32)


if __name__ == "__main__":
    unittest.main()
