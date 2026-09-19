"""Terminology Agent: book tools, auto-approval policy, BOOK.md and its place in the prompt."""

from __future__ import annotations

import unittest
from typing import Any
from uuid import uuid4

from book_agent.domain.enums import (
    AgentItemKind,
    AgentTurnStatus,
    ApprovalStatus,
    ArtifactStatus,
    BlockType,
    ChapterStatus,
    DecisionScope,
    DocumentStatus,
    LockLevel,
    ProtectedPolicy,
    SourceType,
)
from book_agent.domain.models import Block, Chapter, Document, Sentence
from book_agent.harness.agents.terminology import TerminologyAgent
from book_agent.harness.context.book_md import KEY_REGISTER, book_prompt_guidance, load_book_guide
from book_agent.harness.kernel.model import AgentStep, ToolCall
from book_agent.harness.kernel.turn import AgentTurnRunner
from book_agent.harness.tools.book_tools import book_tool_registry, terminology_policy
from book_agent.harness.tools.registry import ToolContext
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.agent import AgentLedgerRepository
from book_agent.services.glossary_service import GlossaryService
from book_agent.translation.contracts import ContextPacket, PacketBlock, TranslationUsage
from book_agent.workers.translator import TranslationTask, build_translation_prompt_request


class _ScriptedModel:
    def __init__(self, steps: list[AgentStep]) -> None:
        self.steps = list(steps)
        self.calls: list[dict[str, Any]] = []

    def step(self, *, model_name, messages, tools) -> AgentStep:
        self.calls.append({"messages": messages, "tools": tools})
        return self.steps.pop(0)


class _FakeExtractionClient:
    def generate_structured_object(self, *, model_name, system_prompt, user_prompt, response_schema, schema_name="x"):
        return (
            {"terms": [{"source_term": "momentum", "target_term": "动量", "term_type": "concept", "required": False, "note": ""}]},
            TranslationUsage(token_in=5, token_out=2, total_tokens=7, cost_usd=0.0),
        )


class TerminologyAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        self.addCleanup(self.engine.dispose)
        with self.session_factory() as session:
            document = Document(source_type=SourceType.EPUB, file_fingerprint="term-fp", title="Momentum", title_src="Momentum Trading", author="J. Welles Wilder", status=DocumentStatus.ACTIVE)
            session.add(document)
            session.flush()
            self.document_id = document.id
            chapter = Chapter(document_id=document.id, ordinal=1, title_src="Chapter One", status=ChapterStatus.PACKET_BUILT)
            session.add(chapter)
            session.flush()
            self.chapter_id = chapter.id
            texts = [
                ("heading", "Momentum basics"),
                ("paragraph", "Momentum measures the rate of change. Wilder introduced the RSI indicator."),
                *[("paragraph", f"Momentum matters in example {i}. The RSI reading confirms it.") for i in range(12)],
            ]
            for ordinal, (kind, text) in enumerate(texts, start=1):
                block = Block(chapter_id=chapter.id, ordinal=ordinal, block_type=BlockType(kind), source_text=text, protected_policy=ProtectedPolicy.TRANSLATE, status=ArtifactStatus.ACTIVE)
                session.add(block)
                session.flush()
                for s_index, sentence_text in enumerate([part.strip() + "." for part in text.split(".") if part.strip()], start=1):
                    session.add(Sentence(block_id=block.id, chapter_id=chapter.id, document_id=document.id, ordinal_in_block=s_index, source_text=sentence_text))
            session.commit()

    def _ctx(self, session) -> ToolContext:
        return ToolContext(session=session, document_id=self.document_id, agent_kind="terminology", turn_id=str(uuid4()), actor_id="agent.terminology")

    def test_book_tools_search_read_and_write_terms(self) -> None:
        registry = book_tool_registry()
        with self.session_factory() as session:
            ctx = self._ctx(session)
            search = registry.get("search_book")
            result = search.handler(ctx, search.parse_arguments({"query": "rsi", "limit": 3}))
            self.assertEqual(result["total_sentences"], 13)
            self.assertEqual(len(result["examples"]), 3)
            outline = registry.get("read_chapter_outline")
            chapters = outline.handler(ctx, outline.parse_arguments({}))["chapters"]
            self.assertEqual(chapters[0]["headings"][0]["text"], "Momentum basics")
            read = registry.get("read_block")
            block = read.handler(ctx, read.parse_arguments({"block_id": result["examples"][0]["block_id"]}))
            self.assertIn("RSI", block["text"])
            propose = registry.get("propose_term")
            written = propose.handler(ctx, propose.parse_arguments({"source_term": "momentum", "target_term": "动量", "note": "技术分析术语"}))
            self.assertEqual(written["lock_level"], "preferred")
            self.assertGreaterEqual(written["occurrences"], 13)
            with self.assertRaises(Exception) as ctx_error:
                propose.handler(ctx, propose.parse_arguments({"source_term": "unicorn", "target_term": "独角兽"}))
            self.assertIn("does not occur", str(ctx_error.exception))
            glossary = registry.get("get_glossary").handler(ctx, registry.get("get_glossary").parse_arguments({}))
            self.assertEqual(glossary["terms"][0]["source_term"], "momentum")
            decisions = AgentLedgerRepository(session).latest_decisions(self.document_id, scope=DecisionScope.BOOK)
            self.assertEqual(decisions["term:momentum"].value_json["note"], "技术分析术语")

    def test_policy_auto_locks_names_and_frequent_terms_but_not_rare_concepts(self) -> None:
        policy = terminology_policy()
        lock = book_tool_registry().get("lock_term")
        with self.session_factory() as session:
            ctx = self._ctx(session)
            person = policy.decide(lock, ctx, lock.parse_arguments({"source_term": "Wilder", "target_term": "怀尔德", "term_type": "person"}))
            frequent = policy.decide(lock, ctx, lock.parse_arguments({"source_term": "momentum", "target_term": "动量"}))
            rare = policy.decide(lock, ctx, lock.parse_arguments({"source_term": "rate of change", "target_term": "变化率"}))
        self.assertEqual(person.policy_id, "auto.lock_names_and_abbreviations")
        self.assertEqual(frequent.policy_id, "auto.lock_frequent_terms")
        self.assertEqual(rare.kind.value, "require_approval")

    def test_agent_turn_records_decisions_and_book_md_reaches_the_prompt(self) -> None:
        model = _ScriptedModel(
            [
                AgentStep(
                    text=None,
                    tool_calls=[
                        ToolCall("c1", "record_decision", {"key": KEY_REGISTER, "text": "专业但可读的技术书语域", "rationale": "面向交易者"}),
                        ToolCall("c2", "propose_term", {"source_term": "momentum", "target_term": "动量"}),
                        ToolCall("c3", "lock_term", {"source_term": "RSI", "target_term": "RSI", "term_type": "abbr"}),
                        ToolCall("c4", "lock_term", {"source_term": "rate of change", "target_term": "变化率"}),
                    ],
                    usage=TranslationUsage(token_in=100, token_out=40, total_tokens=140, cost_usd=0.01),
                ),
                AgentStep(text="术语表已确定。", usage=TranslationUsage(token_in=10, token_out=5, total_tokens=15, cost_usd=0.001)),
            ]
        )
        with self.session_factory() as session:
            seed = TerminologyAgent(session).start_turn(
                document_id=self.document_id, model_name="fake", extraction_client=_FakeExtractionClient(), mode="sampled"
            )
            session.commit()
            items = AgentLedgerRepository(session).list_items(seed.turn_id)
            self.assertEqual([item.kind for item in items], [AgentItemKind.SYSTEM, AgentItemKind.USER])
            brief = items[1].content_json["text"]
            self.assertIn("momentum => 动量", brief)
            self.assertIn("Momentum Trading", brief)
            self.assertEqual(seed.candidate_count, 1)

        runner = AgentTurnRunner(
            session_factory=self.session_factory,
            model=model,
            registry=TerminologyAgent.registry(),
            policy=TerminologyAgent.policy(),
            compaction_threshold_tokens=None,
        )
        outcome = runner.run(seed.turn_id)
        # "rate of change" is rare: the turn waits for a human.
        self.assertEqual(outcome.status, AgentTurnStatus.AWAITING_APPROVAL)
        with self.session_factory() as session:
            ledger = AgentLedgerRepository(session)
            approvals = ledger.approvals_for_turn(seed.turn_id)
            self.assertEqual(sorted(a.status.value for a in approvals), [ApprovalStatus.AUTO_APPROVED.value, ApprovalStatus.PENDING.value])
            entries = {e.source_term: e for e in GlossaryService(session).list_document_entries(self.document_id)}
            self.assertEqual(entries["momentum"].lock_level, LockLevel.PREFERRED)
            self.assertEqual(entries["RSI"].lock_level, LockLevel.LOCKED)
            self.assertNotIn("rate of change", entries)
            guide = load_book_guide(session, self.document_id)
            markdown = guide.render_markdown(title="Momentum Trading")
            self.assertIn("## 语域与文风", markdown)
            self.assertIn("- RSI => RSI (locked)", markdown)
            self.assertIn("- momentum => 动量 (preferred)", markdown)
            guidance = book_prompt_guidance(session, self.document_id)
            self.assertTrue(guidance.startswith("Book Guidance:"))
            self.assertIn("语域与文风: 专业但可读的技术书语域（面向交易者）", guidance)

        packet = ContextPacket(
            packet_id="p1", document_id=self.document_id, chapter_id=self.chapter_id, packet_type="translate",
            book_profile_version=1, current_blocks=[PacketBlock(block_id="b", block_type="paragraph", sentence_ids=["s1"], text="Momentum matters.")],
        )
        with self.session_factory() as session:
            sentence = session.query(Sentence).first()
            request = build_translation_prompt_request(
                TranslationTask(context_packet=packet, current_sentences=[sentence], book_guidance=guidance),
                model_name="m", prompt_version="v", prompt_profile="tech-column-meta-v1",
            )
        roles = [m.role for m in request.messages]
        self.assertEqual(roles, ["system", "system", "user"])
        self.assertTrue(request.messages[1].content.startswith("Book Guidance:"))
        # Without guidance the layout is unchanged (golden-protected).
        with self.session_factory() as session:
            sentence = session.query(Sentence).first()
            plain = build_translation_prompt_request(
                TranslationTask(context_packet=packet, current_sentences=[sentence]),
                model_name="m", prompt_version="v", prompt_profile="tech-column-meta-v1",
            )
        self.assertEqual([m.role for m in plain.messages], ["system", "user"])


if __name__ == "__main__":
    unittest.main()
