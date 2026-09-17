"""The output guardrail sends rejected worker answers back for repair inside one packet turn."""

import tempfile
import unittest
import zipfile
from pathlib import Path

from sqlalchemy import select

from book_agent.core.ids import stable_id
from book_agent.domain.enums import PacketSentenceRole
from book_agent.domain.event_kinds import LLM_CALL_COMPLETED, TRANSLATION_OUTPUT_REJECTED
from book_agent.domain.models import Event
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.infra.repositories.translation import TranslationRepository
from book_agent.orchestrator.bootstrap import BootstrapOrchestrator
from book_agent.services.translation import TranslationService
from book_agent.translation.contracts import (
    AlignmentSuggestion,
    TranslationTargetSegment,
    TranslationUsage,
    TranslationWorkerOutput,
    TranslationWorkerResult,
)
from book_agent.translation.output_validation import COVERAGE_INCOMPLETE, SOURCE_ECHOED
from book_agent.workers.translator import (
    LLMTranslationWorker,
    TranslationPromptRequest,
    TranslationTask,
    TranslationWorkerMetadata,
    build_translation_prompt_request,
)
from tests.test_translation_worker_abstraction import CHAPTER_XHTML, CONTAINER_XML, CONTENT_OPF, NAV_XHTML


def _segments(task: TranslationTask, *, drop_last: bool = False, echo: bool = False) -> TranslationWorkerOutput:
    sentences = list(task.current_sentences)
    if drop_last:
        sentences = sentences[:-1]
    segments = []
    alignments = []
    for sentence in sentences:
        temp_id = stable_id("temp", sentence.id)
        segments.append(
            TranslationTargetSegment(
                temp_id=temp_id,
                text_zh=sentence.source_text if echo else f"译::{sentence.source_text}",
                segment_type="sentence",
                source_sentence_ids=[sentence.id],
                confidence=0.9,
            )
        )
        alignments.append(
            AlignmentSuggestion(source_sentence_ids=[sentence.id], target_temp_ids=[temp_id], relation_type="1:1")
        )
    return TranslationWorkerOutput(
        packet_id=task.context_packet.packet_id, target_segments=segments, alignment_suggestions=alignments
    )


class ScriptedWorker:
    """Returns the scripted outputs in order and records every task it was given."""

    def __init__(self, script):
        self.script = list(script)
        self.tasks: list[TranslationTask] = []

    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(worker_name="scripted", model_name="scripted-model", prompt_version="v1")

    def translate(self, task: TranslationTask) -> TranslationWorkerResult:
        self.tasks.append(task)
        build = self.script.pop(0)
        return TranslationWorkerResult(
            output=build(task),
            usage=TranslationUsage(token_in=100, token_out=50, total_tokens=150, latency_ms=10, cost_usd=0.01),
        )


class OutputGuardrailTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
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
        # A multi-sentence paragraph packet, so coverage gaps are possible.
        self.packet_id = next(
            packet.id
            for packet in artifacts.translation_packets
            if len([m for m in artifacts.packet_sentence_maps if m.packet_id == packet.id and m.role == PacketSentenceRole.CURRENT]) > 1
        )

    def _service(self, session, worker, *, repairs: int) -> TranslationService:
        return TranslationService(TranslationRepository(session), worker=worker, max_output_repairs=repairs)

    def test_rejected_output_is_repaired_in_the_same_turn(self) -> None:
        worker = ScriptedWorker([lambda task: _segments(task, drop_last=True), lambda task: _segments(task)])
        with self.session_factory() as session:
            artifacts = self._service(session, worker, repairs=1).execute_packet(self.packet_id)
            session.commit()
            events = session.scalars(select(Event).where(Event.packet_id == self.packet_id)).all()

        self.assertEqual(len(worker.tasks), 2)
        self.assertIsNone(worker.tasks[0].correction)
        correction = worker.tasks[1].correction
        self.assertIsNotNone(correction)
        self.assertEqual(correction.attempt, 1)
        self.assertEqual(correction.report.error_code, COVERAGE_INCOMPLETE)
        self.assertIsNone(artifacts.translation_run.error_code)
        self.assertEqual(artifacts.translation_run.model_config_json["output_repairs"], 1)
        # Both calls are metered on the accepted run.
        self.assertEqual(artifacts.translation_run.token_in, 200)
        self.assertEqual(artifacts.translation_run.token_out, 100)
        rejected = [event for event in events if event.kind == TRANSLATION_OUTPUT_REJECTED]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0].payload["error_code"], COVERAGE_INCOMPLETE)
        self.assertTrue(rejected[0].payload["repaired"])
        completed = [event for event in events if event.kind == LLM_CALL_COMPLETED]
        self.assertEqual(completed[0].payload["token_in"], 200)

    def test_exhausted_repair_budget_persists_the_last_answer_with_its_error_code(self) -> None:
        worker = ScriptedWorker([lambda task: _segments(task, echo=True), lambda task: _segments(task, echo=True)])
        with self.session_factory() as session:
            artifacts = self._service(session, worker, repairs=1).execute_packet(self.packet_id)
            session.commit()
            events = session.scalars(select(Event).where(Event.packet_id == self.packet_id)).all()

        self.assertEqual(len(worker.tasks), 2)
        self.assertEqual(artifacts.translation_run.error_code, SOURCE_ECHOED)
        rejected = [event for event in events if event.kind == TRANSLATION_OUTPUT_REJECTED]
        self.assertEqual(len(rejected), 1)
        self.assertFalse(rejected[0].payload["repaired"])

    def test_zero_budget_disables_repair(self) -> None:
        worker = ScriptedWorker([lambda task: _segments(task, drop_last=True)])
        with self.session_factory() as session:
            artifacts = self._service(session, worker, repairs=0).execute_packet(self.packet_id)
            session.commit()

        self.assertEqual(len(worker.tasks), 1)
        self.assertEqual(artifacts.translation_run.error_code, COVERAGE_INCOMPLETE)
        self.assertNotIn("output_repairs", artifacts.translation_run.model_config_json)

    def test_repair_prompt_replays_the_answer_in_alias_form_after_the_packet(self) -> None:
        class RecordingClient:
            def __init__(self) -> None:
                self.requests: list[TranslationPromptRequest] = []

            def generate_translation(self, request: TranslationPromptRequest) -> TranslationWorkerOutput:
                self.requests.append(request)
                aliases = list(request.sentence_alias_map.keys())
                if len(self.requests) == 1:
                    aliases = aliases[:-1]
                return TranslationWorkerOutput(
                    packet_id=request.packet_id,
                    target_segments=[
                        TranslationTargetSegment(
                            temp_id=f"t-{alias}", text_zh=f"译文 {alias}", segment_type="sentence", source_sentence_ids=[alias]
                        )
                        for alias in aliases
                    ],
                    alignment_suggestions=[
                        AlignmentSuggestion(source_sentence_ids=[alias], target_temp_ids=[f"t-{alias}"], relation_type="1:1")
                        for alias in aliases
                    ],
                )

        client = RecordingClient()
        worker = LLMTranslationWorker(client, model_name="m", prompt_version="v1")
        with self.session_factory() as session:
            artifacts = self._service(session, worker, repairs=1).execute_packet(self.packet_id)
            session.commit()

        self.assertIsNone(artifacts.translation_run.error_code)
        self.assertEqual(len(client.requests), 2)
        first, second = client.requests
        # The cached prefix (system + packet) is identical; the repair adds an assistant replay and the findings.
        self.assertEqual(first.messages, second.messages[: len(first.messages)])
        self.assertEqual([message.role for message in second.messages[len(first.messages) :]], ["assistant", "user"])
        last_alias = list(second.sentence_alias_map.keys())[-1]
        self.assertIn(last_alias, second.messages[-1].content)
        self.assertIn("failed validation", second.messages[-1].content)
        self.assertIn('"source_sentence_ids":["S1"]', second.messages[-2].content)
        self.assertNotIn(second.sentence_alias_map["S1"], second.messages[-2].content)

    def test_prompt_without_correction_is_unchanged(self) -> None:
        with self.session_factory() as session:
            prepared = self._service(session, ScriptedWorker([]), repairs=1).prepare_packet(self.packet_id)
        request = build_translation_prompt_request(prepared.task, model_name="m", prompt_version="v1")
        self.assertEqual(request.messages[-1].role, "user")
        self.assertNotIn("failed validation", request.messages[-1].content)


if __name__ == "__main__":
    unittest.main()
