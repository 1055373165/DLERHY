import tempfile
import unittest
import zipfile
from pathlib import Path
import sys

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.enums import ChapterStatus, MemoryProposalStatus, MemoryScopeType, MemoryStatus, PacketStatus, SnapshotType
from book_agent.domain.models import Chapter, ChapterMemoryProposal, MemorySnapshot
from book_agent.domain.models.translation import TranslationPacket, TranslationRun
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.infra.repositories.chapter_memory import ChapterTranslationMemoryRepository
from book_agent.infra.repositories.review import ReviewRepository
from book_agent.infra.repositories.translation import TranslationRepository
from book_agent.orchestrator.bootstrap import BootstrapOrchestrator
from book_agent.services.context_compile import ChapterContextCompiler
from book_agent.services.memory_service import MemoryService
from book_agent.services.review import ReviewService
from book_agent.services.translation import TranslationService
from book_agent.services.workflows import DocumentWorkflowService
from book_agent.workers.contracts import (
    AlignmentSuggestion,
    CompiledTranslationContext,
    TranslationTargetSegment,
    TranslationWorkerOutput,
)
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
    <dc:title>Context Engineering Notes</dc:title>
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
    <p>Context engineering determines how context is created.</p>
    <p>Context engineering also determines how context is maintained.</p>
  </body>
