"""The terminology agent does not propose code identifiers, code tokens or generic verbs as terms."""

from __future__ import annotations

import unittest
from uuid import uuid4

from book_agent.domain.enums import ArtifactStatus, BlockType, ChapterStatus, DocumentStatus, ProtectedPolicy, SourceType
from book_agent.domain.models import Block, Chapter, Document, Sentence
from book_agent.domain.terminology.candidates import mostly_in_code, not_a_term
from book_agent.harness.agents.terminology import SYSTEM_PROMPT, TerminologyAgent
from book_agent.harness.tools.book_tools import book_tool_registry
from book_agent.harness.tools.registry import ToolContext, ToolError
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.services.glossary_extraction import GLOSSARY_EXTRACTION_SYSTEM_PROMPT, _Proposal, merge_proposals
from book_agent.domain.enums import TermType
from book_agent.translation.contracts import TranslationUsage


class NotATermTests(unittest.TestCase):
    def test_identifiers_code_tokens_and_generic_verbs(self) -> None:
        for term in ("@tool", "step()", "final_result", "self.memory", "session_id", "Agent.run()", "<think>", "--verbose"):
            self.assertEqual(not_a_term(term), "code identifier", term)
        for verb in ("execute", "run", "call", "return"):
            self.assertEqual(not_a_term(verb), "generic verb", verb)

    def test_names_and_concepts_stay_terms(self) -> None:
        for term in ("LLM-as-a-Judge", "ReAct", "GraphRAG", "Node.js", "harness", "tool calling", "Model Context Protocol", "execution trace", "Event", "Precision@k", "nDCG", "primary/backup replication", "query", "process"):
            self.assertIsNone(not_a_term(term), term)

    def test_a_word_the_book_writes_in_code_is_code(self) -> None:
        code = ["class Event(BaseModel):\n    kind: str", "events: list[Event] = []", "def execute(self): ..."]
        self.assertTrue(mostly_in_code("Event", ["Each `Event` has a kind."], code))
        self.assertTrue(mostly_in_code("ExecutionContext", ["The ExecutionContext holds the state."], ["ctx = ExecutionContext()"]))
        # Prose words stay terms even when code mentions them once.
        self.assertFalse(mostly_in_code("harness", ["The harness runs the loop.", "A harness is the code around the model."], ["harness = Harness()"]))
        self.assertFalse(mostly_in_code("ReAct", ["ReAct interleaves reasoning and acting.", "The ReAct loop."], []))


class _Extraction:
    def generate_structured_object(self, **_kwargs):
        terms = [
            {"source_term": "@tool", "target_term": "@tool", "term_type": "other"},
            {"source_term": "step()", "target_term": "step()", "term_type": "other"},
            {"source_term": "execute", "target_term": "执行", "term_type": "concept"},
            {"source_term": "tool calling", "target_term": "工具调用", "term_type": "concept"},
        ]
        return {"terms": terms}, TranslationUsage(token_in=1, token_out=1, total_tokens=2, cost_usd=0.0)


class TerminologyAgentFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        self.addCleanup(self.engine.dispose)
        with self.session_factory() as session:
            document = Document(source_type=SourceType.EPUB, file_fingerprint="cand-fp", title="Agents", title_src="Agents", status=DocumentStatus.ACTIVE)
            session.add(document)
            session.flush()
            self.document_id = document.id
            chapter = Chapter(document_id=document.id, ordinal=1, title_src="1 Tools", status=ChapterStatus.PACKET_BUILT)
            session.add(chapter)
            session.flush()
            texts = [
                ("paragraph", "Tool calling lets the agent act. Decorate a function with @tool to register it."),
                ("paragraph", "Each step() call runs one Event through the loop. The agent will execute the tool calling plan."),
                ("paragraph", "Tool calling is how the model asks for work. Tool calling returns an Event."),
                ("code", "class Event:\n    pass\nevents = [Event(), Event()]\n@tool\ndef search(): ..."),
            ]
            for ordinal, (kind, text) in enumerate(texts, start=1):
                block = Block(
                    chapter_id=chapter.id,
                    ordinal=ordinal,
                    block_type=BlockType(kind),
                    source_text=text,
                    protected_policy=ProtectedPolicy.TRANSLATE if kind != "code" else ProtectedPolicy.PROTECT,
                    status=ArtifactStatus.ACTIVE,
                )
                session.add(block)
                session.flush()
                if kind == "code":
                    continue
                for index, part in enumerate([p.strip() + "." for p in text.split(".") if p.strip()], start=1):
                    session.add(Sentence(block_id=block.id, chapter_id=chapter.id, document_id=document.id, ordinal_in_block=index, source_text=part))
            session.commit()

    def _ctx(self, session) -> ToolContext:
        return ToolContext(session=session, document_id=self.document_id, agent_kind="terminology", turn_id=str(uuid4()), actor_id="agent.terminology")

    def test_the_term_tools_refuse_identifiers_and_generic_verbs(self) -> None:
        registry = book_tool_registry()
        with self.session_factory() as session:
            lock = registry.get("lock_term")
            for term, target in (("@tool", "@tool"), ("step()", "step()"), ("execute", "执行"), ("Event", "Event")):
                with self.assertRaises(ToolError, msg=term) as caught:
                    lock.handler(self._ctx(session), lock.parse_arguments({"source_term": term, "target_term": target}))
                self.assertIn("not a glossary term", str(caught.exception))
            written = lock.handler(self._ctx(session), lock.parse_arguments({"source_term": "tool calling", "target_term": "工具调用"}))
            self.assertEqual(written["lock_level"], "locked")

    def test_extraction_and_the_agent_brief_leave_them_out(self) -> None:
        proposals = [
            _Proposal(source_term=term, target_term=target, term_type=TermType.CONCEPT, required=False, note="")
            for term, target in (("@tool", "@tool"), ("execute", "执行"), ("tool calling", "工具调用"))
        ]
        suggestions, _dropped = merge_proposals(proposals, ["@tool and tool calling; execute tool calling"] * 3)
        self.assertEqual([item.source_term for item in suggestions], ["tool calling"])
        with self.session_factory() as session:
            seed = TerminologyAgent(session).start_turn(document_id=self.document_id, model_name="m", extraction_client=_Extraction())
            self.assertLessEqual(seed.candidate_count, 1)
        for prompt in (SYSTEM_PROMPT, GLOSSARY_EXTRACTION_SYSTEM_PROMPT):
            self.assertIn("@tool", prompt)
            self.assertIn("generic verbs", prompt)


if __name__ == "__main__":
    unittest.main()
