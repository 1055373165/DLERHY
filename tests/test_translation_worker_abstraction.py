import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from unittest.mock import patch
from pathlib import Path
import sys

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.enums import (
    ArtifactStatus,
    BlockType,
    ChapterStatus,
    DocumentStatus,
    MemoryStatus,
    MemoryScopeType,
    PacketSentenceRole,
    PacketStatus,
    PacketType,
    ProtectedPolicy,
    SnapshotType,
    SentenceStatus,
    SourceType,
)
from book_agent.domain.models import Block, Chapter, Document, MemorySnapshot, Sentence
from book_agent.domain.models.translation import PacketSentenceMap, TranslationPacket
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.infra.repositories.translation import TranslationRepository
from book_agent.orchestrator.bootstrap import BootstrapOrchestrator
from book_agent.core.config import Settings
from book_agent.services.packet_experiment import PacketExperimentOptions, PacketExperimentService
from book_agent.services.packet_experiment_diff import compare_experiment_payloads
from book_agent.services.packet_experiment_scan import PacketExperimentScanService
from book_agent.services.chapter_memory_backfill import ChapterMemoryBackfillService
from book_agent.services.chapter_concept_lock import ChapterConceptLockService
from book_agent.services.context_compile import ChapterContextCompileOptions, ChapterContextCompiler
from book_agent.services.translation import TranslationService
from book_agent.workers.factory import build_translation_worker
from book_agent.workers.contracts import (
    AlignmentSuggestion,
    ConceptCandidate,
    ContextPacket,
    PacketBlock,
    TranslationTargetSegment,
    TranslationWorkerOutput,
    TranslationWorkerResult,
)
from book_agent.workers.providers.openai_compatible import OpenAICompatibleTranslationClient
from book_agent.workers.translator import (
    LLMTranslationWorker,
    TranslationPromptRequest,
    TranslationTask,
    build_translation_prompt_request,
)


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
    <p>The solution to this problem is context engineering.</p>
    <p>Context engineering is a discipline.</p>
    <blockquote>A quoted paragraph.</blockquote>
  </body>