</html>
"""


class CapturingWorker:
    def __init__(self) -> None:
        self.contexts: list[CompiledTranslationContext] = []

    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(
            worker_name="capturing-worker",
            model_name="capture-model",
            prompt_version="capture-v1",
        )

    def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
        assert isinstance(task.context_packet, CompiledTranslationContext)
        self.contexts.append(task.context_packet)
        source_sentence_ids = [sentence.id for sentence in task.current_sentences]
        return TranslationWorkerOutput(
            packet_id=task.context_packet.packet_id,
            target_segments=[
                TranslationTargetSegment(
                    temp_id="temp-1",
                    text_zh="上下文工程决定如何创建上下文。",
                    segment_type="sentence",
                    source_sentence_ids=source_sentence_ids,
                    confidence=0.93,
                )
            ],
            alignment_suggestions=[
                AlignmentSuggestion(
                    source_sentence_ids=source_sentence_ids,
                    target_temp_ids=["temp-1"],
                    relation_type="1:1",
                    confidence=0.92,
                )
            ],
        )


class MemoryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _bootstrap_to_db(self) -> tuple[str, list[str]]:
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
        packet_ids = [packet.id for packet in artifacts.translation_packets]
        return artifacts.document.id, packet_ids

    def _seed_chapter_memory_snapshot(self, *, document_id: str, chapter_id: str, version: int = 2) -> None:
        with self.session_factory() as session:
            snapshot = MemorySnapshot(
                document_id=document_id,
                scope_type=MemoryScopeType.CHAPTER,
                scope_id=chapter_id,
                snapshot_type=SnapshotType.CHAPTER_TRANSLATION_MEMORY,
                version=version,
                content_json={
                    "schema_version": 1,
                    "chapter_id": chapter_id,
                    "chapter_brief": "本节讨论 context engineering 如何组织上下文。",
                    "chapter_brief_version": 1,
                    "active_concepts": [
                        {
                            "source_term": "context engineering",
                            "canonical_zh": "上下文工程",
                            "status": "locked",
                            "times_seen": 2,
                        }
                    ],
                    "recent_accepted_translations": [
                        {
                            "packet_id": "prev-packet",
                            "block_id": "prev-block",
                            "source_excerpt": "Context engineering shapes reasoning.",
                            "target_excerpt": "上下文工程塑造推理过程。",
                            "source_sentence_ids": ["prev-sentence"],
                        }
                    ],
                },
                status=MemoryStatus.ACTIVE,
            )
            session.add(snapshot)
            session.commit()

    def _find_packet_with_text(self, packet_ids: list[str], needle: str) -> str:
        with self.session_factory() as session:
            repository = TranslationRepository(session)
            for packet_id in packet_ids:
                bundle = repository.load_packet_bundle(packet_id)
                merged = " ".join(block.text for block in bundle.context_packet.current_blocks)
                if needle in merged:
                    return packet_id
        raise AssertionError(f"Could not find packet containing text: {needle}")

    def test_load_compiled_context_returns_explicit_compiled_metadata(self) -> None:
        document_id, packet_ids = self._bootstrap_to_db()
        target_packet_id = self._find_packet_with_text(packet_ids, "Context engineering")

        with self.session_factory() as session:
            chapter = session.scalars(select(Chapter).where(Chapter.document_id == document_id)).first()
            assert chapter is not None
            self._seed_chapter_memory_snapshot(document_id=document_id, chapter_id=chapter.id)

        with self.session_factory() as session:
            repository = TranslationRepository(session)
            bundle = repository.load_packet_bundle(target_packet_id)
            memory_service = MemoryService(
                chapter_memory_repository=ChapterTranslationMemoryRepository(session),
                context_compiler=ChapterContextCompiler(),
            )

            result = memory_service.load_compiled_context(
                packet=bundle.context_packet,
                rerun_hints=("Preserve locked term usage.",),
            )

        self.assertIsInstance(result.context, CompiledTranslationContext)
        self.assertEqual(result.context.memory_version_used, 2)
        self.assertEqual(result.context.context_compile_version, ChapterContextCompiler().compile_version)
        self.assertIn("Preserve locked term usage.", result.context.open_questions)
        self.assertTrue(result.context.compile_metadata["chapter_memory_available"])
        self.assertEqual(result.context.compile_metadata["rerun_hint_count"], 1)
        self.assertTrue(
            any(
                concept.source_term == "context engineering" and concept.canonical_zh == "上下文工程"
                for concept in result.context.chapter_concepts
            )
        )
        self.assertIsNotNone(result.chapter_memory_snapshot)
        self.assertEqual(result.chapter_memory_snapshot.version, 2)

    def test_translation_service_uses_compiled_context_metadata(self) -> None:
        document_id, packet_ids = self._bootstrap_to_db()
        target_packet_id = self._find_packet_with_text(packet_ids, "Context engineering")

        with self.session_factory() as session:
            chapter = session.scalars(select(Chapter).where(Chapter.document_id == document_id)).first()
            assert chapter is not None
            self._seed_chapter_memory_snapshot(document_id=document_id, chapter_id=chapter.id)

        worker = CapturingWorker()
        with self.session_factory() as session:
            repository = TranslationRepository(session)
            service = TranslationService(repository, worker=worker)
            service.execute_packet(target_packet_id, rerun_hints=("Resolve context engineering consistently.",))
            session.commit()

            translation_run = session.scalars(select(TranslationRun).order_by(TranslationRun.created_at.desc())).first()

        self.assertEqual(len(worker.contexts), 1)
        compiled_context = worker.contexts[0]
        self.assertIsInstance(compiled_context, CompiledTranslationContext)
        self.assertEqual(compiled_context.memory_version_used, 2)
        self.assertIn("Resolve context engineering consistently.", compiled_context.open_questions)
        self.assertIsNotNone(translation_run)
        assert translation_run is not None
        self.assertEqual(
            translation_run.model_config_json["context_compile_version"],
            ChapterContextCompiler().compile_version,
        )
        self.assertEqual(translation_run.model_config_json["chapter_memory_snapshot_version_used"], 2)
        self.assertTrue(translation_run.model_config_json["compiled_context_metadata"]["chapter_memory_available"])

    def test_memory_service_records_proposal_before_commit_and_can_finalize_it(self) -> None:
        document_id, packet_ids = self._bootstrap_to_db()
        target_packet_id = self._find_packet_with_text(packet_ids, "Context engineering")

        with self.session_factory() as session:
            chapter = session.scalars(select(Chapter).where(Chapter.document_id == document_id)).first()
            assert chapter is not None
            self._seed_chapter_memory_snapshot(document_id=document_id, chapter_id=chapter.id)

        worker = CapturingWorker()
        with self.session_factory() as session:
            repository = TranslationRepository(session)
            memory_service = MemoryService(
                chapter_memory_repository=ChapterTranslationMemoryRepository(session),
                context_compiler=ChapterContextCompiler(),
            )
            service = TranslationService(
                repository,
                worker=worker,
                memory_service=memory_service,
            )
            artifacts = service.execute_packet(target_packet_id, auto_commit_memory=False)
            session.flush()

            proposal = session.scalars(
                select(ChapterMemoryProposal).where(
                    ChapterMemoryProposal.translation_run_id == artifacts.translation_run.id
                )
            ).first()
            latest_snapshot = session.scalars(
                select(MemorySnapshot)
                .where(
                    MemorySnapshot.document_id == document_id,
                    MemorySnapshot.scope_type == MemoryScopeType.CHAPTER,
                    MemorySnapshot.scope_id == chapter.id,
                    MemorySnapshot.snapshot_type == SnapshotType.CHAPTER_TRANSLATION_MEMORY,
                    MemorySnapshot.status == MemoryStatus.ACTIVE,
                )
                .order_by(MemorySnapshot.version.desc())
            ).first()

            self.assertIsNotNone(proposal)
            self.assertIsNotNone(latest_snapshot)
            assert proposal is not None
            assert latest_snapshot is not None
            self.assertEqual(proposal.status, MemoryProposalStatus.PROPOSED)
            self.assertEqual(proposal.base_snapshot_version, 2)
            self.assertEqual(latest_snapshot.version, 2)
            self.assertEqual(
                proposal.proposed_content_json["last_translation_run_id"],
                artifacts.translation_run.id,
            )
            self.assertGreaterEqual(
                len(proposal.proposed_content_json["recent_accepted_translations"]),
                1,
            )

            committed_snapshot = memory_service.commit_approved_packet_memory(
                document_id=document_id,
                chapter_id=chapter.id,
                translation_run_id=artifacts.translation_run.id,
            )
            session.commit()
            committed_snapshot_id = committed_snapshot.id

        with self.session_factory() as session:
            proposal = session.scalars(
                select(ChapterMemoryProposal).where(
                    ChapterMemoryProposal.translation_run_id == artifacts.translation_run.id
                )
            ).first()
            latest_snapshot = session.scalars(
                select(MemorySnapshot)
                .where(
                    MemorySnapshot.document_id == document_id,
                    MemorySnapshot.scope_type == MemoryScopeType.CHAPTER,
                    MemorySnapshot.scope_id == chapter.id,
                    MemorySnapshot.snapshot_type == SnapshotType.CHAPTER_TRANSLATION_MEMORY,
                    MemorySnapshot.status == MemoryStatus.ACTIVE,
                )
                .order_by(MemorySnapshot.version.desc())
            ).first()

            self.assertIsNotNone(proposal)
            self.assertIsNotNone(latest_snapshot)
            assert proposal is not None
            assert latest_snapshot is not None
            self.assertEqual(proposal.status, MemoryProposalStatus.COMMITTED)
            self.assertEqual(proposal.committed_snapshot_id, committed_snapshot_id)
            self.assertEqual(latest_snapshot.id, committed_snapshot_id)
            self.assertEqual(latest_snapshot.version, 3)
            self.assertEqual(
                latest_snapshot.content_json["last_translation_run_id"],
                artifacts.translation_run.id,
            )
            self.assertGreaterEqual(
                len(latest_snapshot.content_json["recent_accepted_translations"]),
                1,
            )

    def test_translation_service_defaults_to_proposal_first_memory(self) -> None:
        document_id, packet_ids = self._bootstrap_to_db()
        target_packet_id = self._find_packet_with_text(packet_ids, "Context engineering")

        with self.session_factory() as session:
            chapter = session.scalars(select(Chapter).where(Chapter.document_id == document_id)).first()
            assert chapter is not None
            self._seed_chapter_memory_snapshot(document_id=document_id, chapter_id=chapter.id)

            service = TranslationService(TranslationRepository(session))
            artifacts = service.execute_packet(target_packet_id)
            session.commit()

            proposal = session.scalars(
                select(ChapterMemoryProposal).where(
                    ChapterMemoryProposal.translation_run_id == artifacts.translation_run.id
                )
            ).first()
            latest_snapshot = session.scalars(
                select(MemorySnapshot)
                .where(
                    MemorySnapshot.document_id == document_id,
                    MemorySnapshot.scope_type == MemoryScopeType.CHAPTER,
                    MemorySnapshot.scope_id == chapter.id,
                    MemorySnapshot.snapshot_type == SnapshotType.CHAPTER_TRANSLATION_MEMORY,
                    MemorySnapshot.status == MemoryStatus.ACTIVE,
                )
                .order_by(MemorySnapshot.version.desc())
            ).first()

            self.assertFalse(service.default_auto_commit_memory)
            self.assertIsNotNone(proposal)
            self.assertIsNotNone(latest_snapshot)
            assert proposal is not None
            assert latest_snapshot is not None
            self.assertEqual(proposal.status, MemoryProposalStatus.PROPOSED)
            self.assertEqual(latest_snapshot.version, 2)

    def test_newer_packet_proposal_retires_older_pending_proposal(self) -> None:
        document_id, packet_ids = self._bootstrap_to_db()
        target_packet_id = self._find_packet_with_text(packet_ids, "Context engineering")

        with self.session_factory() as session:
            chapter = session.scalars(select(Chapter).where(Chapter.document_id == document_id)).first()
            assert chapter is not None
            self._seed_chapter_memory_snapshot(document_id=document_id, chapter_id=chapter.id)

            repository = TranslationRepository(session)
            service = TranslationService(repository)

            first_artifacts = service.execute_packet(target_packet_id)
            packet = repository.session.get(TranslationPacket, target_packet_id)
            assert packet is not None
            packet.status = PacketStatus.BUILT
            repository.session.flush()

            second_artifacts = service.execute_packet(target_packet_id)
            session.commit()

            proposals = session.scalars(
                select(ChapterMemoryProposal)
                .where(ChapterMemoryProposal.packet_id == target_packet_id)
                .order_by(ChapterMemoryProposal.created_at.asc())
            ).all()
            latest_snapshot = session.scalars(
                select(MemorySnapshot)
                .where(
                    MemorySnapshot.document_id == document_id,
                    MemorySnapshot.scope_type == MemoryScopeType.CHAPTER,
                    MemorySnapshot.scope_id == chapter.id,
                    MemorySnapshot.snapshot_type == SnapshotType.CHAPTER_TRANSLATION_MEMORY,
                    MemorySnapshot.status == MemoryStatus.ACTIVE,
                )
                .order_by(MemorySnapshot.version.desc())
            ).first()

            self.assertEqual(first_artifacts.translation_run.attempt, 1)
            self.assertEqual(second_artifacts.translation_run.attempt, 2)
            self.assertEqual(len(proposals), 2)
            self.assertEqual(proposals[0].translation_run_id, first_artifacts.translation_run.id)
            self.assertEqual(proposals[0].status, MemoryProposalStatus.REJECTED)
            self.assertEqual(proposals[1].translation_run_id, second_artifacts.translation_run.id)
            self.assertEqual(proposals[1].status, MemoryProposalStatus.PROPOSED)
            self.assertIsNotNone(latest_snapshot)
            assert latest_snapshot is not None
            self.assertEqual(latest_snapshot.version, 2)

    def test_review_pass_commits_pending_chapter_memory_proposals_in_packet_order(self) -> None:
        document_id, packet_ids = self._bootstrap_to_db()

        with self.session_factory() as session:
            chapter = session.scalars(select(Chapter).where(Chapter.document_id == document_id)).first()
            assert chapter is not None
            self._seed_chapter_memory_snapshot(document_id=document_id, chapter_id=chapter.id)

        with self.session_factory() as session:
            repository = TranslationRepository(session)
            memory_service = MemoryService(
                chapter_memory_repository=ChapterTranslationMemoryRepository(session),
                context_compiler=ChapterContextCompiler(),
            )
            service = TranslationService(
                repository,
                memory_service=memory_service,
            )
            for packet_id in packet_ids:
                service.execute_packet(packet_id, auto_commit_memory=False)
            session.commit()

        with self.session_factory() as session:
            chapter = session.scalars(select(Chapter).where(Chapter.document_id == document_id)).first()
            proposals = session.scalars(
                select(ChapterMemoryProposal)
                .where(ChapterMemoryProposal.chapter_id == chapter.id)
                .order_by(ChapterMemoryProposal.created_at.asc())
            ).all()
            latest_snapshot = session.scalars(
                select(MemorySnapshot)
                .where(
                    MemorySnapshot.document_id == document_id,
                    MemorySnapshot.scope_type == MemoryScopeType.CHAPTER,
                    MemorySnapshot.scope_id == chapter.id,
                    MemorySnapshot.snapshot_type == SnapshotType.CHAPTER_TRANSLATION_MEMORY,
                    MemorySnapshot.status == MemoryStatus.ACTIVE,
                )
                .order_by(MemorySnapshot.version.desc())
            ).first()

            self.assertEqual(len(proposals), 2)
            self.assertTrue(all(proposal.status == MemoryProposalStatus.PROPOSED for proposal in proposals))
            self.assertIsNotNone(latest_snapshot)
            assert latest_snapshot is not None
            self.assertEqual(latest_snapshot.version, 2)

            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter.id)
            session.commit()

            self.assertEqual(review_artifacts.summary.blocking_issue_count, 0)

        with self.session_factory() as session:
            chapter = session.scalars(select(Chapter).where(Chapter.document_id == document_id)).first()
            proposals = session.scalars(
                select(ChapterMemoryProposal)
                .where(ChapterMemoryProposal.chapter_id == chapter.id)
                .order_by(ChapterMemoryProposal.created_at.asc())
            ).all()
            latest_snapshot = session.scalars(
                select(MemorySnapshot)
                .where(
                    MemorySnapshot.document_id == document_id,
                    MemorySnapshot.scope_type == MemoryScopeType.CHAPTER,
                    MemorySnapshot.scope_id == chapter.id,
                    MemorySnapshot.snapshot_type == SnapshotType.CHAPTER_TRANSLATION_MEMORY,
                    MemorySnapshot.status == MemoryStatus.ACTIVE,
                )
                .order_by(MemorySnapshot.version.desc())
            ).first()

            self.assertEqual(chapter.status, ChapterStatus.QA_CHECKED)
            self.assertTrue(all(proposal.status == MemoryProposalStatus.COMMITTED for proposal in proposals))
            self.assertIsNotNone(latest_snapshot)
            assert latest_snapshot is not None
            self.assertEqual(latest_snapshot.version, 4)
            self.assertEqual(
                latest_snapshot.content_json["recent_accepted_translations"][0]["packet_id"],
                "prev-packet",
            )
            self.assertEqual(
                [item["packet_id"] for item in latest_snapshot.content_json["recent_accepted_translations"][-2:]],
                packet_ids,
            )
            self.assertEqual(
                latest_snapshot.content_json["last_translation_run_id"],
                proposals[-1].translation_run_id,
            )

    def test_document_workflow_translation_is_proposal_first_until_review(self) -> None:
        document_id, _packet_ids = self._bootstrap_to_db()

        with self.session_factory() as session:
            chapter = session.scalars(select(Chapter).where(Chapter.document_id == document_id)).first()
            assert chapter is not None
            self._seed_chapter_memory_snapshot(document_id=document_id, chapter_id=chapter.id)

        with self.session_factory() as session:
            workflow = DocumentWorkflowService(session)
            translate_result = workflow.translate_document(document_id)
            session.commit()

            chapter = session.scalars(select(Chapter).where(Chapter.document_id == document_id)).first()
            proposals = session.scalars(
                select(ChapterMemoryProposal)
                .where(ChapterMemoryProposal.chapter_id == chapter.id)
                .order_by(ChapterMemoryProposal.created_at.asc())
            ).all()
            latest_snapshot = session.scalars(
                select(MemorySnapshot)
                .where(
                    MemorySnapshot.document_id == document_id,
                    MemorySnapshot.scope_type == MemoryScopeType.CHAPTER,
                    MemorySnapshot.scope_id == chapter.id,
                    MemorySnapshot.snapshot_type == SnapshotType.CHAPTER_TRANSLATION_MEMORY,
                    MemorySnapshot.status == MemoryStatus.ACTIVE,
                )
                .order_by(MemorySnapshot.version.desc())
            ).first()

            self.assertEqual(chapter.status, ChapterStatus.TRANSLATED)
            self.assertGreaterEqual(len(proposals), 1)
            self.assertTrue(all(proposal.status == MemoryProposalStatus.PROPOSED for proposal in proposals))
            self.assertIsNotNone(latest_snapshot)
            assert latest_snapshot is not None
            self.assertEqual(latest_snapshot.version, 2)
            self.assertEqual(translate_result.memory_commit_mode, "proposal_first")
            self.assertEqual(translate_result.recorded_memory_proposal_count, translate_result.translated_packet_count)

            review = workflow.review_document(document_id)
            session.commit()

            self.assertTrue(review.chapter_results)
            self.assertEqual(review.chapter_results[0].status, ChapterStatus.QA_CHECKED.value)

        with self.session_factory() as session:
            chapter = session.scalars(select(Chapter).where(Chapter.document_id == document_id)).first()
            proposals = session.scalars(
                select(ChapterMemoryProposal)
                .where(ChapterMemoryProposal.chapter_id == chapter.id)
                .order_by(ChapterMemoryProposal.created_at.asc())
            ).all()
            latest_snapshot = session.scalars(
                select(MemorySnapshot)
                .where(
                    MemorySnapshot.document_id == document_id,
                    MemorySnapshot.scope_type == MemoryScopeType.CHAPTER,
                    MemorySnapshot.scope_id == chapter.id,
                    MemorySnapshot.snapshot_type == SnapshotType.CHAPTER_TRANSLATION_MEMORY,
                    MemorySnapshot.status == MemoryStatus.ACTIVE,
                )
                .order_by(MemorySnapshot.version.desc())
            ).first()

            self.assertEqual(chapter.status, ChapterStatus.QA_CHECKED)
            self.assertTrue(all(proposal.status == MemoryProposalStatus.COMMITTED for proposal in proposals))
            self.assertIsNotNone(latest_snapshot)
            assert latest_snapshot is not None
            self.assertGreater(latest_snapshot.version, 2)


if __name__ == "__main__":
    unittest.main()
