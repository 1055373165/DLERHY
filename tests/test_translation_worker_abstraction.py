import tempfile
import unittest
import zipfile
from unittest.mock import patch
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.infra.repositories.translation import TranslationRepository
from book_agent.orchestrator.bootstrap import BootstrapOrchestrator
from book_agent.core.config import Settings
from book_agent.services.translation import TranslationService
from book_agent.workers.factory import build_translation_worker
from book_agent.workers.contracts import (
    AlignmentSuggestion,
    TranslationTargetSegment,
    TranslationWorkerOutput,
    TranslationWorkerResult,
)
from book_agent.workers.providers.openai_compatible import OpenAICompatibleTranslationClient
from book_agent.workers.translator import LLMTranslationWorker, TranslationPromptRequest


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
        self.assertIn("The solution to this problem is context engineering. => ZH::The solution to this problem is context engineering.", fake_client.requests[0].user_prompt)
        self.assertIn("[S1]", fake_client.requests[0].user_prompt)
        self.assertNotIn(first_sentence_id, fake_client.requests[0].user_prompt)
        self.assertNotIn("Packet ID:", fake_client.requests[0].user_prompt)
        self.assertEqual(fake_client.requests[0].sentence_alias_map["S1"], first_sentence_id)
        self.assertIn("Return JSON that matches the provided response schema.", fake_client.requests[0].user_prompt)

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