</html>
"""


class FakeTranslationClient:
    def __init__(self) -> None:
        self.requests: list[TranslationPromptRequest] = []
        self.source_sentence_ids: list[str] = ["placeholder"]

    def generate_translation(self, request: TranslationPromptRequest) -> TranslationWorkerOutput:
        self.requests.append(request)
        return TranslationWorkerOutput(
            packet_id=request.packet_id,
            target_segments=[
                TranslationTargetSegment(
                    temp_id="temp-1",
                    text_zh="模拟译文",
                    segment_type="sentence",
                    source_sentence_ids=self.source_sentence_ids,
                    confidence=0.88,
                )
            ],
            alignment_suggestions=[
                AlignmentSuggestion(
                    source_sentence_ids=self.source_sentence_ids,
                    target_temp_ids=["temp-1"],
                    relation_type="1:1",
                    confidence=0.9,
                )
            ],
        )


class FakeJSONTransport:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def post_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout_seconds: int,
    ) -> dict:
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "payload": payload,
                "timeout_seconds": timeout_seconds,
            }
        )
        return self.response


class LooseSchemaClient:
    def __init__(self, source_sentence_ids: list[str]) -> None:
        self.source_sentence_ids = source_sentence_ids

    def generate_translation(self, request: TranslationPromptRequest) -> TranslationWorkerOutput:
        return TranslationWorkerOutput(
            packet_id=request.packet_id,
            target_segments=[
                TranslationTargetSegment(
                    temp_id="temp-1",
                    text_zh="宽松结构译文",
                    segment_type="translation",
                    source_sentence_ids=self.source_sentence_ids,
                    confidence=0.9,
                )
            ],
            alignment_suggestions=[
                AlignmentSuggestion(
                    source_sentence_ids=self.source_sentence_ids,
                    target_temp_ids=["temp-1"],
                    relation_type="one_to_one",
                    confidence=0.9,
                )
            ],
        )


class TranslationWorkerAbstractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _bootstrap_to_db(self) -> tuple[str, list[str]]:
        return self._bootstrap_custom_chapter(CHAPTER_XHTML)

    def _bootstrap_custom_chapter(self, chapter_xhtml: str) -> tuple[str, list[str]]:
        with tempfile.TemporaryDirectory() as tmpdir:
            epub_path = Path(tmpdir) / "sample.epub"
            with zipfile.ZipFile(epub_path, "w") as archive:
                archive.writestr("mimetype", "application/epub+zip")
                archive.writestr("META-INF/container.xml", CONTAINER_XML)
                archive.writestr("OEBPS/content.opf", CONTENT_OPF)
                archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
                archive.writestr("OEBPS/chapter1.xhtml", chapter_xhtml)

            artifacts = BootstrapOrchestrator().bootstrap_epub(epub_path)

        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()
        packet_ids = [packet.id for packet in artifacts.translation_packets]
        return artifacts.document.id, packet_ids

    def test_llm_worker_metadata_and_prompt_are_wired(self) -> None:
        _, packet_ids = self._bootstrap_to_db()
        fake_client = FakeTranslationClient()
        worker = LLMTranslationWorker(
            fake_client,
            model_name="mock-llm",
            prompt_version="p0.llm.v1",
            runtime_config={"provider": "fake"},
        )

        with self.session_factory() as session:
            repository = TranslationRepository(session)
            heading_packet_id, first_paragraph_packet_id, second_paragraph_packet_id = packet_ids[:3]
            TranslationService(repository).execute_packet(heading_packet_id)
            TranslationService(repository).execute_packet(first_paragraph_packet_id)
            session.flush()

            bundle = repository.load_packet_bundle(second_paragraph_packet_id)
            first_sentence_id = bundle.current_sentences[0].id
            fake_client.source_sentence_ids = ["S1"]
            service = TranslationService(repository, worker=worker)
            artifacts = service.execute_packet(second_paragraph_packet_id)
            session.commit()

            self.assertEqual(artifacts.translation_run.model_name, "mock-llm")
            self.assertEqual(artifacts.translation_run.prompt_version, "p0.llm.v1")
            self.assertEqual(artifacts.translation_run.model_config_json["provider"], "fake")
            self.assertEqual(artifacts.alignment_edges[0].sentence_id, first_sentence_id)

        self.assertEqual(len(fake_client.requests), 1)
        self.assertIn("Context engineering is a discipline.", fake_client.requests[0].user_prompt)
        self.assertIn("Chapter Concept Memory:", fake_client.requests[0].user_prompt)
        self.assertIn("context engineering =>", fake_client.requests[0].user_prompt.lower())
        self.assertIn("The solution to this problem is context engineering. => ZH::The solution to this problem is context engineering.", fake_client.requests[0].user_prompt)
        self.assertIn("[S1]", fake_client.requests[0].user_prompt)
        self.assertNotIn(first_sentence_id, fake_client.requests[0].user_prompt)
        self.assertNotIn("Packet ID:", fake_client.requests[0].user_prompt)
        self.assertEqual(fake_client.requests[0].sentence_alias_map["S1"], first_sentence_id)
        self.assertIn("Current Paragraph:", fake_client.requests[0].user_prompt)
        self.assertIn("Sentence Ledger:", fake_client.requests[0].user_prompt)
        self.assertIn("Return JSON that matches the provided response schema.", fake_client.requests[0].user_prompt)

    def test_load_packet_bundle_restores_current_sentence_order_by_block_ordinal(self) -> None:
        document_id = "11111111-1111-1111-1111-111111111111"
        chapter_id = "22222222-2222-2222-2222-222222222222"
        block_id = "33333333-3333-3333-3333-333333333333"
        packet_id = "44444444-4444-4444-4444-444444444444"
        sentence_ids = [
            "ffffffff-ffff-ffff-ffff-fffffffffff1",
            "00000000-0000-0000-0000-000000000002",
            "88888888-8888-8888-8888-888888888883",
        ]
        sentence_texts = [
            "First sentence establishes the point.",
            "Second sentence develops the argument.",
            "Third sentence closes the paragraph.",
        ]

        with self.session_factory() as session:
            session.add(
                Document(
                    id=document_id,
                    source_type=SourceType.EPUB,
                    file_fingerprint="fingerprint-1",
                    source_path="/tmp/sample.epub",
                    title="Packet Order Test",
                    author="Tester",
                    src_lang="en",
                    tgt_lang="zh",
                    status=DocumentStatus.ACTIVE,
                    parser_version=1,
                    segmentation_version=1,
                    active_book_profile_version=1,
                    metadata_json={},
                )
            )
            session.commit()
            session.add(
                Chapter(
                    id=chapter_id,
                    document_id=document_id,
                    ordinal=1,
                    title_src="Chapter One",
                    title_tgt=None,
                    anchor_start=None,
                    anchor_end=None,
                    status=ChapterStatus.PACKET_BUILT,
                    summary_version=1,
                    risk_level=None,
                    metadata_json={},
                )
            )
            session.commit()
            session.add(
                Block(
                    id=block_id,
                    chapter_id=chapter_id,
                    ordinal=1,
                    block_type=BlockType.PARAGRAPH,
                    source_text=" ".join(sentence_texts),
                    normalized_text=" ".join(sentence_texts),
                    source_anchor=None,
                    source_span_json={},
                    parse_confidence=1.0,
                    protected_policy=ProtectedPolicy.TRANSLATE,
                    status=ArtifactStatus.ACTIVE,
                )
            )
            session.commit()
            for ordinal, (sentence_id, text) in enumerate(zip(sentence_ids, sentence_texts, strict=True), start=1):
                session.add(
                    Sentence(
                        id=sentence_id,
                        block_id=block_id,
                        chapter_id=chapter_id,
                        document_id=document_id,
                        ordinal_in_block=ordinal,
                        source_text=text,
                        normalized_text=text,
                        source_lang="en",
                        translatable=True,
                        nontranslatable_reason=None,
                        source_anchor=None,
                        source_span_json={},
                        upstream_confidence=1.0,
                        sentence_status=SentenceStatus.PENDING,
                        active_version=1,
                    )
                )
            session.commit()

            context_packet = ContextPacket(
                packet_id=packet_id,
                document_id=document_id,
                chapter_id=chapter_id,
                packet_type="translate",
                book_profile_version=1,
                chapter_brief_version=1,
                heading_path=["Chapter One"],
                current_blocks=[
                    PacketBlock(
                        block_id=block_id,
                        block_type="paragraph",
                        sentence_ids=sentence_ids,
                        text=" ".join(sentence_texts),
                    )
                ],
                chapter_brief="Packet order regression test.",
                style_constraints={"tone": "faithful-clear"},
                budget_hint={"max_input_tokens": 6000, "max_output_tokens": 2500},
            )
            session.add(
                TranslationPacket(
                    id=packet_id,
                    chapter_id=chapter_id,
                    block_start_id=block_id,
                    block_end_id=block_id,
                    packet_type=PacketType.TRANSLATE,
                    book_profile_version=1,
                    chapter_brief_version=1,
                    termbase_version=1,
                    entity_snapshot_version=1,
                    style_snapshot_version=1,
                    packet_json=context_packet.model_dump(mode="json"),
                    risk_score=0.1,
                    status=PacketStatus.BUILT,
                )
            )
            session.commit()
            for sentence_id in sentence_ids:
                session.add(
                    PacketSentenceMap(
                        packet_id=packet_id,
                        sentence_id=sentence_id,
                        role=PacketSentenceRole.CURRENT,
                    )
                )
            session.commit()

            bundle = TranslationRepository(session).load_packet_bundle(packet_id)
            self.assertEqual([sentence.id for sentence in bundle.current_sentences], sentence_ids)

            prompt_request = build_translation_prompt_request(
                TranslationTask(
                    context_packet=bundle.context_packet,
                    current_sentences=bundle.current_sentences,
                ),
                model_name="mock-llm",
                prompt_version="packet-order-test",
            )

        first_index = prompt_request.user_prompt.index(sentence_texts[0])
        second_index = prompt_request.user_prompt.index(sentence_texts[1])
        third_index = prompt_request.user_prompt.index(sentence_texts[2])
        self.assertLess(first_index, second_index)
        self.assertLess(second_index, third_index)

    def test_build_translation_prompt_request_supports_sentence_led_layout(self) -> None:
        context_packet = ContextPacket(
            packet_id="pkt-1",
            document_id="doc-1",
            chapter_id="ch-1",
            packet_type="translate",
            book_profile_version=1,
            chapter_brief_version=1,
            heading_path=["Chapter One"],
            current_blocks=[
                PacketBlock(
                    block_id="block-1",
                    block_type="paragraph",
                    sentence_ids=["s1", "s2"],
                    text="First sentence. Second sentence.",
                )
            ],
            chapter_brief="Chapter summary.",
        )
        current_sentences = [
            Sentence(
                id="s1",
                block_id="block-1",
                chapter_id="ch-1",
                document_id="doc-1",
                ordinal_in_block=1,
                source_text="First sentence.",
                normalized_text="First sentence.",
                source_lang="en",
                translatable=True,
                nontranslatable_reason=None,
                source_anchor=None,
                source_span_json={},
                upstream_confidence=1.0,
                sentence_status=SentenceStatus.PENDING,
                active_version=1,
            ),
            Sentence(
                id="s2",
                block_id="block-1",
                chapter_id="ch-1",
                document_id="doc-1",
                ordinal_in_block=2,
                source_text="Second sentence.",
                normalized_text="Second sentence.",
                source_lang="en",
                translatable=True,
                nontranslatable_reason=None,
                source_anchor=None,
                source_span_json={},
                upstream_confidence=1.0,
                sentence_status=SentenceStatus.PENDING,
                active_version=1,
            ),
        ]
        prompt_request = build_translation_prompt_request(
            TranslationTask(
                context_packet=context_packet,
                current_sentences=current_sentences,
            ),
            model_name="mock-llm",
            prompt_version="layout-test",
            prompt_layout="sentence-led",
        )
        self.assertIn("Current Sentences:", prompt_request.user_prompt)
        self.assertIn("Current Paragraph:", prompt_request.user_prompt)
        self.assertNotIn("Sentence Ledger:", prompt_request.user_prompt)
        self.assertLess(
            prompt_request.user_prompt.index("Current Sentences:"),
            prompt_request.user_prompt.index("Current Paragraph:"),
        )

    def test_build_translation_prompt_request_supports_prompt_profiles(self) -> None:
        context_packet = ContextPacket(
            packet_id="pkt-1",
            document_id="doc-1",
            chapter_id="ch-1",
            packet_type="translate",
            book_profile_version=1,
            chapter_brief_version=1,
            heading_path=["Chapter One"],
            current_blocks=[
                PacketBlock(
                    block_id="block-1",
                    block_type="paragraph",
                    sentence_ids=["s1"],
                    text="Context engineering shapes reasoning.",
                )
            ],
            chapter_brief="Chapter summary.",
            chapter_concepts=[
                ConceptCandidate(
                    source_term="context engineering",
                    canonical_zh="上下文工程",
                    status="locked",
                    confidence=1.0,
                    times_seen=3,
                )
            ],
        )
        current_sentences = [
            Sentence(
                id="s1",
                block_id="block-1",
                chapter_id="ch-1",
                document_id="doc-1",
                ordinal_in_block=1,
                source_text="Context engineering shapes reasoning.",
                normalized_text="Context engineering shapes reasoning.",
                source_lang="en",
                translatable=True,
                nontranslatable_reason=None,
                source_anchor=None,
                source_span_json={},
                upstream_confidence=1.0,
                sentence_status=SentenceStatus.PENDING,
                active_version=1,
            )
        ]

        current_prompt = build_translation_prompt_request(
            TranslationTask(context_packet=context_packet, current_sentences=current_sentences),
            model_name="mock-llm",
            prompt_version="profile-test",
            prompt_profile="current",
        )
        role_prompt = build_translation_prompt_request(
            TranslationTask(context_packet=context_packet, current_sentences=current_sentences),
            model_name="mock-llm",
            prompt_version="profile-test",
            prompt_profile="role-style-v2",
        )
        memory_prompt = build_translation_prompt_request(
            TranslationTask(context_packet=context_packet, current_sentences=current_sentences),
            model_name="mock-llm",
            prompt_version="profile-test",
            prompt_profile="role-style-memory-v2",
        )

        self.assertIn("high-fidelity book translation worker", current_prompt.system_prompt)
        self.assertIn("senior technical translator and localizer", role_prompt.system_prompt)
        self.assertIn("Chinese Style Priorities:", role_prompt.user_prompt)
        self.assertIn("Memory and Ambiguity Handling:", memory_prompt.user_prompt)
        self.assertIn("locked terms and chapter concept memory", memory_prompt.system_prompt)

    def test_context_compiler_can_disable_chapter_memory_features(self) -> None:
        packet = ContextPacket(
            packet_id="pkt-1",
            document_id="doc-1",
            chapter_id="ch-1",
            packet_type="translate",
            book_profile_version=1,
            chapter_brief_version=1,
            heading_path=["Chapter One"],
            current_blocks=[
                PacketBlock(
                    block_id="block-1",
                    block_type="paragraph",
                    sentence_ids=["s1"],
                    text="Current paragraph text.",
                )
            ],
            prev_translated_blocks=[],
            chapter_brief="Packet brief.",
        )
        snapshot = MemorySnapshot(
            id="mem-1",
            document_id="doc-1",
            scope_type=MemoryScopeType.CHAPTER,
            scope_id="ch-1",
            snapshot_type=SnapshotType.CHAPTER_TRANSLATION_MEMORY,
            version=3,
            content_json={
                "chapter_brief": "Memory brief.",
                "recent_accepted_translations": [
                    {
                        "block_id": "block-0",
                        "source_excerpt": "Prior source",
                        "target_excerpt": "先前译文",
                        "source_sentence_ids": ["s0"],
                    }
                ],
                "active_concepts": [
                    {
                        "source_term": "context engineering",
                        "canonical_zh": "上下文工程",
                        "status": "locked",
                        "times_seen": 2,
                    }
                ],
            },
            status=MemoryStatus.ACTIVE,
            created_at=datetime.now(timezone.utc),
        )
        compiler = ChapterContextCompiler()
        compiled = compiler.compile(packet, chapter_memory_snapshot=snapshot)
        self.assertEqual(compiled.chapter_brief, "Memory brief.")
        self.assertEqual(len(compiled.prev_translated_blocks), 1)
        self.assertEqual(len(compiled.chapter_concepts), 1)

        compiled_without_memory = compiler.compile(
            packet,
            chapter_memory_snapshot=snapshot,
            options=ChapterContextCompileOptions(
                include_memory_blocks=False,
                include_chapter_concepts=False,
                prefer_memory_chapter_brief=False,
            ),
        )
        self.assertEqual(compiled_without_memory.chapter_brief, "Packet brief.")
        self.assertEqual(len(compiled_without_memory.prev_translated_blocks), 0)
        self.assertEqual(len(compiled_without_memory.chapter_concepts), 0)

    def test_packet_experiment_service_dry_run_exports_prompt_without_worker_output(self) -> None:
        _, packet_ids = self._bootstrap_to_db()
        packet_id = packet_ids[1]

        with self.session_factory() as session:
            service = PacketExperimentService(
                TranslationRepository(session),
                settings=Settings(translation_backend="echo", translation_model="echo-worker"),
            )
            artifacts = service.run(
                packet_id,
                PacketExperimentOptions(
                    include_memory_blocks=False,
                    include_chapter_concepts=False,
                    prefer_memory_chapter_brief=False,
                    prompt_layout="sentence-led",
                    execute=False,
                ),
            )

        self.assertEqual(artifacts.payload["packet_id"], packet_id)
        self.assertIn("generated_at", artifacts.payload)
        self.assertEqual(
            artifacts.payload["database_url"],
            "sqlite+pysqlite:///./artifacts/book-agent.db",
        )
        self.assertEqual(artifacts.payload["options"]["prompt_layout"], "sentence-led")
        self.assertEqual(artifacts.payload["options"]["prompt_profile"], "role-style-v2")
        self.assertFalse(artifacts.payload["options"]["execute"])
        self.assertIsNone(artifacts.payload["worker_output"])
        self.assertEqual(artifacts.payload["worker_metadata"]["worker_name"], "planned::echo")
        self.assertIn("context_sources", artifacts.payload)
        self.assertIn("chapter_memory_snapshot_id", artifacts.payload)
        self.assertIn("chapter_memory_snapshot_version", artifacts.payload)
        self.assertEqual(artifacts.payload["context_sources"]["chapter_brief_source"], "packet")
        self.assertIn("Current Sentences:", artifacts.payload["prompt_request"]["user_prompt"])

    def test_packet_experiment_service_execute_runs_single_packet_worker(self) -> None:
        _, packet_ids = self._bootstrap_to_db()
        packet_id = packet_ids[1]
        fake_client = FakeTranslationClient()
        fake_client.source_sentence_ids = ["S1"]
        worker = LLMTranslationWorker(
            fake_client,
            model_name="mock-llm",
            prompt_version="experiment.v1",
            runtime_config={"provider": "fake"},
        )

        with self.session_factory() as session:
            service = PacketExperimentService(
                TranslationRepository(session),
                settings=Settings(translation_backend="echo", translation_model="echo-worker"),
                worker=worker,
            )
            artifacts = service.run(
                packet_id,
                PacketExperimentOptions(
                    prompt_layout="paragraph-led",
                    execute=True,
                ),
            )

        self.assertIsNotNone(artifacts.payload["worker_output"])
        self.assertEqual(artifacts.payload["worker_metadata"]["model_name"], "mock-llm")
        self.assertIn("generated_at", artifacts.payload)
        self.assertEqual(len(fake_client.requests), 1)
        self.assertIn("Current Paragraph:", artifacts.payload["prompt_request"]["user_prompt"])

    def test_packet_experiment_service_concept_override_applies_without_writing_memory(self) -> None:
        _, packet_ids = self._bootstrap_to_db()
        packet_id = packet_ids[2]

        with self.session_factory() as session:
            service = PacketExperimentService(
                TranslationRepository(session),
                settings=Settings(translation_backend="echo", translation_model="echo-worker"),
            )
            artifacts = service.run(
                packet_id,
                PacketExperimentOptions(
                    prompt_layout="paragraph-led",
                    concept_overrides=(
                        ConceptCandidate(
                            source_term="context engineering",
                            canonical_zh="上下文工程",
                            status="locked",
                            confidence=1.0,
                        ),
                    ),
                ),
            )

        self.assertIn("context engineering => 上下文工程 (locked", artifacts.payload["prompt_request"]["user_prompt"])
        self.assertEqual(len(artifacts.payload["options"]["concept_overrides"]), 1)
        self.assertEqual(
            artifacts.payload["options"]["concept_overrides"][0]["canonical_zh"],
            "上下文工程",
        )

    def test_chapter_concept_lock_updates_memory_and_prompt(self) -> None:
        _, packet_ids = self._bootstrap_to_db()
        target_packet_id = packet_ids[2]

        with self.session_factory() as session:
            repository = TranslationRepository(session)
            translation_service = TranslationService(repository)
            translation_service.execute_packet(packet_ids[0])
            translation_service.execute_packet(packet_ids[1])
            translation_service.execute_packet(packet_ids[2])
            session.flush()

            chapter_id = repository.load_packet_bundle(target_packet_id).context_packet.chapter_id
            lock_result = ChapterConceptLockService(session).lock_concept(
                chapter_id=chapter_id,
                source_term="context engineering",
                canonical_zh="上下文工程",
            )
            session.commit()

        with self.session_factory() as session:
            service = PacketExperimentService(
                TranslationRepository(session),
                settings=Settings(translation_backend="echo", translation_model="echo-worker"),
            )
            artifacts = service.run(
                target_packet_id,
                PacketExperimentOptions(
                    prompt_layout="paragraph-led",
                    execute=False,
                ),
            )

        self.assertGreaterEqual(lock_result.snapshot_version, 2)
        self.assertIn("context engineering => 上下文工程 (locked", artifacts.payload["prompt_request"]["user_prompt"])
        self.assertEqual(artifacts.payload["chapter_memory_snapshot_version"], lock_result.snapshot_version)

    def test_packet_experiment_diff_reports_context_and_prompt_changes(self) -> None:
        baseline = {
            "packet_id": "pkt-1",
            "options": {
                "prompt_layout": "paragraph-led",
            },
            "context_compile_version": "v1",
            "context_sources": {
                "raw_prev_translated_count": 0,
                "compiled_prev_translated_count": 0,
                "chapter_memory_translation_count": 0,
                "raw_chapter_concept_count": 0,
                "compiled_chapter_concept_count": 0,
                "chapter_memory_concept_count": 0,
                "chapter_brief_source": "packet",
            },
            "context_packet": {
                "chapter_brief": "Old brief",
                "prev_translated_blocks": [],
                "chapter_concepts": [],
            },
            "prompt_request": {
                "system_prompt": "system one",
                "user_prompt": "Section Context:\nCurrent Paragraph:\n- P1 [paragraph] Alpha",
            },
            "worker_output": None,
        }
        candidate = {
            "packet_id": "pkt-1",
            "options": {
                "prompt_layout": "sentence-led",
            },
            "context_compile_version": "v1",
            "context_sources": {
                "raw_prev_translated_count": 0,
                "compiled_prev_translated_count": 1,
                "chapter_memory_translation_count": 1,
                "raw_chapter_concept_count": 0,
                "compiled_chapter_concept_count": 1,
                "chapter_memory_concept_count": 1,
                "chapter_brief_source": "memory",
            },
            "context_packet": {
                "chapter_brief": "New brief",
                "prev_translated_blocks": [{"block_id": "b1"}],
                "chapter_concepts": [{"source_term": "context engineering"}],
            },
            "prompt_request": {
                "system_prompt": "system two",
                "user_prompt": "Section Context:\nCurrent Sentences:\n1. [S1] Alpha\nCurrent Paragraph:\n- P1 [paragraph] Alpha",
            },
            "worker_output": {
                "target_segments": [
                    {
                        "text_zh": "译文 Alpha",
                    }
                ]
            },
        }

        diff = compare_experiment_payloads(
            baseline,
            candidate,
            baseline_label="base",
            candidate_label="cand",
        )

        self.assertTrue(diff.payload["summary"]["prompt_layout_changed"])
        self.assertTrue(diff.payload["summary"]["chapter_brief_changed"])
        self.assertTrue(diff.payload["summary"]["chapter_brief_source_changed"])
        self.assertTrue(diff.payload["summary"]["previous_translation_count_changed"])
        self.assertTrue(diff.payload["summary"]["chapter_concept_count_changed"])
        self.assertTrue(diff.payload["summary"]["user_prompt_changed"])
        self.assertTrue(diff.payload["summary"]["worker_output_presence_changed"])
        self.assertIn("Current Sentences", "\n".join(diff.payload["prompt_delta"]["user_prompt_unified_diff"]))
        self.assertEqual(diff.payload["context_delta"]["previous_translation_count"]["delta"], 1)
        self.assertEqual(
            diff.payload["context_delta"]["context_sources"]["chapter_brief_source"]["cand"],
            "memory",
        )

    def test_packet_experiment_scan_ranks_memory_rich_packets_first(self) -> None:
        packet_a_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"
        packet_b_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2"

        class StubExperimentService:
            def run(self, packet_id: str, options: PacketExperimentOptions):
                payloads = {
                    packet_a_id: {
                        "context_packet": {
                            "current_blocks": [{"block_type": "paragraph", "sentence_ids": ["s1", "s2"]}],
                        },
                        "context_sources": {
                            "raw_prev_translated_count": 0,
                            "compiled_prev_translated_count": 3,
                            "chapter_memory_translation_count": 3,
                            "raw_chapter_concept_count": 0,
                            "compiled_chapter_concept_count": 1,
                            "chapter_memory_concept_count": 1,
                            "chapter_brief_source": "memory",
                        },
                    },
                    packet_b_id: {
                        "context_packet": {
                            "current_blocks": [{"block_type": "paragraph", "sentence_ids": ["s1"]}],
                        },
                        "context_sources": {
                            "raw_prev_translated_count": 0,
                            "compiled_prev_translated_count": 0,
                            "chapter_memory_translation_count": 0,
                            "raw_chapter_concept_count": 0,
                            "compiled_chapter_concept_count": 0,
                            "chapter_memory_concept_count": 0,
                            "chapter_brief_source": "packet",
                        },
                    },
                }
                return type("Artifacts", (), {"payload": payloads[packet_id]})()

        with self.session_factory() as session:
            chapter_id = "22222222-2222-2222-2222-222222222222"
            document_id = "11111111-1111-1111-1111-111111111111"
            session.add(
                Document(
                    id=document_id,
                    source_type=SourceType.EPUB,
                    file_fingerprint="fingerprint-1",
                    source_path="/tmp/sample.epub",
                    title="Packet Scan Test",
                    author="Tester",
                    src_lang="en",
                    tgt_lang="zh",
                    status=DocumentStatus.ACTIVE,
                    parser_version=1,
                    segmentation_version=1,
                    active_book_profile_version=1,
                    metadata_json={},
                )
            )
            session.commit()
            session.add(
                Chapter(
                    id=chapter_id,
                    document_id=document_id,
                    ordinal=1,
                    title_src="Chapter One",
                    title_tgt=None,
                    anchor_start=None,
                    anchor_end=None,
                    status=ChapterStatus.PACKET_BUILT,
                    summary_version=1,
                    risk_level=None,
                    metadata_json={},
                )
            )
            session.commit()
            session.add_all(
                [
                    TranslationPacket(
                        id=packet_a_id,
                        chapter_id=chapter_id,
                        block_start_id=None,
                        block_end_id=None,
                        packet_type=PacketType.TRANSLATE,
                        book_profile_version=1,
                        chapter_brief_version=1,
                        termbase_version=1,
                        entity_snapshot_version=1,
                        style_snapshot_version=1,
                        packet_json={},
                        risk_score=0.1,
                        status=PacketStatus.BUILT,
                    ),
                    TranslationPacket(
                        id=packet_b_id,
                        chapter_id=chapter_id,
                        block_start_id=None,
                        block_end_id=None,
                        packet_type=PacketType.TRANSLATE,
                        book_profile_version=1,
                        chapter_brief_version=1,
                        termbase_version=1,
                        entity_snapshot_version=1,
                        style_snapshot_version=1,
                        packet_json={},
                        risk_score=0.1,
                        status=PacketStatus.BUILT,
                    ),
                ]
            )
            session.commit()

            scan_service = PacketExperimentScanService(
                TranslationRepository(session),
                experiment_service=StubExperimentService(),
            )
            artifacts = scan_service.scan_chapter(chapter_id)

        self.assertEqual(artifacts.payload["packet_count"], 2)
        self.assertEqual(artifacts.payload["top_candidate"]["packet_id"], packet_a_id)
        self.assertGreater(artifacts.payload["entries"][0]["memory_signal_score"], artifacts.payload["entries"][1]["memory_signal_score"])

    def test_translation_service_reuses_chapter_memory_across_nonadjacent_packets(self) -> None:
        chapter_xhtml = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>First memory paragraph introduces the core concept.</p>
    <p>Second memory paragraph extends the discussion.</p>
    <p>Third memory paragraph keeps building the case.</p>
    <p>Fourth memory paragraph revisits the core concept in a new light.</p>
  </body>
</html>
"""
        document_id, packet_ids = self._bootstrap_custom_chapter(chapter_xhtml)
        heading_packet_id, first_packet_id, second_packet_id, third_packet_id, fourth_packet_id = packet_ids[:5]

        with self.session_factory() as session:
            repository = TranslationRepository(session)
            service = TranslationService(repository)
            service.execute_packet(heading_packet_id)
            service.execute_packet(first_packet_id)
            service.execute_packet(second_packet_id)
            service.execute_packet(third_packet_id)
            session.commit()

            latest_memory = session.scalars(
                select(MemorySnapshot)
                .where(
                    MemorySnapshot.document_id == document_id,
                    MemorySnapshot.scope_type == MemoryScopeType.CHAPTER,
                    MemorySnapshot.snapshot_type == SnapshotType.CHAPTER_TRANSLATION_MEMORY,
                )
                .order_by(MemorySnapshot.version.desc())
            ).first()
            self.assertIsNotNone(latest_memory)
            self.assertEqual(latest_memory.content_json["last_packet_id"], third_packet_id)
            self.assertGreaterEqual(len(latest_memory.content_json["recent_accepted_translations"]), 3)

        fake_client = FakeTranslationClient()
        worker = LLMTranslationWorker(
            fake_client,
            model_name="mock-llm",
            prompt_version="p0.llm.v1",
            runtime_config={"provider": "fake"},
        )

        with self.session_factory() as session:
            repository = TranslationRepository(session)
            bundle = repository.load_packet_bundle(fourth_packet_id)
            fake_client.source_sentence_ids = ["S1"]
            TranslationService(repository, worker=worker).execute_packet(fourth_packet_id)
            session.commit()

            self.assertEqual(bundle.current_sentences[0].source_text, "Fourth memory paragraph revisits the core concept in a new light.")

        self.assertEqual(len(fake_client.requests), 1)
        prompt = fake_client.requests[0].user_prompt
        self.assertIn(
            "First memory paragraph introduces the core concept. => ZH::First memory paragraph introduces the core concept.",
            prompt,
        )
        self.assertIn("Current Paragraph:", prompt)

    def test_chapter_memory_backfill_reconstructs_memory_without_rerunning_packets(self) -> None:
        chapter_xhtml = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>First translated paragraph introduces context engineering.</p>
    <p>Second translated paragraph reinforces the context engineering concept.</p>
    <p>Third translated paragraph closes the section.</p>
  </body>
