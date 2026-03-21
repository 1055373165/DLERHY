# ruff: noqa: E402

from http.client import IncompleteRead
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
    Detector,
    DocumentStatus,
    IssueStatus,
    MemoryStatus,
    MemoryScopeType,
    PacketSentenceRole,
    PacketStatus,
    PacketType,
    ProtectedPolicy,
    RootCauseLayer,
    Severity,
    SnapshotType,
    SentenceStatus,
    SourceType,
)
from book_agent.domain.models import Block, Chapter, Document, MemorySnapshot, Sentence
from book_agent.domain.models.review import ReviewIssue
from book_agent.domain.models.translation import AlignmentEdge, PacketSentenceMap, TranslationPacket
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.infra.repositories.chapter_memory import ChapterTranslationMemoryRepository
from book_agent.infra.repositories.translation import TranslationRepository
from book_agent.orchestrator.bootstrap import BootstrapOrchestrator
from book_agent.core.config import Settings
from book_agent.services.packet_experiment import PacketExperimentOptions, PacketExperimentService
from book_agent.services.packet_experiment_diff import compare_experiment_payloads
from book_agent.services.packet_experiment_scan import PacketExperimentScanService
from book_agent.services.chapter_memory_backfill import ChapterMemoryBackfillService
from book_agent.services.chapter_concept_lock import ChapterConceptLockService
from book_agent.services.translation_chapter_smoke import (
    TranslationChapterSmokeOptions,
    TranslationChapterSmokeService,
)
from book_agent.services.context_compile import (
    ChapterContextCompileOptions,
    ChapterContextCompiler,
    _compress_chapter_brief,
)
from book_agent.services.translation import TranslationService
from book_agent.workers.factory import build_translation_worker
from book_agent.workers.contracts import (
    AlignmentSuggestion,
    ConceptCandidate,
    ContextPacket,
    DiscourseBridge,
    PacketBlock,
    RelevantTerm,
    TranslatedContextBlock,
    TranslationTargetSegment,
    TranslationWorkerOutput,
    TranslationWorkerResult,
)
from book_agent.workers.providers.openai_compatible import OpenAICompatibleTranslationClient, UrllibJSONTransport
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


