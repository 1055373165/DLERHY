"""The slim reply format: the model leaves out the packet id and the alignment, which we know.

Asking the model to copy back the packet's UUID and an alignment that restates each segment's
source_sentence_ids was about half of its output tokens (docs/specs/cost-governance/SPEC.md, E4).
In the slim format the client fills both in; everything downstream sees the full output.
"""

from __future__ import annotations

import json
import unittest

from book_agent.translation.contracts import (
    TranslationModelReply,
    TranslationWorkerOutput,
    complete_model_reply,
)
from book_agent.translation.output_validation import OutputCorrection
from book_agent.workers.providers.openai_compatible import OpenAICompatibleTranslationClient
from book_agent.workers.translator import TranslationPromptRequest, _correction_messages
from tests.test_translation_worker_abstraction import FakeJSONTransport


def _segment(temp_id: str, sources: list[str], text: str = "译文") -> dict:
    return {"temp_id": temp_id, "text_zh": text, "segment_type": "sentence", "source_sentence_ids": sources, "confidence": 0.9}


class CompleteReplyTests(unittest.TestCase):
    def test_the_alignment_follows_each_segments_sentences(self) -> None:
        completed = complete_model_reply(
            {"target_segments": [_segment("T1", ["a", "b"]), _segment("T2", ["c"]), _segment("T3", ["c"]), _segment("T4", ["d"])]},
            packet_id="P",
        )
        self.assertEqual(completed["packet_id"], "P")
        self.assertEqual(
            [(item["source_sentence_ids"], item["target_temp_ids"], item["relation_type"]) for item in completed["alignment_suggestions"]],
            [(["a", "b"], ["T1"], "n:1"), (["c"], ["T2"], "1:n"), (["c"], ["T3"], "1:n"), (["d"], ["T4"], "1:1")],
        )
        TranslationWorkerOutput.model_validate(completed)

    def test_a_reply_with_its_own_alignment_keeps_it(self) -> None:
        own = [{"source_sentence_ids": ["a"], "target_temp_ids": ["T1"], "relation_type": "1:1"}]
        completed = complete_model_reply({"packet_id": "X", "target_segments": [_segment("T1", ["a"])], "alignment_suggestions": own}, packet_id="P")
        self.assertEqual((completed["packet_id"], completed["alignment_suggestions"]), ("X", own))

    def test_a_segment_without_sentences_aligns_nothing(self) -> None:
        completed = complete_model_reply({"target_segments": [_segment("T1", [])]}, packet_id="P")
        self.assertEqual(completed["alignment_suggestions"], [])


def _chat_response(content: dict) -> dict:
    return {
        "id": "r1",
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        "choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}],
    }


def _request(reply_format: str) -> TranslationPromptRequest:
    schema = (TranslationModelReply if reply_format == "slim" else TranslationWorkerOutput).model_json_schema()
    return TranslationPromptRequest(
        packet_id="pkt_1",
        model_name="deepseek-chat",
        prompt_version="p",
        system_prompt="system",
        user_prompt="user",
        response_schema=schema,
        reply_format=reply_format,
    )


class SlimClientTests(unittest.TestCase):
    def _client(self, transport) -> OpenAICompatibleTranslationClient:
        return OpenAICompatibleTranslationClient(api_key="k", base_url="https://api.deepseek.com", timeout_seconds=30, transport=transport)

    def test_the_request_asks_for_less_and_the_reply_is_completed(self) -> None:
        transport = FakeJSONTransport(_chat_response({"target_segments": [_segment("t1", ["s1"])], "low_confidence_flags": [], "notes": []}))
        output = self._client(transport).generate_translation(_request("slim"))

        self.assertEqual(output.packet_id, "pkt_1")
        self.assertEqual(
            [(item.source_sentence_ids, item.target_temp_ids, item.relation_type) for item in output.alignment_suggestions],
            [(["s1"], ["t1"], "1:1")],
        )
        messages = transport.calls[0]["payload"]["messages"]
        self.assertNotIn("packet_id must equal", messages[-1]["content"])
        self.assertIn("target_segments, low_confidence_flags, notes", messages[0]["content"])
        self.assertNotIn("alignment_suggestions", messages[0]["content"])

    def test_the_full_format_is_unchanged(self) -> None:
        # A full reply that leaves out the alignment is still a format error, never completed.
        from book_agent.workers.providers.openai_compatible import ProviderResponseFormatError

        transport = FakeJSONTransport(
            _chat_response({"packet_id": "pkt_1", "target_segments": [_segment("t1", ["s1"])], "low_confidence_flags": [], "notes": []})
        )
        with self.assertRaises(ProviderResponseFormatError):
            self._client(transport).generate_translation(_request("full"))
        self.assertIn("packet_id must equal: pkt_1", transport.calls[0]["payload"]["messages"][-1]["content"])

    def test_a_correction_replays_the_answer_in_the_slim_format(self) -> None:
        previous = TranslationWorkerOutput.model_validate(complete_model_reply({"target_segments": [_segment("T1", ["sid-1"])]}, packet_id="P"))
        correction = OutputCorrection(previous_output=previous, report=_EmptyReport(), attempt=1)
        replayed = json.loads(_correction_messages(correction, {"S1": "sid-1"}, slim=True)[0].content)
        self.assertNotIn("packet_id", replayed)
        self.assertNotIn("alignment_suggestions", replayed)
        self.assertEqual(replayed["target_segments"][0]["source_sentence_ids"], ["S1"])


class _EmptyReport:
    def findings(self, alias_of=None) -> list[str]:
        return ["Sentences with no translation: S2."]


class SettingTests(unittest.TestCase):
    def test_the_setting_reaches_the_worker_and_marks_the_runs(self) -> None:
        from book_agent.core.config import Settings
        from book_agent.workers.factory import build_translation_worker

        def worker(reply_format: str):
            return build_translation_worker(
                Settings(
                    _env_file=None,
                    translation_backend="openai_compatible",
                    translation_openai_api_key="k",
                    translation_openai_base_url="https://api.deepseek.com",
                    translation_model="deepseek-chat",
                    translation_prompt_version="p1",
                    translation_reply_format=reply_format,
                )
            )

        slim, full, unknown = worker("slim"), worker("full"), worker("tiny")
        self.assertEqual((slim.reply_format, slim.metadata().prompt_version), ("slim", "p1+slim-reply"))
        self.assertEqual((full.reply_format, full.metadata().prompt_version), ("full", "p1"))
        self.assertEqual(unknown.reply_format, "full")


if __name__ == "__main__":
    unittest.main()