</html>
"""
        document_id, packet_ids = self._bootstrap_custom_chapter(chapter_xhtml)
        heading_packet_id, first_packet_id, second_packet_id, third_packet_id = packet_ids[:4]

        with self.session_factory() as session:
            repository = TranslationRepository(session)
            service = TranslationService(repository)
            for packet_id in [heading_packet_id, first_packet_id, second_packet_id, third_packet_id]:
                service.execute_packet(packet_id)
            session.commit()

            chapter_id = repository.load_packet_bundle(first_packet_id).packet.chapter_id
            session.query(MemorySnapshot).filter(
                MemorySnapshot.document_id == document_id,
                MemorySnapshot.scope_type == MemoryScopeType.CHAPTER,
                MemorySnapshot.scope_id == chapter_id,
                MemorySnapshot.snapshot_type == SnapshotType.CHAPTER_TRANSLATION_MEMORY,
            ).delete(synchronize_session=False)
            session.commit()

            artifacts = ChapterMemoryBackfillService(repository).backfill_chapter_with_options(
                chapter_id,
                reset_existing=False,
            )
            session.commit()

            latest_memory = session.scalars(
                select(MemorySnapshot)
                .where(
                    MemorySnapshot.document_id == document_id,
                    MemorySnapshot.scope_type == MemoryScopeType.CHAPTER,
                    MemorySnapshot.scope_id == chapter_id,
                    MemorySnapshot.snapshot_type == SnapshotType.CHAPTER_TRANSLATION_MEMORY,
                    MemorySnapshot.status == MemoryStatus.ACTIVE,
                )
                .order_by(MemorySnapshot.version.desc())
            ).first()

        self.assertTrue(artifacts.payload["seeded_initial_snapshot"])
        self.assertEqual(artifacts.payload["replayed_packet_count"], 4)
        self.assertIsNotNone(latest_memory)
        self.assertEqual(latest_memory.content_json["last_packet_id"], third_packet_id)
        self.assertGreaterEqual(len(latest_memory.content_json["recent_accepted_translations"]), 3)
        self.assertGreaterEqual(len(latest_memory.content_json["active_concepts"]), 1)

    def test_chapter_memory_backfill_can_reset_existing_snapshot(self) -> None:
        chapter_xhtml = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>Agentic AI depends on distributed SQL.</p>
    <p>Context engineering shapes how context is created.</p>
  </body>
</html>
"""
        document_id, packet_ids = self._bootstrap_custom_chapter(chapter_xhtml)
        heading_packet_id, first_packet_id, second_packet_id = packet_ids[:3]

        with self.session_factory() as session:
            repository = TranslationRepository(session)
            service = TranslationService(repository)
            for packet_id in [heading_packet_id, first_packet_id, second_packet_id]:
                service.execute_packet(packet_id)
            session.commit()

            chapter_id = repository.load_packet_bundle(first_packet_id).packet.chapter_id
            latest_memory = session.scalars(
                select(MemorySnapshot)
                .where(
                    MemorySnapshot.document_id == document_id,
                    MemorySnapshot.scope_type == MemoryScopeType.CHAPTER,
                    MemorySnapshot.scope_id == chapter_id,
                    MemorySnapshot.snapshot_type == SnapshotType.CHAPTER_TRANSLATION_MEMORY,
                    MemorySnapshot.status == MemoryStatus.ACTIVE,
                )
                .order_by(MemorySnapshot.version.desc())
            ).first()
            assert latest_memory is not None
            latest_memory.content_json["active_concepts"] = [
                {
                    "source_term": "Aakash Gupta Context Engineering",
                    "canonical_zh": None,
                    "status": "candidate",
                    "confidence": 0.6,
                    "first_seen_packet_id": first_packet_id,
                    "last_seen_packet_id": second_packet_id,
                    "times_seen": 2,
                }
            ]
            session.merge(latest_memory)
            session.commit()

            artifacts = ChapterMemoryBackfillService(repository).backfill_chapter_with_options(
                chapter_id,
                reset_existing=True,
            )
            session.commit()

            rebuilt_memory = session.scalars(
                select(MemorySnapshot)
                .where(
                    MemorySnapshot.document_id == document_id,
                    MemorySnapshot.scope_type == MemoryScopeType.CHAPTER,
                    MemorySnapshot.scope_id == chapter_id,
                    MemorySnapshot.snapshot_type == SnapshotType.CHAPTER_TRANSLATION_MEMORY,
                    MemorySnapshot.status == MemoryStatus.ACTIVE,
                )
                .order_by(MemorySnapshot.version.desc())
            ).first()

        self.assertTrue(artifacts.payload["reset_existing"])
        self.assertIsNotNone(rebuilt_memory)
        lowered = {item["source_term"].lower() for item in rebuilt_memory.content_json["active_concepts"]}
        self.assertIn("agentic ai", lowered)
        self.assertIn("distributed sql", lowered)
        self.assertIn("context engineering", lowered)
        self.assertNotIn("aakash gupta context engineering", lowered)

    def test_extract_concept_candidates_filters_noise_and_keeps_core_terms(self) -> None:
        with self.session_factory() as session:
            service = TranslationService(TranslationRepository(session))
            concepts = service._extract_concept_candidates(
                [
                    "Agentic AI depends on distributed SQL and context engineering.",
                    "Generative AI can produce text, but an adaptive agent can act over time.",
                    "Aakash Gupta Context Engineering is discussed elsewhere.",
                    "To accomplish these responses agentic systems need to perceive and act.",
                    "An agent might fail without memory.",
                ]
            )

        lowered = {concept.lower() for concept in concepts}
        self.assertIn("agentic ai", lowered)
        self.assertIn("distributed sql", lowered)
        self.assertIn("context engineering", lowered)
        self.assertIn("generative ai", lowered)
        self.assertIn("adaptive agent", lowered)
        self.assertNotIn("agent might", lowered)
        self.assertNotIn("aakash gupta context engineering", lowered)
        self.assertNotIn("accomplish these responses agentic", lowered)

    def test_llm_worker_drops_invalid_sentence_ids_before_persistence(self) -> None:
        _, packet_ids = self._bootstrap_to_db()
        packet_id = packet_ids[0]
        fake_client = FakeTranslationClient()
        fake_client.source_sentence_ids = ["broken-id"]
        worker = LLMTranslationWorker(
            fake_client,
            model_name="mock-llm",
            prompt_version="p0.llm.v1",
            runtime_config={"provider": "fake"},
        )

        with self.session_factory() as session:
            service = TranslationService(TranslationRepository(session), worker=worker)
            artifacts = service.execute_packet(packet_id)
            session.commit()

            self.assertEqual(len(artifacts.target_segments), 1)
            self.assertEqual(len(artifacts.alignment_edges), 0)

    def test_openai_compatible_client_builds_payload_and_parses_structured_output(self) -> None:
        transport = FakeJSONTransport(
            {
                "id": "resp_123",
                "usage": {"input_tokens": 123, "output_tokens": 45, "total_tokens": 168},
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"packet_id":"pkt_1","target_segments":[{"temp_id":"t1","text_zh":"译文","segment_type":"sentence","source_sentence_ids":["s1"],"confidence":0.91}],"alignment_suggestions":[{"source_sentence_ids":["s1"],"target_temp_ids":["t1"],"relation_type":"1:1","confidence":0.93}],"low_confidence_flags":[],"notes":[]}',
                            }
                        ],
                    }
                ]
            }
        )
        client = OpenAICompatibleTranslationClient(
            api_key="test-key",
            base_url="https://provider.example/v1/responses",
            timeout_seconds=45,
            transport=transport,
            extra_headers={"X-Test": "1"},
        )

        output = client.generate_translation(
            TranslationPromptRequest(
                packet_id="pkt_1",
                model_name="gpt-test",
                prompt_version="p0.llm.v1",
                system_prompt="system",
                user_prompt="user",
                response_schema=TranslationWorkerOutput.model_json_schema(),
            )
        )

        self.assertEqual(output.packet_id, "pkt_1")
        self.assertEqual(output.target_segments[0].text_zh, "译文")
        self.assertEqual(output.usage.token_in, 123)
        self.assertEqual(output.usage.token_out, 45)
        self.assertEqual(output.usage.total_tokens, 168)
        self.assertEqual(output.usage.provider_request_id, "resp_123")
        self.assertGreater(output.usage.latency_ms, 0)
        self.assertEqual(len(transport.calls), 1)
        call = transport.calls[0]
        self.assertEqual(call["url"], "https://provider.example/v1/responses")
        self.assertEqual(call["timeout_seconds"], 45)
        self.assertEqual(call["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(call["headers"]["X-Test"], "1")
        self.assertEqual(call["payload"]["model"], "gpt-test")
        self.assertEqual(call["payload"]["text"]["format"]["type"], "json_schema")
        self.assertEqual(call["payload"]["text"]["format"]["name"], "translation_worker_output")

    def test_openai_compatible_client_supports_chat_completions_mode(self) -> None:
        transport = FakeJSONTransport(
            {
                "id": "chatcmpl_123",
                "usage": {"prompt_tokens": 88, "completion_tokens": 22, "total_tokens": 110},
                "choices": [
                    {
                        "message": {
                            "content": '{"packet_id":"pkt_1","target_segments":[{"temp_id":"t1","text_zh":"译文","segment_type":"sentence","source_sentence_ids":["s1"],"confidence":0.91}],"alignment_suggestions":[{"source_sentence_ids":["s1"],"target_temp_ids":["t1"],"relation_type":"1:1","confidence":0.93}],"low_confidence_flags":[],"notes":[]}'
                        }
                    }
                ]
            }
        )
        client = OpenAICompatibleTranslationClient(
            api_key="test-key",
            base_url="https://api.deepseek.com",
            timeout_seconds=45,
            input_cache_hit_cost_per_1m_tokens=0.028,
            input_cost_per_1m_tokens=0.28,
            output_cost_per_1m_tokens=0.42,
            transport=transport,
        )

        output = client.generate_translation(
            TranslationPromptRequest(
                packet_id="pkt_1",
                model_name="deepseek-chat",
                prompt_version="p0.llm.v1",
                system_prompt="system",
                user_prompt="user",
                response_schema=TranslationWorkerOutput.model_json_schema(),
            )
        )

        self.assertEqual(output.packet_id, "pkt_1")
        self.assertEqual(output.target_segments[0].text_zh, "译文")
        self.assertEqual(output.usage.token_in, 88)
        self.assertEqual(output.usage.token_out, 22)
        self.assertEqual(output.usage.total_tokens, 110)
        self.assertEqual(output.usage.provider_request_id, "chatcmpl_123")
        self.assertGreater(output.usage.latency_ms, 0)
        self.assertAlmostEqual(output.usage.cost_usd or 0.0, 0.00003388, places=8)
        self.assertEqual(len(transport.calls), 1)
        call = transport.calls[0]
        self.assertEqual(call["url"], "https://api.deepseek.com/chat/completions")
        self.assertEqual(call["payload"]["model"], "deepseek-chat")
        self.assertEqual(call["payload"]["messages"][0]["role"], "system")
        self.assertEqual(call["payload"]["messages"][1]["role"], "user")
        self.assertIn("Do not use top-level keys like translation or translations.", call["payload"]["messages"][1]["content"])
        self.assertIn('"packet_id"', call["payload"]["messages"][1]["content"])
        self.assertEqual(call["payload"]["response_format"]["type"], "json_object")

    def test_openai_compatible_client_salvages_fenced_chat_completion_json(self) -> None:
        transport = FakeJSONTransport(
            {
                "id": "chatcmpl_456",
                "usage": {"prompt_tokens": 55, "completion_tokens": 33, "total_tokens": 88},
                "choices": [
                    {
                        "message": {
                            "content": (
                                "Here is the requested payload.\n"
                                "```json\n"
                                '{"packet_id":"pkt_1","target_segments":[{"temp_id":"t1","text_zh":"译文","segment_type":"sentence","source_sentence_ids":["s1"],"confidence":0.91}],"alignment_suggestions":[{"source_sentence_ids":["s1"],"target_temp_ids":["t1"],"relation_type":"1:1","confidence":0.93}],"low_confidence_flags":[],"notes":[]}\n'
                                "```"
                            )
                        }
                    }
                ],
            }
        )
        client = OpenAICompatibleTranslationClient(
            api_key="test-key",
            base_url="https://api.deepseek.com",
            transport=transport,
        )

        output = client.generate_translation(
            TranslationPromptRequest(
                packet_id="pkt_1",
                model_name="deepseek-chat",
                prompt_version="p0.llm.v1",
                system_prompt="system",
                user_prompt="user",
                response_schema=TranslationWorkerOutput.model_json_schema(),
            )
        )

        self.assertEqual(output.packet_id, "pkt_1")
        self.assertEqual(output.target_segments[0].text_zh, "译文")

    def test_openai_compatible_client_salvages_wrapped_json_object_from_text(self) -> None:
        transport = FakeJSONTransport(
            {
                "id": "resp_456",
                "usage": {"input_tokens": 66, "output_tokens": 44, "total_tokens": 110},
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": (
                                    "Sure. The structured payload is below:\n"
                                    '{"translation":{"packet_id":"pkt_1","target_segments":[{"temp_id":"t1","text_zh":"译文","segment_type":"sentence","source_sentence_ids":["s1"],"confidence":0.91}],"alignment_suggestions":[{"source_sentence_ids":["s1"],"target_temp_ids":["t1"],"relation_type":"1:1","confidence":0.93}],"low_confidence_flags":[],"notes":[]}}\n'
                                    "Use it as-is."
                                ),
                            }
                        ],
                    }
                ],
            }
        )
        client = OpenAICompatibleTranslationClient(
            api_key="test-key",
            base_url="https://provider.example/v1/responses",
            transport=transport,
        )

        output = client.generate_translation(
            TranslationPromptRequest(
                packet_id="pkt_1",
                model_name="gpt-test",
                prompt_version="p0.llm.v1",
                system_prompt="system",
                user_prompt="user",
                response_schema=TranslationWorkerOutput.model_json_schema(),
            )
        )

        self.assertEqual(output.packet_id, "pkt_1")
        self.assertEqual(output.target_segments[0].text_zh, "译文")

    def test_factory_builds_openai_compatible_worker_when_credentials_exist(self) -> None:
        settings = Settings(
            translation_backend="openai_compatible",
            translation_model="gpt-test",
            translation_prompt_version="p0.llm.v1",
            translation_openai_api_key="test-key",
            translation_openai_base_url="https://provider.example/v1/responses",
            translation_timeout_seconds=30,
            translation_max_retries=1,
            translation_retry_backoff_seconds=1.5,
        )

        worker = build_translation_worker(settings)

        self.assertIsInstance(worker, LLMTranslationWorker)
        metadata = worker.metadata()
        self.assertEqual(metadata.model_name, "gpt-test")
        self.assertEqual(metadata.runtime_config["provider"], "openai_compatible")
        self.assertEqual(metadata.runtime_config["base_url"], "https://provider.example/v1/responses")
        self.assertEqual(metadata.runtime_config["timeout_seconds"], 30)
        self.assertEqual(metadata.runtime_config["max_retries"], 1)
        self.assertEqual(metadata.runtime_config["retry_backoff_seconds"], 1.5)

    def test_settings_accept_standard_openai_env_aliases(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "OPENAI_API_KEY": "env-test-key",
                "OPENAI_BASE_URL": "https://provider.example/v1/responses",
            },
            clear=False,
        ):
            settings = Settings(
                translation_backend="openai_compatible",
                translation_model="gpt-test",
                translation_prompt_version="p0.llm.v1",
            )

        self.assertEqual(settings.translation_openai_api_key, "env-test-key")
        self.assertEqual(settings.translation_openai_base_url, "https://provider.example/v1/responses")

    def test_factory_rejects_openai_compatible_worker_without_api_key(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "BOOK_AGENT_TRANSLATION_OPENAI_API_KEY": "",
                "OPENAI_API_KEY": "",
            },
            clear=False,
        ):
            settings = Settings(
                translation_backend="openai_compatible",
                translation_model="gpt-test",
                translation_prompt_version="p0.llm.v1",
                translation_openai_api_key=None,
            )

            with self.assertRaises(ValueError):
                build_translation_worker(settings)

    def test_translation_service_normalizes_common_llm_output_labels(self) -> None:
        _, packet_ids = self._bootstrap_to_db()
        packet_id = packet_ids[0]

        with self.session_factory() as session:
            bundle = TranslationRepository(session).load_packet_bundle(packet_id)
            worker = LLMTranslationWorker(
                LooseSchemaClient([sentence.id for sentence in bundle.current_sentences]),
                model_name="loose-schema-test",
                prompt_version="p0.llm.v1",
            )
            artifacts = TranslationService(TranslationRepository(session), worker=worker).execute_packet(packet_id)
            session.commit()

            self.assertEqual(artifacts.target_segments[0].segment_type.value, "sentence")
            self.assertEqual(artifacts.alignment_edges[0].relation_type.value, "1:1")

    def test_translation_service_persists_usage_metadata_from_worker_result(self) -> None:
        _, packet_ids = self._bootstrap_to_db()
        packet_id = packet_ids[0]

        class UsageAwareClient:
            def __init__(self, source_sentence_ids: list[str]) -> None:
                self.source_sentence_ids = source_sentence_ids

            def generate_translation(self, request: TranslationPromptRequest) -> TranslationWorkerResult:
                return TranslationWorkerResult(
                    output=TranslationWorkerOutput(
                        packet_id=request.packet_id,
                        target_segments=[
                            TranslationTargetSegment(
                                temp_id="temp-1",
                                text_zh="带 usage 的译文",
                                segment_type="sentence",
                                source_sentence_ids=self.source_sentence_ids,
                                confidence=0.93,
                            )
                        ],
                        alignment_suggestions=[
                            AlignmentSuggestion(
                                source_sentence_ids=self.source_sentence_ids,
                                target_temp_ids=["temp-1"],
                                relation_type="1:1",
                                confidence=0.96,
                            )
                        ],
                    ),
                    usage={
                        "token_in": 321,
                        "token_out": 123,
                        "total_tokens": 444,
                        "latency_ms": 987,
                        "cost_usd": 0.00123,
                        "provider_request_id": "req_123",
                    },
                )

        with self.session_factory() as session:
            bundle = TranslationRepository(session).load_packet_bundle(packet_id)
            worker = LLMTranslationWorker(
                UsageAwareClient([sentence.id for sentence in bundle.current_sentences]),
                model_name="usage-test",
                prompt_version="p0.llm.v1",
            )
            artifacts = TranslationService(TranslationRepository(session), worker=worker).execute_packet(packet_id)
            session.commit()

            self.assertEqual(artifacts.translation_run.token_in, 321)
            self.assertEqual(artifacts.translation_run.token_out, 123)
            self.assertEqual(float(artifacts.translation_run.cost_usd), 0.00123)
            self.assertEqual(artifacts.translation_run.latency_ms, 987)


if __name__ == "__main__":
    unittest.main()