class DuplicateAlignmentClient:
    def generate_translation(self, request: TranslationPromptRequest) -> TranslationWorkerOutput:
        return TranslationWorkerOutput(
            packet_id=request.packet_id,
            target_segments=[
                TranslationTargetSegment(
                    temp_id="temp-1",
                    text_zh="重复对齐测试译文",
                    segment_type="sentence",
                    source_sentence_ids=["S1"],
                    confidence=0.9,
                )
            ],
            alignment_suggestions=[
                AlignmentSuggestion(
                    source_sentence_ids=["S1"],
                    target_temp_ids=["temp-1", "temp-1"],
                    relation_type="1:1",
                    confidence=0.9,
                ),
                AlignmentSuggestion(
                    source_sentence_ids=["S1"],
                    target_temp_ids=["temp-1"],
                    relation_type="1:1",
                    confidence=0.9,
                ),
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
        self.assertEqual(artifacts.translation_run.model_config_json["prompt_profile"], "role-style-faithful-v6")
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
        self.assertIn("publication-grade English-to-Chinese translator", fake_client.requests[0].system_prompt)

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

    def test_translation_service_dedupes_duplicate_alignment_edges_from_worker_output(self) -> None:
        _, packet_ids = self._bootstrap_to_db()
        worker = LLMTranslationWorker(
            DuplicateAlignmentClient(),
            model_name="mock-llm",
            prompt_version="duplicate-alignment-test",
            runtime_config={"provider": "fake"},
        )

        with self.session_factory() as session:
            repository = TranslationRepository(session)
            artifacts = TranslationService(repository, worker=worker).execute_packet(packet_ids[1])
            session.commit()

            stored_edges = session.scalars(
                select(AlignmentEdge).where(AlignmentEdge.target_segment_id == artifacts.target_segments[0].id)
            ).all()

        self.assertEqual(len(artifacts.alignment_edges), 1)
        self.assertEqual(len(stored_edges), 1)

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

    def test_build_translation_prompt_request_omits_empty_context_sections_and_compacts_single_sentence_ledger(self) -> None:
        context_packet = ContextPacket(
            packet_id="pkt-compact",
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
                    text="A compact paragraph that should not duplicate the source sentence twice.",
                )
            ],
            chapter_brief=None,
        )
        sentence = Sentence(
            id="s1",
            block_id="block-1",
            chapter_id="ch-1",
            document_id="doc-1",
            ordinal_in_block=1,
            source_text="A compact paragraph that should not duplicate the source sentence twice.",
            normalized_text="A compact paragraph that should not duplicate the source sentence twice.",
            source_lang="en",
            translatable=True,
            nontranslatable_reason=None,
            source_anchor=None,
            source_span_json={},
            upstream_confidence=1.0,
            sentence_status=SentenceStatus.PENDING,
            active_version=1,
        )

        prompt_request = build_translation_prompt_request(
            TranslationTask(context_packet=context_packet, current_sentences=[sentence]),
            model_name="mock-llm",
            prompt_version="compact-prompt-test",
        )

        self.assertIn("Sentence Ledger:", prompt_request.user_prompt)
        self.assertIn("[S1] This is the only sentence in the current paragraph.", prompt_request.user_prompt)
        self.assertEqual(
            prompt_request.user_prompt.count(
                "A compact paragraph that should not duplicate the source sentence twice."
            ),
            1,
        )
        self.assertNotIn("Locked and Relevant Terms:", prompt_request.user_prompt)
        self.assertNotIn("Relevant Entities:", prompt_request.user_prompt)
        self.assertNotIn("Chapter Concept Memory:", prompt_request.user_prompt)
        self.assertNotIn("Previous Accepted Translations (same local context):", prompt_request.user_prompt)
        self.assertNotIn("Previous Source Context:", prompt_request.user_prompt)
        self.assertNotIn("Upcoming Source Context:", prompt_request.user_prompt)

    def test_build_translation_prompt_request_uses_compact_role_style_prompt_for_self_contained_packet(self) -> None:
        context_packet = ContextPacket(
            packet_id="pkt-lean-role",
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
                    text="Short self-contained technical prose. Another short sentence keeps the paragraph local.",
                )
            ],
            chapter_brief=None,
        )
        current_sentences = [
            Sentence(
                id="s1",
                block_id="block-1",
                chapter_id="ch-1",
                document_id="doc-1",
                ordinal_in_block=1,
                source_text="Short self-contained technical prose.",
                normalized_text="Short self-contained technical prose.",
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
                source_text="Another short sentence keeps the paragraph local.",
                normalized_text="Another short sentence keeps the paragraph local.",
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

        full_prompt = build_translation_prompt_request(
            TranslationTask(context_packet=context_packet, current_sentences=current_sentences),
            model_name="mock-llm",
            prompt_version="lean-role-test",
            prompt_profile="role-style-v2",
            allow_compact_prompt=False,
        )
        compact_prompt = build_translation_prompt_request(
            TranslationTask(context_packet=context_packet, current_sentences=current_sentences),
            model_name="mock-llm",
            prompt_version="lean-role-test",
            prompt_profile="role-style-v2",
        )

        self.assertIn("Chinese Style Priorities:", full_prompt.user_prompt)
        self.assertNotIn("Chinese Style Priorities:", compact_prompt.user_prompt)
        self.assertIn("Current Paragraph:", compact_prompt.user_prompt)
        self.assertIn("Sentence Ledger:", compact_prompt.user_prompt)
        self.assertIn("professional English-to-Chinese technical translator", compact_prompt.system_prompt)
        self.assertLess(len(compact_prompt.user_prompt), len(full_prompt.user_prompt))
        self.assertLess(len(compact_prompt.system_prompt), len(full_prompt.system_prompt))

    def test_build_translation_prompt_request_includes_open_questions_and_rerun_hints(self) -> None:
        context_packet = ContextPacket(
            packet_id="pkt-questions",
            document_id="doc-1",
            chapter_id="chap-1",
            packet_type="translate",
            book_profile_version=1,
            heading_path=["Chapter One"],
            current_blocks=[
                PacketBlock(
                    block_id="block-1",
                    block_type="paragraph",
                    sentence_ids=["sent-1"],
                    text="Context engineering improves system behavior.",
                )
            ],
            chapter_brief="This section defines a core concept.",
            open_questions=[
                "speaker_reference_ambiguous",
                "Rerun focus [context_engineering_literal]: prefer '上下文工程' over literal phrasing in this packet.",
            ],
        )
        prompt_request = build_translation_prompt_request(
            TranslationTask(
                context_packet=context_packet,
                current_sentences=[
                    Sentence(
                        id="sent-1",
                        block_id="block-1",
                        chapter_id="chap-1",
                        document_id="doc-1",
                        ordinal_in_block=1,
                        source_text="Context engineering improves system behavior.",
                        normalized_text="Context engineering improves system behavior.",
                        source_lang="en",
                        translatable=True,
                        nontranslatable_reason=None,
                        source_anchor=None,
                        source_span_json={},
                        upstream_confidence=1.0,
                        sentence_status=SentenceStatus.PENDING,
                        active_version=1,
                    )
                ],
            ),
            model_name="mock-llm",
            prompt_version="prompt-open-questions-test",
        )

        self.assertIn("Open Questions and Rerun Hints:", prompt_request.user_prompt)
        self.assertIn("speaker_reference_ambiguous", prompt_request.user_prompt)
        self.assertIn("prefer '上下文工程' over literal phrasing", prompt_request.user_prompt)

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
            style_constraints={
                "paragraph_intent": "definition",
                "paragraph_intent_hint": "Treat this as concept-definition prose.",
                "literalism_guardrails": (
                    "Prefer natural Chinese evidential phrasing such as '大量证据表明' or "
                    "'现有证据表明', not literal weight metaphors. || "
                    "Prefer '更符合上下文的输出', not literal forms like '上下文更准确的输出'."
                ),
            },
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
        faithful_prompt = build_translation_prompt_request(
            TranslationTask(context_packet=context_packet, current_sentences=current_sentences),
            model_name="mock-llm",
            prompt_version="profile-test",
            prompt_profile="role-style-faithful-v4",
        )
        faithful_prompt_v5 = build_translation_prompt_request(
            TranslationTask(context_packet=context_packet, current_sentences=current_sentences),
            model_name="mock-llm",
            prompt_version="profile-test",
            prompt_profile="role-style-faithful-v5",
        )
        faithful_prompt_v6 = build_translation_prompt_request(
            TranslationTask(context_packet=context_packet, current_sentences=current_sentences),
            model_name="mock-llm",
            prompt_version="profile-test",
            prompt_profile="role-style-faithful-v6",
        )
        memory_prompt = build_translation_prompt_request(
            TranslationTask(context_packet=context_packet, current_sentences=current_sentences),
            model_name="mock-llm",
            prompt_version="profile-test",
            prompt_profile="role-style-memory-v2",
        )
        brief_prompt = build_translation_prompt_request(
            TranslationTask(context_packet=context_packet, current_sentences=current_sentences),
            model_name="mock-llm",
            prompt_version="profile-test",
            prompt_profile="role-style-brief-v3",
        )

        self.assertIn("high-fidelity book translation worker", current_prompt.system_prompt)
        self.assertIn("senior technical translator and localizer", role_prompt.system_prompt)
        self.assertIn("High fidelity comes first", faithful_prompt.system_prompt)
        self.assertIn("never promotional, chatty, or over-interpreted", faithful_prompt.system_prompt)
        self.assertIn("Chinese Style Priorities:", faithful_prompt.user_prompt)
        self.assertIn("Paragraph Intent Signal:", faithful_prompt.user_prompt)
        self.assertIn("Keep concrete imagery concrete", faithful_prompt_v5.system_prompt)
        self.assertIn("abstract noun-heavy phrasing", faithful_prompt_v5.system_prompt)
        self.assertIn("Chinese Style Priorities:", faithful_prompt_v5.user_prompt)
        self.assertIn("Paragraph Intent Signal:", faithful_prompt_v5.user_prompt)
        self.assertIn("service, marketing, or management language", faithful_prompt_v6.system_prompt)
        self.assertIn("simple ending into a slogan about consistency, care, or service", faithful_prompt_v6.system_prompt)
        self.assertIn("Chinese Style Priorities:", faithful_prompt_v6.user_prompt)
        self.assertIn("Paragraph Intent Signal:", faithful_prompt_v6.user_prompt)
        self.assertIn("follow them over generic smoothing", faithful_prompt_v6.user_prompt)
        self.assertIn("Chinese Style Priorities:", role_prompt.user_prompt)
        self.assertIn("Paragraph Intent Signal:", role_prompt.user_prompt)
        self.assertIn("Intent: definition", role_prompt.user_prompt)
        self.assertIn("Source-Aware Literalism Guardrails:", role_prompt.user_prompt)
        self.assertIn("大量证据表明", role_prompt.user_prompt)
        self.assertIn("Memory and Ambiguity Handling:", memory_prompt.user_prompt)
        self.assertIn("locked terms and chapter concept memory", memory_prompt.system_prompt)
        self.assertIn("publication-grade English-to-Chinese translator and localizer", brief_prompt.system_prompt)
        self.assertIn("Paragraph Intent Priorities:", brief_prompt.user_prompt)
        self.assertIn("Literalism Guardrails:", brief_prompt.user_prompt)
        self.assertIn("Chapter Brief as the purpose summary of this section", brief_prompt.user_prompt)

    def test_build_translation_prompt_request_includes_section_level_scaffolding(self) -> None:
        context_packet = ContextPacket(
            packet_id="pkt-scaffold",
            document_id="doc-1",
            chapter_id="ch-1",
            packet_type="translate",
            book_profile_version=1,
            chapter_brief_version=1,
            heading_path=["Chapter One", "Context Engineering"],
            current_blocks=[
                PacketBlock(
                    block_id="block-1",
                    block_type="paragraph",
                    sentence_ids=["s1"],
                    text="Context engineering is the deliberate design of inputs, memory, and tools.",
                )
            ],
            chapter_brief="This chapter explains why context engineering changes how engineers guide agentic systems.",
            section_brief="This part of the section 'Context Engineering' defines context engineering and clarifies how it should be understood in the chapter.",
            discourse_bridge=DiscourseBridge(
                previous_paragraph_role="analogy",
                current_paragraph_role="concept definition",
                relation_to_previous="moves from analogy into concept definition",
                active_referents=["context engineering", "agentic systems"],
            ),
            style_constraints={
                "paragraph_intent": "definition",
                "paragraph_intent_hint": "Treat this as concept-definition prose.",
                "literalism_guardrails": "Prefer '上下文工程' for context engineering.",
            },
            relevant_terms=[
                RelevantTerm(
                    source_term="context engineering",
                    target_term="上下文工程",
                    lock_level="locked",
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
                source_text="Context engineering is the deliberate design of inputs, memory, and tools.",
                normalized_text="Context engineering is the deliberate design of inputs, memory, and tools.",
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

        prompt_request = build_translation_prompt_request(
            TranslationTask(context_packet=context_packet, current_sentences=current_sentences),
            model_name="mock-llm",
            prompt_version="section-scaffold-test",
            prompt_profile="role-style-v2",
            allow_compact_prompt=False,
        )

        self.assertIn("Section-Level Scaffolding:", prompt_request.user_prompt)
        self.assertIn("Section Brief: This part of the section 'Context Engineering' defines context engineering", prompt_request.user_prompt)
        self.assertIn("Previous Paragraph Role: analogy", prompt_request.user_prompt)
        self.assertIn("Current Paragraph Role: concept definition", prompt_request.user_prompt)
        self.assertIn("Relation to Previous: moves from analogy into concept definition", prompt_request.user_prompt)
        self.assertIn("Active Referents: context engineering, agentic systems", prompt_request.user_prompt)

    def test_build_translation_prompt_request_supports_material_aware_profiles(self) -> None:
        technical_packet = ContextPacket(
            packet_id="pkt-tech",
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
                    text="It represents a shift from simply telling a computer what to do, to explaining why we need something done and trusting it to figure out the how.",
                )
            ],
            chapter_brief="This chapter introduces agentic systems and why they change how engineers specify work.",
            chapter_concepts=[
                ConceptCandidate(
                    source_term="agentic systems",
                    canonical_zh="智能体系统",
                    status="locked",
                    confidence=1.0,
                    times_seen=2,
                )
            ],
            style_constraints={
                "tone": "faithful-clear",
                "translation_material": "technical_book",
                "paragraph_intent": "definition",
                "literalism_guardrails": (
                    "Prefer: not-x-but-y || "
                    "Rewrite perspective shells into lighter Chinese technical prose."
                ),
            },
        )
        paper_packet = technical_packet.model_copy(
            update={
                "packet_id": "pkt-paper",
                "style_constraints": {
                    "tone": "faithful-clear",
                    "translation_material": "academic_paper",
                    "paragraph_intent": "evidence",
                },
            }
        )
        current_sentences = [
            Sentence(
                id="s1",
                block_id="block-1",
                chapter_id="ch-1",
                document_id="doc-1",
                ordinal_in_block=1,
                source_text=technical_packet.current_blocks[0].text,
                normalized_text=technical_packet.current_blocks[0].text,
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

        book_prompt = build_translation_prompt_request(
            TranslationTask(context_packet=technical_packet, current_sentences=current_sentences),
            model_name="mock-llm",
            prompt_version="material-test",
            prompt_profile="material-aware-v1",
        )
        paper_prompt = build_translation_prompt_request(
            TranslationTask(context_packet=paper_packet, current_sentences=current_sentences),
            model_name="mock-llm",
            prompt_version="material-test",
            prompt_profile="material-aware-v1",
        )
        minimal_prompt = build_translation_prompt_request(
            TranslationTask(context_packet=technical_packet, current_sentences=current_sentences),
            model_name="mock-llm",
            prompt_version="material-test",
            prompt_profile="material-aware-minimal-v1",
        )

        self.assertIn("translator of English computer-science and software-engineering books", book_prompt.system_prompt)
        self.assertIn("Material-Specific Style Target:", book_prompt.user_prompt)
        self.assertIn("native Chinese computer-science book author", book_prompt.user_prompt)
        self.assertIn("Translation Material: technical_book", book_prompt.user_prompt)
        self.assertIn("translator for computer science and machine-learning papers", paper_prompt.system_prompt)
        self.assertIn("formal Chinese academic prose", paper_prompt.user_prompt)
        self.assertIn("Translation Material: academic_paper", paper_prompt.user_prompt)
        self.assertIn("native, fluent Chinese technical prose", minimal_prompt.system_prompt)
        self.assertNotIn("Sentence Ledger:", minimal_prompt.user_prompt)
        self.assertNotIn("Translation Material: technical_book", minimal_prompt.user_prompt)
        self.assertIn("Source-Aware Literalism Guardrails:", minimal_prompt.user_prompt)
        self.assertLess(len(minimal_prompt.user_prompt), len(book_prompt.user_prompt))

    def test_context_compiler_infers_paragraph_intent_signal(self) -> None:
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
                    text="In technical terms, agentic AI refers to systems that combine a large language model with memory.",
                )
            ],
            chapter_brief="This chapter explains agentic AI and memory.",
            style_constraints={"tone": "faithful-clear"},
        )
        snapshot = MemorySnapshot(
            id="mem-1",
            document_id="doc-1",
            scope_type=MemoryScopeType.CHAPTER,
            scope_id="ch-1",
            snapshot_type=SnapshotType.CHAPTER_TRANSLATION_MEMORY,
            version=3,
            content_json={"chapter_brief": "This chapter explains agentic AI and memory."},
            status=MemoryStatus.ACTIVE,
            created_at=datetime.now(timezone.utc),
        )

        compiled = ChapterContextCompiler().compile(packet, chapter_memory_snapshot=snapshot)

        self.assertEqual(compiled.style_constraints["paragraph_intent"], "definition")
        self.assertIn("concept-definition prose", str(compiled.style_constraints["paragraph_intent_hint"]))

    def test_context_compiler_skips_low_precision_paragraph_intent_signal(self) -> None:
        packet = ContextPacket(
            packet_id="pkt-analogy",
            document_id="doc-1",
            chapter_id="ch-1",
            packet_type="translate",
            book_profile_version=1,
            chapter_brief_version=1,
            heading_path=["Chapter One"],
            current_blocks=[
                PacketBlock(
                    block_id="block-analogy",
                    block_type="paragraph",
                    sentence_ids=["s1"],
                    text="If generative AI chat is a recipe book, then agentic AI is a personal chef.",
                )
            ],
            chapter_brief="This chapter explains agentic AI through a recipe-book analogy.",
            style_constraints={"tone": "faithful-clear"},
        )
        snapshot = MemorySnapshot(
            id="mem-analogy",
            document_id="doc-1",
            scope_type=MemoryScopeType.CHAPTER,
            scope_id="ch-1",
            snapshot_type=SnapshotType.CHAPTER_TRANSLATION_MEMORY,
            version=3,
            content_json={"chapter_brief": "This chapter explains agentic AI through a recipe-book analogy."},
            status=MemoryStatus.ACTIVE,
            created_at=datetime.now(timezone.utc),
        )

        compiled = ChapterContextCompiler().compile(packet, chapter_memory_snapshot=snapshot)

        self.assertNotIn("paragraph_intent", compiled.style_constraints)
        self.assertNotIn("paragraph_intent_hint", compiled.style_constraints)

    def test_context_compiler_infers_source_aware_literalism_guardrails(self) -> None:
        packet = ContextPacket(
            packet_id="pkt-literalism",
            document_id="doc-1",
            chapter_id="ch-1",
            packet_type="translate",
            book_profile_version=1,
            chapter_brief_version=1,
            heading_path=["Chapter One"],
            current_blocks=[
                PacketBlock(
                    block_id="block-literalism",
                    block_type="paragraph",
                    sentence_ids=["s1"],
                    text=(
                        "In essence, the weight of evidence shows relying on external content "
                        "tends to yield more reliable and contextually accurate outputs."
                    ),
                )
            ],
            chapter_brief="This chapter discusses context engineering and memory.",
            style_constraints={"tone": "faithful-clear"},
        )

        compiled = ChapterContextCompiler().compile(packet, chapter_memory_snapshot=None)

        literalism = str(compiled.style_constraints["literalism_guardrails"])
        self.assertIn("Prefer: 大量证据表明 / 现有证据表明", literalism)
        self.assertIn("大量证据表明", literalism)
        self.assertIn("更符合上下文的输出", literalism)

    def test_context_compiler_infers_shift_and_vantage_literalism_guardrails(self) -> None:
        packet = ContextPacket(
            packet_id="pkt-shift-vantage",
            document_id="doc-1",
            chapter_id="ch-1",
            packet_type="translate",
            book_profile_version=1,
            chapter_brief_version=1,
            heading_path=["Foreword"],
            current_blocks=[
                PacketBlock(
                    block_id="block-shift-vantage",
                    block_type="paragraph",
                    sentence_ids=["s1"],
                    text=(
                        "It represents a shift from simply telling a computer what to do, to explaining why "
                        "we need something done and trusting it to figure out the how. "
                        "From my vantage point as the CIO of a global financial institution, the stakes are immeasurably high. "
                        "A fun anecdote is harmless; a production mistake is not."
                    ),
                )
            ],
            chapter_brief="This chapter discusses agentic systems in enterprise settings.",
            style_constraints={"tone": "faithful-clear"},
        )

        compiled = ChapterContextCompiler().compile(packet, chapter_memory_snapshot=None)

        literalism = str(compiled.style_constraints["literalism_guardrails"])
        self.assertIn("不再是……而是…… / 从……转向……", literalism)
        self.assertIn("告诉它目标与原因，让它自己决定如何实现", literalism)
        self.assertIn("从我这位……的角度看 / 站在……的角度看", literalism)
        self.assertIn("风险极高 / 代价极高", literalism)
        self.assertIn("只是个趣闻 / 只是个小插曲", literalism)

    def test_context_compiler_infers_profound_responsibility_literalism_guardrail(self) -> None:
        packet = ContextPacket(
            packet_id="pkt-responsibility",
            document_id="doc-1",
            chapter_id="ch-1",
            packet_type="translate",
            book_profile_version=1,
            chapter_brief_version=1,
            heading_path=["Chapter One"],
            current_blocks=[
                PacketBlock(
                    block_id="block-responsibility",
                    block_type="paragraph",
                    sentence_ids=["s1"],
                    text=(
                        "Deploying agentic systems in production requires a profound sense of responsibility."
                    ),
                )
            ],
            chapter_brief="This chapter discusses production-grade deployment and governance.",
            style_constraints={"tone": "faithful-clear"},
        )

        compiled = ChapterContextCompiler().compile(packet, chapter_memory_snapshot=None)

        literalism = str(compiled.style_constraints["literalism_guardrails"])
        self.assertIn("强烈的责任感 / 很强的责任意识", literalism)
        self.assertIn("深刻的责任感", literalism)

    def test_context_compiler_infers_consistency_care_literalism_guardrail(self) -> None:
        packet = ContextPacket(
            packet_id="pkt-consistency-care",
            document_id="doc-1",
            chapter_id="ch-1",
            packet_type="translate",
            book_profile_version=1,
            chapter_brief_version=1,
            heading_path=["Introduction"],
            current_blocks=[
                PacketBlock(
                    block_id="block-consistency-care",
                    block_type="paragraph",
                    sentence_ids=["s1"],
                    text=(
                        "The chef’s value is greater than just preparing a single meal, because the chef "
                        "draws on memory and experience to provide consistency and care over time."
                    ),
                )
            ],
            chapter_brief="This chapter explains agentic AI through a recipe-book versus chef analogy.",
            style_constraints={"tone": "faithful-clear"},
        )

        compiled = ChapterContextCompiler().compile(packet, chapter_memory_snapshot=None)

        literalism = str(compiled.style_constraints["literalism_guardrails"])
        self.assertIn("长期稳定、周到地照应", literalism)
        self.assertIn("提供连贯性和关怀", literalism)

    def test_context_compiler_infers_agentic_ai_term_guardrail(self) -> None:
        packet = ContextPacket(
            packet_id="pkt-agentic-ai-term",
            document_id="doc-1",
            chapter_id="ch-1",
            packet_type="translate",
            book_profile_version=1,
            chapter_brief_version=1,
            heading_path=["Introduction"],
            current_blocks=[
                PacketBlock(
                    block_id="block-agentic-ai-term",
                    block_type="paragraph",
                    sentence_ids=["s1"],
                    text="If generative AI chat is a recipe book, then agentic AI is a personal chef.",
                )
            ],
            chapter_brief="This chapter explains agentic AI through a recipe-book analogy.",
            style_constraints={"tone": "faithful-clear"},
        )

        compiled = ChapterContextCompiler().compile(packet, chapter_memory_snapshot=None)

        literalism = str(compiled.style_constraints["literalism_guardrails"])
        self.assertIn("智能体AI", literalism)
        self.assertIn("智能体式AI", literalism)

    def test_context_compiler_infers_knowledge_timeline_literalism_guardrail(self) -> None:
        packet = ContextPacket(
            packet_id="pkt-knowledge-timeline",
            document_id="doc-1",
            chapter_id="ch-1",
            packet_type="translate",
            book_profile_version=1,
            chapter_brief_version=1,
            heading_path=["Chapter One"],
            current_blocks=[
                PacketBlock(
                    block_id="block-knowledge-timeline",
                    block_type="paragraph",
                    sentence_ids=["s1"],
                    text=(
                        "Memory is also the structured record of what was known, when it was known, "
                        "and why it mattered for action."
                    ),
                )
            ],
            chapter_brief="This chapter discusses context engineering and memory.",
            style_constraints={"tone": "faithful-clear"},
        )

        compiled = ChapterContextCompiler().compile(packet, chapter_memory_snapshot=None)

        literalism = str(compiled.style_constraints["literalism_guardrails"])
        self.assertIn("avoid compressed calques like '获知时间'", literalism)
        self.assertIn("知晓这些内容的时间点", literalism)

    def test_context_compiler_infers_emerging_term_scaffolding_literalism_guardrail(self) -> None:
        packet = ContextPacket(
            packet_id="pkt-emerging-term",
            document_id="doc-1",
            chapter_id="ch-1",
            packet_type="translate",
            book_profile_version=1,
            chapter_brief_version=1,
            heading_path=["Chapter One"],
            current_blocks=[
                PacketBlock(
                    block_id="block-emerging-term",
                    block_type="paragraph",
                    sentence_ids=["s1"],
                    text=(
                        "This broader challenge is what some are beginning to call context engineering, "
                        "which is the deliberate design of how context is created, maintained, and "
                        "applied to shape reasoning."
                    ),
                )
            ],
            chapter_brief="This chapter discusses context engineering and memory.",
            style_constraints={"tone": "faithful-clear"},
        )

        compiled = ChapterContextCompiler().compile(packet, chapter_memory_snapshot=None)

        literalism = str(compiled.style_constraints["literalism_guardrails"])
        self.assertIn("Prefer: 有人开始将其称为……，它指的是……", literalism)
        self.assertIn("avoid scaffolding like '称之为……的领域/内容'", literalism)
        self.assertIn("有人开始将其称为", literalism)
        self.assertIn("rendered consistently as '上下文'", literalism)

    def test_context_compiler_infers_durable_substrate_literalism_guardrail(self) -> None:
        packet = ContextPacket(
            packet_id="pkt-durable-substrate",
            document_id="doc-1",
            chapter_id="ch-1",
            packet_type="translate",
            book_profile_version=1,
            chapter_brief_version=1,
            heading_path=["Chapter One"],
            current_blocks=[
                PacketBlock(
                    block_id="block-durable-substrate",
                    block_type="paragraph",
                    sentence_ids=["s1"],
                    text=(
                        "Within this view, memory becomes the durable substrate of context, "
                        "providing more than just raw recall of what has been said."
                    ),
                )
            ],
            chapter_brief="This chapter discusses context engineering and memory.",
            style_constraints={"tone": "faithful-clear"},
        )

        compiled = ChapterContextCompiler().compile(packet, chapter_memory_snapshot=None)

        literalism = str(compiled.style_constraints["literalism_guardrails"])
        self.assertIn("not rigid calques like '持久基底'", literalism)
        self.assertIn("使上下文得以持久存在的基础", literalism)

    def test_context_compiler_filters_stale_literalism_and_locked_term_conflict_from_previous_translations(self) -> None:
        packet = ContextPacket(
            packet_id="pkt-memory-sanitize",
            document_id="doc-1",
            chapter_id="ch-1",
            packet_type="translate",
            book_profile_version=1,
            chapter_brief_version=1,
            heading_path=["Chapter One"],
            current_blocks=[
                PacketBlock(
                    block_id="block-current",
                    block_type="paragraph",
                    sentence_ids=["s1"],
                    text="Current paragraph.",
                )
            ],
            prev_translated_blocks=[
                TranslatedContextBlock(
                    block_id="block-stale-style",
                    source_excerpt="Research further shows that in-context information can shape behavior through external context.",
                    target_excerpt="研究进一步表明，情境信息能够通过外部情境塑造行为。",
                    source_sentence_ids=["s-prev-1"],
                ),
                TranslatedContextBlock(
                    block_id="block-term-conflict",
                    source_excerpt="Agentic AI must take on more than that.",
                    target_excerpt="然而，智能体式AI必须承担更多。",
                    source_sentence_ids=["s-prev-2"],
                ),
                TranslatedContextBlock(
                    block_id="block-clean",
                    source_excerpt="Memory supports continuity across tasks.",
                    target_excerpt="记忆支持跨任务的连续性。",
                    source_sentence_ids=["s-prev-3"],
                ),
            ],
            relevant_terms=[
                RelevantTerm(source_term="Agentic AI", target_term="智能体式AI", lock_level="locked")
            ],
            chapter_brief="This chapter discusses agentic AI and memory.",
            style_constraints={"tone": "faithful-clear"},
        )

        compiled = ChapterContextCompiler().compile(packet, chapter_memory_snapshot=None)

        self.assertEqual(len(compiled.prev_translated_blocks), 1)
        self.assertEqual(compiled.prev_translated_blocks[0].block_id, "block-clean")
        self.assertEqual(compiled.relevant_terms[0].target_term, "智能体AI")

    def test_context_compiler_can_disable_source_aware_literalism_guardrails(self) -> None:
        packet = ContextPacket(
            packet_id="pkt-literalism",
            document_id="doc-1",
            chapter_id="ch-1",
            packet_type="translate",
            book_profile_version=1,
            chapter_brief_version=1,
            heading_path=["Chapter One"],
            current_blocks=[
                PacketBlock(
                    block_id="block-literalism",
                    block_type="paragraph",
                    sentence_ids=["s1"],
                    text=(
                        "In essence, the weight of evidence shows relying on external content "
                        "tends to yield more reliable and contextually accurate outputs."
                    ),
                )
            ],
            chapter_brief="This chapter discusses context engineering and memory.",
            style_constraints={"tone": "faithful-clear"},
        )

        compiled = ChapterContextCompiler().compile(
            packet,
            chapter_memory_snapshot=None,
            options=ChapterContextCompileOptions(include_literalism_guardrails=False),
        )

        self.assertNotIn("literalism_guardrails", compiled.style_constraints)

    def test_context_compiler_trims_default_source_context_and_brief_for_self_contained_packet(self) -> None:
        packet = ContextPacket(
            packet_id="pkt-trim",
            document_id="doc-1",
            chapter_id="ch-1",
            packet_type="translate",
            book_profile_version=1,
            chapter_brief_version=1,
            heading_path=["Chapter One"],
            current_blocks=[
                PacketBlock(
                    block_id="block-current",
                    block_type="paragraph",
                    sentence_ids=["s1", "s2"],
                    text=(
                        "Agentic AI systems coordinate memory, tools, and planning into a single operating loop. "
                        "This paragraph is long enough to stand on its own without relying on neighboring blocks. "
                        "It also names the operational consequences directly, so the local meaning does not depend on a prior paragraph."
                    ),
                )
            ],
            prev_blocks=[
                PacketBlock(
                    block_id="block-prev-1",
                    block_type="paragraph",
                    sentence_ids=["sp1"],
                    text="Previous context one.",
                ),
                PacketBlock(
                    block_id="block-prev-2",
                    block_type="paragraph",
                    sentence_ids=["sp2"],
                    text="Previous context two.",
                ),
            ],
            next_blocks=[
                PacketBlock(
                    block_id="block-next-1",
                    block_type="paragraph",
                    sentence_ids=["sn1"],
                    text="Next context one.",
                ),
                PacketBlock(
                    block_id="block-next-2",
                    block_type="paragraph",
                    sentence_ids=["sn2"],
                    text="Next context two.",
                ),
            ],
            chapter_brief=(
                "This chapter explains agentic AI, context engineering, memory, planning, and tool use in detail. "
                "It also frames how these pieces fit together in production systems and why the operating loop matters."
            ),
            style_constraints={"tone": "faithful-clear"},
        )

        compiled = ChapterContextCompiler().compile(packet, chapter_memory_snapshot=None)

        self.assertEqual(compiled.prev_blocks, [])
        self.assertEqual(compiled.next_blocks, [])
        self.assertEqual(compiled.chapter_brief, _compress_chapter_brief(packet.chapter_brief or ""))

    def test_context_compiler_drops_previous_translations_for_self_contained_long_paragraph(self) -> None:
        packet = ContextPacket(
            packet_id="pkt-prev-translation-trim",
            document_id="doc-1",
            chapter_id="ch-1",
            packet_type="translate",
            book_profile_version=1,
            chapter_brief_version=1,
            heading_path=["Chapter One"],
            current_blocks=[
                PacketBlock(
                    block_id="block-current",
                    block_type="paragraph",
                    sentence_ids=["s1", "s2"],
                    text=(
                        "If generative AI chat is a recipe book, then agentic AI is a personal chef. "
                        "A chef remembers what you liked last week and keeps track of what is in the fridge."
                    ),
                )
            ],
            prev_translated_blocks=[
                TranslatedContextBlock(
                    block_id="block-prev-1",
                    source_excerpt="Prior reference one.",
                    target_excerpt="上一段引用一。",
                    source_sentence_ids=["sp1"],
                ),
                TranslatedContextBlock(
                    block_id="block-prev-2",
                    source_excerpt="Prior reference two.",
                    target_excerpt="上一段引用二。",
                    source_sentence_ids=["sp2"],
                ),
            ],
            chapter_brief="This chapter explains agentic AI through a recipe-book analogy.",
            style_constraints={"tone": "faithful-clear"},
        )

        compiled = ChapterContextCompiler().compile(packet, chapter_memory_snapshot=None)

        self.assertEqual(compiled.prev_translated_blocks, [])
        self.assertEqual(
            compiled.compile_metadata.get("selected_prev_translated_block_count"),
            0,
        )

    def test_context_compiler_keeps_minimal_context_for_short_bridge_paragraph(self) -> None:
        packet = ContextPacket(
            packet_id="pkt-short-bridge",
            document_id="doc-1",
            chapter_id="ch-1",
            packet_type="translate",
            book_profile_version=1,
            chapter_brief_version=1,
            heading_path=["Chapter One"],
            current_blocks=[
                PacketBlock(
                    block_id="block-current",
                    block_type="paragraph",
                    sentence_ids=["s1"],
                    text="However, this changes in practice.",
                )
            ],
            prev_blocks=[
                PacketBlock(
                    block_id="block-prev-1",
                    block_type="paragraph",
                    sentence_ids=["sp1"],
                    text="Previous context one.",
                ),
                PacketBlock(
                    block_id="block-prev-2",
                    block_type="paragraph",
                    sentence_ids=["sp2"],
                    text="Previous context two.",
                ),
            ],
            next_blocks=[
                PacketBlock(
                    block_id="block-next-1",
                    block_type="paragraph",
                    sentence_ids=["sn1"],
                    text="Next context one.",
                ),
                PacketBlock(
                    block_id="block-next-2",
                    block_type="paragraph",
                    sentence_ids=["sn2"],
                    text="Next context two.",
                ),
            ],
            chapter_brief=(
                "This chapter explains how execution reality diverges from the simplified intuition presented earlier. "
                "The paragraph is a transition into practical constraints."
            ),
            style_constraints={"tone": "faithful-clear"},
        )

        compiled = ChapterContextCompiler().compile(packet, chapter_memory_snapshot=None)

        self.assertEqual([block.block_id for block in compiled.prev_blocks], ["block-prev-2"])
        self.assertEqual(compiled.next_blocks, [])
        self.assertEqual(compiled.chapter_brief, _compress_chapter_brief(packet.chapter_brief or ""))

    def test_context_compiler_prefers_previous_translations_over_raw_source_context_when_available(self) -> None:
        packet = ContextPacket(
            packet_id="pkt-prev-translation-primary",
            document_id="doc-1",
            chapter_id="ch-1",
            packet_type="translate",
            book_profile_version=1,
            chapter_brief_version=1,
            heading_path=["Chapter One"],
            current_blocks=[
                PacketBlock(
                    block_id="block-current",
                    block_type="paragraph",
                    sentence_ids=["s1"],
                    text="However, this changes in practice.",
                )
            ],
            prev_blocks=[
                PacketBlock(
                    block_id="block-prev-1",
                    block_type="paragraph",
                    sentence_ids=["sp1"],
                    text="Previous context one.",
                ),
                PacketBlock(
                    block_id="block-prev-2",
                    block_type="paragraph",
                    sentence_ids=["sp2"],
                    text="Previous context two.",
                ),
            ],
            next_blocks=[
                PacketBlock(
                    block_id="block-next-1",
                    block_type="paragraph",
                    sentence_ids=["sn1"],
                    text="Next context one.",
                ),
            ],
            prev_translated_blocks=[
                TranslatedContextBlock(
                    block_id="block-prev-2",
                    source_excerpt="Previous context two.",
                    target_excerpt="上一段上下文二。",
                    source_sentence_ids=["sp2"],
                )
            ],
            chapter_brief=(
                "This chapter explains how execution reality diverges from the simplified intuition presented earlier. "
                "The paragraph is a transition into practical constraints."
            ),
            style_constraints={"tone": "faithful-clear"},
        )

        compiled = ChapterContextCompiler().compile(packet, chapter_memory_snapshot=None)

        self.assertEqual(compiled.prev_blocks, [])
        self.assertEqual(compiled.next_blocks, [])
        self.assertEqual(compiled.chapter_brief, _compress_chapter_brief(packet.chapter_brief or ""))
        self.assertTrue(compiled.style_constraints.get("suppress_chapter_brief_in_prompt"))
        self.assertEqual(len(compiled.prev_translated_blocks), 1)

    def test_context_compiler_keeps_brief_for_shift_statement_even_with_previous_translations(self) -> None:
        packet = ContextPacket(
            packet_id="pkt-shift-brief",
            document_id="doc-1",
            chapter_id="ch-1",
            packet_type="translate",
            book_profile_version=1,
            chapter_brief_version=1,
            heading_path=["Chapter One"],
            current_blocks=[
                PacketBlock(
                    block_id="block-current",
                    block_type="paragraph",
                    sentence_ids=["s1"],
                    text=(
                        "It represents a shift from simply telling a computer what to do, "
                        "to explaining why we need something done and trusting it to figure out the how."
                    ),
                )
            ],
            prev_blocks=[
                PacketBlock(
                    block_id="block-prev-1",
                    block_type="paragraph",
                    sentence_ids=["sp1"],
                    text="Previous context one.",
                )
            ],
            prev_translated_blocks=[
                TranslatedContextBlock(
                    block_id="block-prev-1",
                    source_excerpt="Previous context one.",
                    target_excerpt="上一段上下文一。",
                    source_sentence_ids=["sp1"],
                )
            ],
            chapter_brief="This chapter explains why agentic systems mark a shift in how engineers specify work.",
            style_constraints={"tone": "faithful-clear"},
        )

        compiled = ChapterContextCompiler().compile(packet, chapter_memory_snapshot=None)

        self.assertEqual(compiled.prev_blocks, [])
        self.assertEqual(
            compiled.chapter_brief,
            _compress_chapter_brief(packet.chapter_brief or ""),
        )
        self.assertNotIn("suppress_chapter_brief_in_prompt", compiled.style_constraints)

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
                    text="Current paragraph text about context engineering.",
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
                "chapter_brief_version": 5,
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
                include_paragraph_intent=False,
            ),
        )
        self.assertEqual(compiled_without_memory.chapter_brief, "Packet brief.")
        self.assertEqual(len(compiled_without_memory.prev_translated_blocks), 0)
        self.assertEqual(len(compiled_without_memory.chapter_concepts), 0)
        self.assertNotIn("paragraph_intent", compiled_without_memory.style_constraints)

    def test_context_compiler_prefers_newer_packet_brief_over_stale_memory_brief(self) -> None:
        packet = ContextPacket(
            packet_id="pkt-fresh-brief",
            document_id="doc-1",
            chapter_id="ch-1",
            packet_type="translate",
            book_profile_version=1,
            chapter_brief_version=3,
            heading_path=["Chapter One"],
            current_blocks=[
                PacketBlock(
                    block_id="block-1",
                    block_type="paragraph",
                    sentence_ids=["s1"],
                    text="Current paragraph text about context engineering.",
                )
            ],
            prev_translated_blocks=[],
            chapter_brief="Fresh packet brief with updated context engineering framing.",
        )
        snapshot = MemorySnapshot(
            id="mem-stale-brief",
            document_id="doc-1",
            scope_type=MemoryScopeType.CHAPTER,
            scope_id="ch-1",
            snapshot_type=SnapshotType.CHAPTER_TRANSLATION_MEMORY,
            version=2,
            content_json={
                "chapter_brief": "Old memory brief.",
                "chapter_brief_version": 1,
            },
            status=MemoryStatus.ACTIVE,
            created_at=datetime.now(timezone.utc),
        )

        compiled = ChapterContextCompiler().compile(packet, chapter_memory_snapshot=snapshot)

        self.assertEqual(
            compiled.chapter_brief,
            "Fresh packet brief with updated context engineering framing.",
        )

    def test_context_compiler_filters_chapter_concepts_to_local_relevance(self) -> None:
        packet = ContextPacket(
            packet_id="pkt-concepts",
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
                    text="This section explains durable memory for agent systems.",
                )
            ],
            prev_translated_blocks=[],
            chapter_brief=None,
        )
        snapshot = MemorySnapshot(
            id="mem-2",
            document_id="doc-1",
            scope_type=MemoryScopeType.CHAPTER,
            scope_id="ch-1",
            snapshot_type=SnapshotType.CHAPTER_TRANSLATION_MEMORY,
            version=4,
            content_json={
                "chapter_brief": "Memory brief.",
                "active_concepts": [
                    {
                        "source_term": "durable memory",
                        "canonical_zh": "持久记忆",
                        "status": "locked",
                        "times_seen": 4,
                    },
                    {
                        "source_term": "context engineering",
                        "canonical_zh": "上下文工程",
                        "status": "locked",
                        "times_seen": 6,
                    },
                ],
            },
            status=MemoryStatus.ACTIVE,
            created_at=datetime.now(timezone.utc),
        )

        compiled = ChapterContextCompiler().compile(packet, chapter_memory_snapshot=snapshot)

        self.assertEqual([concept.source_term for concept in compiled.chapter_concepts], ["durable memory"])
        self.assertEqual([term.source_term for term in compiled.relevant_terms], ["durable memory"])

    def test_context_compiler_builds_section_brief_and_discourse_bridge(self) -> None:
        packet = ContextPacket(
            packet_id="pkt-discourse",
            document_id="doc-1",
            chapter_id="ch-1",
            packet_type="translate",
            book_profile_version=1,
            chapter_brief_version=1,
            heading_path=["Chapter One", "Context Engineering"],
            current_blocks=[
                PacketBlock(
                    block_id="block-current",
                    block_type="paragraph",
                    sentence_ids=["s1"],
                    text="Context engineering is the deliberate design of context, memory, and tools for an agentic system.",
                )
            ],
            prev_blocks=[
                PacketBlock(
                    block_id="block-prev",
                    block_type="paragraph",
                    sentence_ids=["sp1"],
                    text="If chat is a recipe book, agentic systems behave more like a personal chef.",
                )
            ],
            chapter_brief="This chapter explains why context engineering changes how engineers guide agentic systems.",
            relevant_terms=[
                RelevantTerm(
                    source_term="context engineering",
                    target_term="上下文工程",
                    lock_level="locked",
                ),
                RelevantTerm(
                    source_term="agentic systems",
                    target_term="智能体式系统",
                    lock_level="locked",
                ),
            ],
        )

        compiled = ChapterContextCompiler().compile(packet, chapter_memory_snapshot=None)

        self.assertIsNotNone(compiled.section_brief)
        self.assertIn("defines context engineering", compiled.section_brief or "")
        self.assertIsNotNone(compiled.discourse_bridge)
        assert compiled.discourse_bridge is not None
        self.assertEqual(compiled.discourse_bridge.previous_paragraph_role, "analogy")
        self.assertEqual(compiled.discourse_bridge.current_paragraph_role, "concept definition")
        self.assertEqual(
            compiled.discourse_bridge.relation_to_previous,
            "moves from analogy into concept definition",
        )
        self.assertEqual(
            compiled.discourse_bridge.active_referents,
            ["context engineering", "agentic systems"],
        )

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
                    include_paragraph_intent=False,
                    include_literalism_guardrails=False,
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
        self.assertFalse(artifacts.payload["options"]["include_paragraph_intent"])
        self.assertFalse(artifacts.payload["options"]["include_literalism_guardrails"])
        self.assertTrue(artifacts.payload["options"]["prefer_previous_translations_over_source_context"])
        self.assertEqual(artifacts.payload["options"]["prompt_layout"], "sentence-led")
        self.assertEqual(artifacts.payload["options"]["prompt_profile"], "role-style-faithful-v6")
        self.assertFalse(artifacts.payload["options"]["execute"])
        self.assertIsNone(artifacts.payload["worker_output"])
        self.assertEqual(artifacts.payload["worker_metadata"]["worker_name"], "planned::echo")
        self.assertIn("context_sources", artifacts.payload)
        self.assertIn("prompt_stats", artifacts.payload)
        self.assertIn("chapter_memory_snapshot_id", artifacts.payload)
        self.assertIn("chapter_memory_snapshot_version", artifacts.payload)
        self.assertIn("compiled_prev_block_count", artifacts.payload["context_sources"])
        self.assertIn("compiled_next_block_count", artifacts.payload["context_sources"])
        self.assertIn("prompt_chapter_brief_present", artifacts.payload["context_sources"])
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

    def test_packet_experiment_service_supports_material_profile_override(self) -> None:
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
                    prompt_profile="material-aware-minimal-v1",
                    material_profile_override="academic_paper",
                ),
            )

        self.assertEqual(artifacts.payload["options"]["material_profile_override"], "academic_paper")
        self.assertEqual(artifacts.payload["context_sources"]["translation_material"], "academic_paper")
        self.assertNotIn("Translation Material:", artifacts.payload["prompt_request"]["user_prompt"])
        self.assertGreater(artifacts.payload["prompt_stats"]["total_prompt_chars"], 0)

    def test_packet_experiment_service_rerun_hints_appear_in_prompt(self) -> None:
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
                    rerun_hints=(
                        "Rerun focus [context_engineering_literal]: prefer '上下文工程' over literal phrasing in this packet.",
                    ),
                ),
            )

        self.assertIn("Open Questions and Rerun Hints:", artifacts.payload["prompt_request"]["user_prompt"])
        self.assertIn("prefer '上下文工程' over literal phrasing", artifacts.payload["prompt_request"]["user_prompt"])
        self.assertEqual(
            artifacts.payload["options"]["rerun_hints"][0],
            "Rerun focus [context_engineering_literal]: prefer '上下文工程' over literal phrasing in this packet.",
        )

    def test_packet_experiment_service_can_resolve_review_issue_ids_into_rerun_context(self) -> None:
        document_id, packet_ids = self._bootstrap_to_db()
        packet_id = packet_ids[2]

        with self.session_factory() as session:
            repository = TranslationRepository(session)
            bundle = repository.load_packet_bundle(packet_id)
            now = datetime.now(timezone.utc)
            issue = ReviewIssue(
                id="issue-style-1",
                document_id=document_id,
                chapter_id=bundle.packet.chapter_id,
                block_id=None,
                sentence_id=bundle.current_sentences[0].id,
                packet_id=packet_id,
                issue_type="STYLE_DRIFT",
                root_cause_layer=RootCauseLayer.PACKET,
                severity=Severity.MEDIUM,
                blocking=False,
                detector=Detector.RULE,
                confidence=1.0,
                evidence_json={
                    "style_rule": "contextually_accurate_outputs_literal",
                    "preferred_hint": "更符合上下文的输出",
                    "prompt_guidance": (
                        "Prefer '更符合上下文的输出' or an equally natural Chinese expression, "
                        "not literal forms like '上下文更准确的输出'."
                    ),
                },
                status=IssueStatus.OPEN,
                created_at=now,
                updated_at=now,
            )
            session.add(issue)
            session.commit()

            service = PacketExperimentService(
                repository,
                settings=Settings(translation_backend="echo", translation_model="echo-worker"),
            )
            artifacts = service.run(
                packet_id,
                PacketExperimentOptions(
                    prompt_layout="paragraph-led",
                    review_issue_ids=(issue.id,),
                ),
            )

        self.assertIn("Open Questions and Rerun Hints:", artifacts.payload["prompt_request"]["user_prompt"])
        self.assertIn(
            "Rerun focus [contextually_accurate_outputs_literal]: prefer '更符合上下文的输出' over literal phrasing in this packet.",
            artifacts.payload["prompt_request"]["user_prompt"],
        )
        self.assertIn(
            "Rerun guidance [contextually_accurate_outputs_literal]: Prefer '更符合上下文的输出' or an equally natural Chinese expression, not literal forms like '上下文更准确的输出'.",
            artifacts.payload["prompt_request"]["user_prompt"],
        )
        self.assertEqual(artifacts.payload["options"]["review_issue_ids"], [issue.id])
        self.assertEqual(artifacts.payload["rerun_context"]["review_issue_count"], 1)
        self.assertEqual(
            artifacts.payload["rerun_context"]["resolved_rerun_hints"],
            [
                "Rerun focus [contextually_accurate_outputs_literal]: prefer '更符合上下文的输出' over literal phrasing in this packet.",
                "Rerun guidance [contextually_accurate_outputs_literal]: Prefer '更符合上下文的输出' or an equally natural Chinese expression, not literal forms like '上下文更准确的输出'.",
            ],
        )

    def test_translation_service_execute_packet_accepts_rerun_overrides_and_hints(self) -> None:
        _, packet_ids = self._bootstrap_to_db()
        packet_id = packet_ids[2]
        fake_client = FakeTranslationClient()
        worker = LLMTranslationWorker(
            fake_client,
            model_name="mock-llm",
            prompt_version="rerun-hints-test",
            runtime_config={"provider": "fake"},
        )

        with self.session_factory() as session:
            repository = TranslationRepository(session)
            service = TranslationService(repository, worker=worker)
            service.execute_packet(
                packet_id,
                compile_options=ChapterContextCompileOptions(
                    concept_overrides=(
                        ConceptCandidate(
                            source_term="context engineering",
                            canonical_zh="上下文工程",
                            status="locked",
                            confidence=1.0,
                        ),
                    ),
                ),
                rerun_hints=(
                    "Rerun focus [context_engineering_literal]: prefer '上下文工程' over literal phrasing in this packet.",
                ),
            )

        self.assertEqual(len(fake_client.requests), 1)
        self.assertIn("context engineering => 上下文工程 (locked)", fake_client.requests[0].user_prompt)
        self.assertIn("Open Questions and Rerun Hints:", fake_client.requests[0].user_prompt)
        self.assertIn("prefer '上下文工程' over literal phrasing", fake_client.requests[0].user_prompt)

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
        self.assertFalse(diff.payload["summary"]["prompt_profile_changed"])
        self.assertTrue(diff.payload["summary"]["chapter_brief_changed"])
        self.assertTrue(diff.payload["summary"]["chapter_brief_source_changed"])
        self.assertTrue(diff.payload["summary"]["previous_translation_count_changed"])
        self.assertTrue(diff.payload["summary"]["chapter_concept_count_changed"])
        self.assertTrue(diff.payload["summary"]["user_prompt_changed"])
        self.assertTrue(diff.payload["summary"]["worker_output_presence_changed"])
        self.assertTrue(diff.payload["summary"]["prompt_size_changed"])
        self.assertIn("Current Sentences", "\n".join(diff.payload["prompt_delta"]["user_prompt_unified_diff"]))
        self.assertEqual(diff.payload["context_delta"]["previous_translation_count"]["delta"], 1)
        self.assertEqual(
            diff.payload["context_delta"]["context_sources"]["chapter_brief_source"]["cand"],
            "memory",
        )
        self.assertIn("prompt_stats", diff.payload["prompt_delta"])

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
            now = datetime.now(timezone.utc)
            session.add_all(
                [
                    ReviewIssue(
                        id="issue-style-a",
                        document_id=document_id,
                        chapter_id=chapter_id,
                        block_id=None,
                        sentence_id=None,
                        packet_id=packet_a_id,
                        issue_type="STYLE_DRIFT",
                        root_cause_layer=RootCauseLayer.PACKET,
                        severity=Severity.MEDIUM,
                        blocking=False,
                        detector=Detector.RULE,
                        confidence=1.0,
                        evidence_json={},
                        status=IssueStatus.OPEN,
                        created_at=now,
                        updated_at=now,
                    ),
                    ReviewIssue(
                        id="issue-term-b",
                        document_id=document_id,
                        chapter_id=chapter_id,
                        block_id=None,
                        sentence_id=None,
                        packet_id=packet_b_id,
                        issue_type="TERM_CONFLICT",
                        root_cause_layer=RootCauseLayer.PACKET,
                        severity=Severity.MEDIUM,
                        blocking=False,
                        detector=Detector.RULE,
                        confidence=1.0,
                        evidence_json={},
                        status=IssueStatus.OPEN,
                        created_at=now,
                        updated_at=now,
                    ),
                    ReviewIssue(
                        id="issue-style-b",
                        document_id=document_id,
                        chapter_id=chapter_id,
                        block_id=None,
                        sentence_id=None,
                        packet_id=packet_b_id,
                        issue_type="STYLE_DRIFT",
                        root_cause_layer=RootCauseLayer.PACKET,
                        severity=Severity.MEDIUM,
                        blocking=False,
                        detector=Detector.RULE,
                        confidence=1.0,
                        evidence_json={},
                        status=IssueStatus.OPEN,
                        created_at=now,
                        updated_at=now,
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
        self.assertEqual(artifacts.payload["unresolved_packet_issue_count"], 3)
        self.assertEqual(artifacts.payload["top_candidate"]["packet_id"], packet_a_id)
        self.assertGreater(artifacts.payload["entries"][0]["memory_signal_score"], artifacts.payload["entries"][1]["memory_signal_score"])
        entry_by_packet = {entry["packet_id"]: entry for entry in artifacts.payload["entries"]}
        self.assertEqual(entry_by_packet[packet_a_id]["unresolved_issue_count"], 1)
        self.assertEqual(entry_by_packet[packet_a_id]["issue_priority_tier"], 1)
        self.assertFalse(entry_by_packet[packet_a_id]["has_non_style_issue"])
        self.assertEqual(entry_by_packet[packet_b_id]["unresolved_issue_count"], 2)
        self.assertEqual(entry_by_packet[packet_b_id]["issue_priority_tier"], 0)
        self.assertTrue(entry_by_packet[packet_b_id]["has_non_style_issue"])
        self.assertTrue(entry_by_packet[packet_b_id]["mixed_issue_types"])
        self.assertEqual(
            entry_by_packet[packet_b_id]["unresolved_issue_types"],
            ["STYLE_DRIFT", "TERM_CONFLICT"],
        )

    def test_translation_chapter_smoke_selects_top_packets_and_summarizes_style_drift(self) -> None:
        packet_a_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"
        packet_b_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2"

        class StubScanService:
            def scan_chapter(self, chapter_id: str, *, options: PacketExperimentOptions | None = None):
                return type(
                    "Artifacts",
                    (),
                    {
                        "payload": {
                            "chapter_id": chapter_id,
                            "packet_count": 2,
                            "entries": [
                                {
                                    "packet_id": packet_a_id,
                                    "memory_signal_score": 250,
                                    "current_sentence_count": 2,
                                },
                                {
                                    "packet_id": packet_b_id,
                                    "memory_signal_score": 100,
                                    "current_sentence_count": 1,
                                },
                            ],
                        }
                    },
                )()

        class StubExperimentService:
            def run(self, packet_id: str, options: PacketExperimentOptions):
                payloads = {
                    packet_a_id: {
                        "context_packet": {
                            "current_blocks": [
                                {
                                    "block_id": "block-a",
                                    "block_type": "paragraph",
                                    "sentence_ids": ["s1"],
                                    "text": "This broader challenge is what some are beginning to call context engineering.",
                                }
                            ]
                        },
                        "context_sources": {"compiled_prev_translated_count": 2},
                        "worker_output": {
                            "target_segments": [
                                {
                                    "text_zh": "这一挑战正被称为情境工程。",
                                    "source_sentence_ids": ["s1"],
                                }
                            ],
                            "alignment_suggestions": [{"source_sentence_ids": ["s1"]}],
                            "low_confidence_flags": [],
                        },
                        "usage": {"cost_usd": 0.1},
                        "prompt_request": {"user_prompt": "prompt-a"},
                    },
                    packet_b_id: {
                        "context_packet": {
                            "current_blocks": [
                                {
                                    "block_id": "block-b",
                                    "block_type": "paragraph",
                                    "sentence_ids": ["s1"],
                                    "text": "This paragraph discusses distributed SQL foundations.",
                                }
                            ]
                        },
                        "context_sources": {"compiled_prev_translated_count": 1},
                        "worker_output": {
                            "target_segments": [
                                {
                                    "text_zh": "这一挑战正被称为上下文工程。",
                                    "source_sentence_ids": ["s1"],
                                }
                            ],
                            "alignment_suggestions": [{"source_sentence_ids": ["s1"]}],
                            "low_confidence_flags": [],
                        },
                        "usage": {"cost_usd": 0.2},
                        "prompt_request": {"user_prompt": "prompt-b"},
                    },
                }
                return type("Artifacts", (), {"payload": payloads[packet_id]})()

        smoke_service = TranslationChapterSmokeService(
            experiment_service=StubExperimentService(),
            scan_service=StubScanService(),
        )
        artifacts = smoke_service.run_chapter(
            "chapter-1",
            options=TranslationChapterSmokeOptions(selected_packet_limit=1, execute_selected=True),
        )

        self.assertEqual(artifacts.payload["selected_packet_ids"], [packet_a_id])
        self.assertEqual(artifacts.payload["aggregate_summary"]["selected_packet_count"], 1)
        self.assertEqual(artifacts.payload["aggregate_summary"]["total_style_drift_hits"], 1)
        self.assertEqual(artifacts.payload["packet_results"][0]["style_drift_hits"], ["context_engineering_literal"])
        self.assertEqual(artifacts.payload["aggregate_summary"]["total_cost_usd"], 0.1)

    def test_translation_chapter_smoke_prioritizes_issue_driven_packets_by_default(self) -> None:
        packet_a_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"
        packet_b_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2"

        class StubScanService:
            def scan_chapter(self, chapter_id: str, *, options: PacketExperimentOptions | None = None):
                return type(
                    "Artifacts",
                    (),
                    {
                        "payload": {
                            "chapter_id": chapter_id,
                            "packet_count": 2,
                            "entries": [
                                {
                                    "packet_id": packet_a_id,
                                    "memory_signal_score": 300,
                                    "current_sentence_count": 2,
                                    "unresolved_issue_count": 2,
                                    "style_drift_issue_count": 2,
                                    "non_style_issue_count": 0,
                                    "has_non_style_issue": False,
                                    "mixed_issue_types": False,
                                    "issue_priority_tier": 1,
                                    "issue_priority_reason": "style_only",
                                },
                                {
                                    "packet_id": packet_b_id,
                                    "memory_signal_score": 120,
                                    "current_sentence_count": 1,
                                    "unresolved_issue_count": 2,
                                    "style_drift_issue_count": 1,
                                    "non_style_issue_count": 1,
                                    "has_non_style_issue": True,
                                    "mixed_issue_types": True,
                                    "issue_priority_tier": 0,
                                    "issue_priority_reason": "mixed_or_non_style",
                                },
                            ],
                        }
                    },
                )()

        class StubExperimentService:
            def run(self, packet_id: str, options: PacketExperimentOptions):
                return type(
                    "Artifacts",
                    (),
                    {
                        "payload": {
                            "context_packet": {"current_blocks": []},
                            "context_sources": {},
                            "worker_output": {
                                "target_segments": [],
                                "alignment_suggestions": [],
                                "low_confidence_flags": [],
                            },
                            "usage": {"cost_usd": 0.0},
                            "prompt_request": {"user_prompt": "prompt"},
                        }
                    },
                )()

        smoke_service = TranslationChapterSmokeService(
            experiment_service=StubExperimentService(),
            scan_service=StubScanService(),
        )
        artifacts = smoke_service.run_chapter(
            "chapter-1",
            options=TranslationChapterSmokeOptions(selected_packet_limit=1, execute_selected=True),
        )

        self.assertEqual(artifacts.payload["selected_packet_ids"], [packet_b_id])
        self.assertEqual(artifacts.payload["aggregate_summary"]["selected_mixed_issue_packet_count"], 1)
        self.assertEqual(artifacts.payload["aggregate_summary"]["selected_non_style_issue_packet_count"], 1)
        self.assertEqual(artifacts.payload["packet_results"][0]["issue_priority_tier"], 0)

    def test_translation_chapter_smoke_can_disable_issue_priority_and_keep_scan_order(self) -> None:
        packet_a_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"
        packet_b_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2"

        class StubScanService:
            def scan_chapter(self, chapter_id: str, *, options: PacketExperimentOptions | None = None):
                return type(
                    "Artifacts",
                    (),
                    {
                        "payload": {
                            "chapter_id": chapter_id,
                            "packet_count": 2,
                            "entries": [
                                {
                                    "packet_id": packet_a_id,
                                    "memory_signal_score": 300,
                                    "current_sentence_count": 2,
                                    "unresolved_issue_count": 2,
                                    "style_drift_issue_count": 2,
                                    "non_style_issue_count": 0,
                                    "has_non_style_issue": False,
                                    "mixed_issue_types": False,
                                    "issue_priority_tier": 1,
                                },
                                {
                                    "packet_id": packet_b_id,
                                    "memory_signal_score": 120,
                                    "current_sentence_count": 1,
                                    "unresolved_issue_count": 2,
                                    "style_drift_issue_count": 1,
                                    "non_style_issue_count": 1,
                                    "has_non_style_issue": True,
                                    "mixed_issue_types": True,
                                    "issue_priority_tier": 0,
                                },
                            ],
                        }
                    },
                )()

        class StubExperimentService:
            def run(self, packet_id: str, options: PacketExperimentOptions):
                return type(
                    "Artifacts",
                    (),
                    {
                        "payload": {
                            "context_packet": {"current_blocks": []},
                            "context_sources": {},
                            "worker_output": {
                                "target_segments": [],
                                "alignment_suggestions": [],
                                "low_confidence_flags": [],
                            },
                            "usage": {"cost_usd": 0.0},
                            "prompt_request": {"user_prompt": "prompt"},
                        }
                    },
                )()

        smoke_service = TranslationChapterSmokeService(
            experiment_service=StubExperimentService(),
            scan_service=StubScanService(),
        )
        artifacts = smoke_service.run_chapter(
            "chapter-1",
            options=TranslationChapterSmokeOptions(
                selected_packet_limit=1,
                execute_selected=True,
                prefer_issue_driven_packets=False,
            ),
        )

        self.assertEqual(artifacts.payload["selected_packet_ids"], [packet_a_id])

    def test_translation_chapter_smoke_requires_source_pattern_match_for_style_drift(self) -> None:
        packet_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"

        class StubScanService:
            def scan_chapter(self, chapter_id: str, *, options: PacketExperimentOptions | None = None):
                return type(
                    "Artifacts",
                    (),
                    {
                        "payload": {
                            "chapter_id": chapter_id,
                            "packet_count": 1,
                            "entries": [
                                {
                                    "packet_id": packet_id,
                                    "memory_signal_score": 100,
                                    "current_sentence_count": 1,
                                }
                            ],
                        }
                    },
                )()

        class StubExperimentService:
            def run(self, packet_id: str, options: PacketExperimentOptions):
                return type(
                    "Artifacts",
                    (),
                    {
                        "payload": {
                            "context_packet": {
                                "current_blocks": [
                                    {
                                        "block_id": "block-1",
                                        "block_type": "paragraph",
                                        "sentence_ids": ["s1"],
                                        "text": "This paragraph discusses distributed SQL foundations.",
                                    }
                                ]
                            },
                            "worker_output": {
                                "target_segments": [
                                    {
                                        "text_zh": "这一挑战正被一些人称为情境工程。",
                                        "source_sentence_ids": ["s1"],
                                    }
                                ],
                                "alignment_suggestions": [{"source_sentence_ids": ["s1"]}],
                                "low_confidence_flags": [],
                            },
                            "usage": {"cost_usd": 0.1},
                            "prompt_request": {"user_prompt": "prompt-a"},
                        }
                    },
                )()

        smoke_service = TranslationChapterSmokeService(
            experiment_service=StubExperimentService(),
            scan_service=StubScanService(),
        )
        artifacts = smoke_service.run_chapter(
            "chapter-1",
            options=TranslationChapterSmokeOptions(selected_packet_limit=1, execute_selected=True),
        )

        self.assertEqual(artifacts.payload["aggregate_summary"]["total_style_drift_hits"], 0)
        self.assertEqual(artifacts.payload["packet_results"][0]["style_drift_hits"], [])

    def test_translation_service_reuses_chapter_memory_across_nonadjacent_packets(self) -> None:
        chapter_xhtml = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>First memory paragraph introduces the core concept. It explains why continuity matters. It also frames the upcoming discussion. Finally, it anchors the terminology for later packets.</p>
    <p>Second memory paragraph extends the discussion. It keeps the concept active in local discourse. It adds another angle on the same idea. Finally, it reinforces the naming choice.</p>
    <p>Third memory paragraph keeps building the case. It connects the concept to runtime behavior. It highlights the operational consequence. Finally, it closes the local argument before the next packet.</p>
    <p>Fourth memory paragraph revisits the core concept in a new light. It introduces a contrast with prior assumptions. It preserves the same terminology across packets. Finally, it gives the translator another place to reuse memory.</p>
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
            "First memory paragraph introduces the core concept. It explains why continuity matters.",
            prompt,
        )
        self.assertIn("Current Paragraph:", prompt)

    def test_chapter_memory_backfill_reconstructs_memory_without_rerunning_packets(self) -> None:
        chapter_xhtml = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>First translated paragraph introduces context engineering. It gives the chapter a stable anchor term. It also explains why the term matters. Finally, it sets up the following discussion.</p>
    <p>Second translated paragraph reinforces the context engineering concept. It reuses the same core term. It adds another supporting explanation. Finally, it keeps the concept active for memory replay.</p>
    <p>Third translated paragraph closes the section. It summarizes the earlier framing. It preserves the same terminology. Finally, it ends the chapter segment cleanly.</p>
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
    <p>Agentic AI depends on distributed SQL. It also depends on stable orchestration. It benefits from explicit memory design. Finally, it needs infrastructure that can persist state.</p>
    <p>Context engineering shapes how context is created. It also shapes how context is maintained. It influences how context is retrieved. Finally, it determines how context is applied during execution.</p>
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

    def test_merge_active_concepts_counts_unique_packets_only(self) -> None:
        with self.session_factory() as session:
            service = TranslationService(TranslationRepository(session))
            merged = service._merge_active_concepts(
                existing_concepts=[
                    {
                        "source_term": "Context engineering",
                        "canonical_zh": None,
                        "status": "candidate",
                        "confidence": 0.6,
                        "first_seen_packet_id": "packet-1",
                        "last_seen_packet_id": "packet-1",
                        "packet_ids_seen": ["packet-1"],
                        "times_seen": 1,
                        "mention_count": 1,
                        "packet_mention_counts": {"packet-1": 1},
                    }
                ],
                source_sentences=["Context engineering shapes how context is created."],
                packet_id="packet-1",
            )
            self.assertEqual(len(merged), 1)
            self.assertEqual(merged[0]["packet_ids_seen"], ["packet-1"])
            self.assertEqual(merged[0]["times_seen"], 1)
            self.assertEqual(merged[0]["mention_count"], 1)
            self.assertEqual(merged[0]["packet_mention_counts"], {"packet-1": 1})

            merged = service._merge_active_concepts(
                existing_concepts=merged,
                source_sentences=["Context engineering also determines how context is maintained."],
                packet_id="packet-2",
            )
            self.assertEqual(merged[0]["packet_ids_seen"], ["packet-1", "packet-2"])
            self.assertEqual(merged[0]["times_seen"], 2)
            self.assertEqual(merged[0]["mention_count"], 2)
            self.assertEqual(
                merged[0]["packet_mention_counts"],
                {"packet-1": 1, "packet-2": 1},
            )

    def test_translation_service_updates_chapter_memory_with_newer_packet_brief(self) -> None:
        document_id, packet_ids = self._bootstrap_to_db()

        with self.session_factory() as session:
            repository = TranslationRepository(session)
            heading_packet_id, first_packet_id = packet_ids[:2]
            service = TranslationService(repository)
            service.execute_packet(heading_packet_id)
            service.execute_packet(first_packet_id)
            chapter_id = repository.load_packet_bundle(first_packet_id).packet.chapter_id
            chapter_memory_repository = ChapterTranslationMemoryRepository(session)
            current_snapshot = chapter_memory_repository.load_latest(
                document_id=document_id,
                chapter_id=chapter_id,
            )
            assert current_snapshot is not None

            stale_snapshot = chapter_memory_repository.supersede_and_create_next(
                current_snapshot=current_snapshot,
                document_id=document_id,
                chapter_id=chapter_id,
                content_json={
                    **dict(current_snapshot.content_json),
                    "chapter_brief": "Old stale brief.",
                    "chapter_brief_version": 0,
                },
            )
            session.flush()

            service.execute_packet(first_packet_id)
            refreshed_snapshot = chapter_memory_repository.load_latest(
                document_id=document_id,
                chapter_id=chapter_id,
            )
            assert refreshed_snapshot is not None
            self.assertGreater(refreshed_snapshot.version, stale_snapshot.version)
            self.assertNotEqual(refreshed_snapshot.content_json.get("chapter_brief"), "Old stale brief.")
            self.assertEqual(refreshed_snapshot.content_json.get("chapter_brief_version"), 1)

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

    def test_openai_compatible_client_retries_after_incomplete_read_in_live_transport(self) -> None:
        class StubHTTPResponse:
            def __init__(self, *, payload: bytes | None = None, read_exc: Exception | None = None) -> None:
                self.payload = payload
                self.read_exc = read_exc

            def __enter__(self) -> "StubHTTPResponse":
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

            def read(self) -> bytes:
                if self.read_exc is not None:
                    raise self.read_exc
                return self.payload or b""

        client = OpenAICompatibleTranslationClient(
            api_key="test-key",
            base_url="https://api.deepseek.com",
            timeout_seconds=45,
            max_retries=1,
            retry_backoff_seconds=0,
            transport=UrllibJSONTransport(),
        )

        with patch(
            "book_agent.workers.providers.openai_compatible.urlopen",
            side_effect=[
                StubHTTPResponse(read_exc=IncompleteRead(b"")),
                StubHTTPResponse(
                    payload=(
                        '{"id":"chatcmpl_retry_123",'
                        '"usage":{"prompt_tokens":88,"completion_tokens":22,"total_tokens":110},'
                        '"choices":[{"message":{"content":"{\\"packet_id\\":\\"pkt_1\\",\\"target_segments\\":[{\\"temp_id\\":\\"t1\\",\\"text_zh\\":\\"译文\\",\\"segment_type\\":\\"sentence\\",\\"source_sentence_ids\\":[\\"s1\\"],\\"confidence\\":0.91}],\\"alignment_suggestions\\":[{\\"source_sentence_ids\\":[\\"s1\\"],\\"target_temp_ids\\":[\\"t1\\"],\\"relation_type\\":\\"1:1\\",\\"confidence\\":0.93}],\\"low_confidence_flags\\":[],\\"notes\\":[]}"}}]}'
                    ).encode("utf-8")
                ),
            ],
        ) as mocked_urlopen:
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
        self.assertEqual(mocked_urlopen.call_count, 2)

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

    def test_openai_compatible_client_normalizes_common_alignment_aliases(self) -> None:
        transport = FakeJSONTransport(
            {
                "id": "chatcmpl_alias_123",
                "usage": {"prompt_tokens": 77, "completion_tokens": 19, "total_tokens": 96},
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"packet_id":"pkt_1","target_segments":[{"temp_id":"t1","text_zh":"译文","segment_type":"sentence","source_id":"s1","confidence":0.91}],'
                                '"alignment_suggestions":[{"source_id":["s1"],"target_temp_id":"t1","relation_type":"1:1","confidence":0.93}],'
                                '"notes":[]}'
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
        self.assertEqual(output.target_segments[0].source_sentence_ids, ["s1"])
        self.assertEqual(output.alignment_suggestions[0].source_sentence_ids, ["s1"])
        self.assertEqual(output.alignment_suggestions[0].target_temp_ids, ["t1"])
        self.assertEqual(output.low_confidence_flags, [])

    def test_openai_compatible_client_generates_generic_structured_object_in_chat_mode(self) -> None:
        transport = FakeJSONTransport(
            {
                "id": "chatcmpl_concept_123",
                "usage": {"prompt_tokens": 42, "completion_tokens": 18, "total_tokens": 60},
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"source_term":"Context Engineering","canonical_zh":"上下文工程",'
                                '"confidence":0.96,"rationale":"The translated examples already use 上下文工程。"}'
                            )
                        }
                    }
                ],
            }
        )
        client = OpenAICompatibleTranslationClient(
            api_key="test-key",
            base_url="https://api.deepseek.com",
            timeout_seconds=30,
            transport=transport,
        )

        payload, usage = client.generate_structured_object(
            model_name="deepseek-chat",
            system_prompt="system",
            user_prompt="user",
            response_schema={
                "type": "object",
                "properties": {
                    "source_term": {"type": "string"},
                    "canonical_zh": {"type": "string"},
                    "confidence": {"type": "number"},
                    "rationale": {"type": "string"},
                },
                "required": ["source_term", "canonical_zh"],
            },
            schema_name="concept_resolution",
        )

        self.assertEqual(payload["source_term"], "Context Engineering")
        self.assertEqual(payload["canonical_zh"], "上下文工程")
        self.assertEqual(usage.token_in, 42)
        self.assertEqual(usage.token_out, 18)
        self.assertEqual(usage.provider_request_id, "chatcmpl_concept_123")
        self.assertEqual(len(transport.calls), 1)
        call = transport.calls[0]
        self.assertEqual(call["url"], "https://api.deepseek.com/chat/completions")
        self.assertEqual(call["payload"]["model"], "deepseek-chat")
        self.assertEqual(call["payload"]["response_format"]["type"], "json_object")

    def test_factory_builds_openai_compatible_worker_when_credentials_exist(self) -> None:
        settings = Settings(
            translation_backend="openai_compatible",
            translation_model="gpt-test",
            translation_prompt_version="p0.llm.v1",
            translation_prompt_profile="role-style-v2",
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
        self.assertEqual(metadata.runtime_config["prompt_profile"], "role-style-v2")
        self.assertEqual(metadata.runtime_config["base_url"], "https://provider.example/v1/responses")
        self.assertEqual(metadata.runtime_config["timeout_seconds"], 30)
        self.assertEqual(metadata.runtime_config["max_retries"], 1)
        self.assertEqual(metadata.runtime_config["retry_backoff_seconds"], 1.5)

    def test_settings_read_openai_key_from_dotenv_and_ignore_shell_env(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as handle:
            handle.write(
                "OPENAI_API_KEY=dotenv-test-key\n"
                "OPENAI_BASE_URL=https://provider.example/v1/responses\n"
            )
            env_path = Path(handle.name)
        self.addCleanup(lambda: env_path.unlink(missing_ok=True))

        with patch.dict(
            "os.environ",
            {
                "OPENAI_API_KEY": "env-test-key",
                "OPENAI_BASE_URL": "https://provider.example/v1/responses",
            },
            clear=False,
        ):
            settings = Settings(
                _env_file=env_path,
                translation_backend="openai_compatible",
                translation_model="gpt-test",
                translation_prompt_version="p0.llm.v1",
            )

        self.assertEqual(settings.translation_openai_api_key, "dotenv-test-key")
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
                _env_file=None,
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
