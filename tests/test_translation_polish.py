"""The polish pass: the editor's text replaces an accepted draft only when it passes every check."""

from __future__ import annotations

import unittest

from sqlalchemy import select

from book_agent.domain.event_kinds import PACKET_TRANSLATED
from book_agent.domain.models import Chapter, Event
from book_agent.domain.models.translation import TranslationPacket
from book_agent.infra.repositories.translation import TranslationRepository
from book_agent.services.translation import TranslationService
from book_agent.translation.contracts import TranslationUsage
from tests import test_translation_output_guardrail as guardrail
from tests.test_translation_output_guardrail import ScriptedWorker, _segments


class _Editor:
    """Stands in for the provider client: rewrites segments with ``edit`` and records each request."""

    def __init__(self, edit=None, *, fail: Exception | None = None, drop_one: bool = False) -> None:
        self.edit = edit or (lambda text: text)
        self.fail = fail
        self.drop_one = drop_one
        self.requests: list[dict] = []

    def generate_structured_object(self, **kwargs):
        self.requests.append(kwargs)
        if self.fail is not None:
            raise self.fail
        segments = []
        for block in kwargs["user_prompt"].split("\n\n"):
            lines = block.splitlines()
            if not lines or not lines[0].startswith("["):
                continue
            segment_id = lines[0].strip("[]")
            draft = next(line[len("译文："):] for line in lines if line.startswith("译文："))
            segments.append({"id": segment_id, "text_zh": self.edit(draft)})
        if self.drop_one:
            segments = segments[:-1]
        return {"segments": segments}, TranslationUsage(token_in=40, token_out=20, total_tokens=60, latency_ms=5, cost_usd=0.002)


class _EditedWorker(ScriptedWorker):
    def __init__(self, script, client) -> None:
        super().__init__(script)
        self.client = client


class TranslationPolishTests(guardrail.OutputGuardrailTests):
    # Reuse the guardrail fixture (a real EPUB packet); none of its own tests run here.
    def _execute(self, editor, *, polish: bool = True, script=None):
        worker = _EditedWorker(script or [lambda task: _segments(task)], editor)
        with self.session_factory() as session:
            artifacts = TranslationService(
                TranslationRepository(session), worker=worker, max_output_repairs=0, polish=polish
            ).execute_packet(self.packet_id)
            session.commit()
            events = session.scalars(
                select(Event).where(Event.packet_id == self.packet_id, Event.kind == PACKET_TRANSLATED)
            ).all()
        return artifacts, events

    def test_the_editors_text_replaces_the_draft_and_the_draft_is_kept(self) -> None:
        editor = _Editor(lambda text: text.replace("译::", "润::"))
        artifacts, events = self._execute(editor)

        self.assertTrue(all(segment.text_zh.startswith("润::") for segment in artifacts.target_segments))
        audit = artifacts.translation_run.output_json["polish"]
        self.assertTrue(audit["applied"])
        self.assertEqual(audit["changed_segments"], len(artifacts.target_segments))
        self.assertTrue(all(item["text_zh"].startswith("译::") for item in audit["draft"]))
        # The editor's call is metered on the run.
        self.assertEqual((artifacts.translation_run.token_in, artifacts.translation_run.token_out), (140, 70))
        self.assertTrue(events[-1].payload["polish"]["applied"])
        # The editor saw the source next to each draft segment.
        self.assertIn("原文：", editor.requests[0]["user_prompt"])

    def test_an_edit_that_breaks_a_locked_term_keeps_the_draft(self) -> None:
        from book_agent.services.glossary_service import GlossaryService

        with self.session_factory() as session:
            packet = session.get(TranslationPacket, self.packet_id)
            chapter = session.get(Chapter, packet.chapter_id)
            GlossaryService(session).lock_term(chapter.document_id, "context engineering", "上下文工程")
            session.commit()

        def draft(task):
            output = _segments(task)
            for segment in output.target_segments:
                segment.text_zh = "上下文工程是一门学科。"
            return output

        artifacts, _ = self._execute(_Editor(lambda text: text.replace("上下文工程", "语境设计")), script=[draft])

        self.assertEqual(artifacts.target_segments[0].text_zh, "上下文工程是一门学科。")
        self.assertEqual(artifacts.translation_run.output_json["polish"]["reverted_reason"], "validation")
        self.assertIsNone(artifacts.translation_run.error_code)

    def test_a_missing_segment_or_a_failed_call_keeps_the_draft(self) -> None:
        artifacts, _ = self._execute(_Editor(lambda text: "改::" + text, drop_one=True))
        self.assertTrue(all(segment.text_zh.startswith("译::") for segment in artifacts.target_segments))
        self.assertEqual(artifacts.translation_run.output_json["polish"]["reverted_reason"], "segments_mismatch")

        artifacts, _ = self._execute(_Editor(fail=TimeoutError("provider timed out")))
        self.assertTrue(all(segment.text_zh.startswith("译::") for segment in artifacts.target_segments))
        self.assertEqual(artifacts.translation_run.output_json["polish"]["reverted_reason"], "failed: TimeoutError")
        self.assertIsNone(artifacts.translation_run.error_code)

    def test_switched_off_the_editor_is_never_called(self) -> None:
        editor = _Editor(lambda text: "改::" + text)
        artifacts, _ = self._execute(editor, polish=False)
        self.assertEqual(editor.requests, [])
        self.assertNotIn("polish", artifacts.translation_run.output_json)


# The inherited guardrail tests already run in their own module.
for _name in [name for name in dir(guardrail.OutputGuardrailTests) if name.startswith("test_")]:
    setattr(TranslationPolishTests, _name, None)


if __name__ == "__main__":
    unittest.main()
