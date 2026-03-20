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

from book_agent.domain.enums import MemoryScopeType, MemoryStatus, SnapshotType
from book_agent.domain.models import Chapter, MemorySnapshot
from book_agent.domain.models.translation import TranslationRun
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.infra.repositories.chapter_memory import ChapterTranslationMemoryRepository
from book_agent.infra.repositories.translation import TranslationRepository
from book_agent.orchestrator.bootstrap import BootstrapOrchestrator
from book_agent.services.context_compile import ChapterContextCompiler
from book_agent.services.memory_service import MemoryService
from book_agent.services.translation import TranslationService
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


if __name__ == "__main__":
    unittest.main()
