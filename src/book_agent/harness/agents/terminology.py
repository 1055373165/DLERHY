"""Terminology Agent: settle the book's glossary and BOOK.md before translation.

Computational part (billed as glossary.extract): sample the book, run the
existing glossary extraction on the sample, count occurrences. Model part
(the agent turn): verify candidates with the book tools, record preferred
renderings, lock what may be locked, and write the book-level decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.domain.enums import AgentItemKind, BlockType
from book_agent.domain.models import Block, Chapter, Document
from book_agent.harness.context.book_md import load_book_guide
from book_agent.harness.kernel.budget import TurnBudget
from book_agent.harness.tools.book_tools import book_tool_registry, terminology_policy
from book_agent.harness.tools.permissions import PermissionPolicy
from book_agent.harness.tools.registry import ToolRegistry
from book_agent.infra.repositories.agent import AgentLedgerRepository
from book_agent.services.glossary_extraction import GlossaryExtractionService, GlossarySuggestion, chunk_texts

AGENT_KIND = "terminology"
MODE_SAMPLED = "sampled"
MODE_THOROUGH = "thorough"
MODE_SKIP = "skip"
TERMINOLOGY_MODES = (MODE_SAMPLED, MODE_THOROUGH, MODE_SKIP)

SYSTEM_PROMPT = """You are the terminology agent for a professional English-to-Simplified-Chinese book translation.
Your job runs once, before translation starts, and decides what every translator packet will see:
1. The glossary: which terms must be rendered consistently and how. Use propose_term for consistency-critical
   concepts (preferred rendering; translation prompts follow it). Use lock_term only for names, abbreviations,
   titles and unambiguous terms that appear often; a lock makes review block on any deviation.
2. BOOK.md decisions via record_decision: book.genre, book.register (语域与文风, in Chinese), book.audience,
   book.preservation_policy (what stays untranslated: code, commands, table content, product names...),
   book.style_notes, and any resolved ambiguity worth remembering (dotted key of your choice).
Work from the candidate list and excerpts you are given; verify doubtful terms with search_book / read_block
before deciding. Prefer the rendering a Chinese professional publication in this field would use. Do not
propose everyday vocabulary. When you are done, reply with a short Chinese summary and no further tool calls."""


@dataclass(slots=True)
class TerminologyTurnSeed:
    turn_id: str
    candidate_count: int
    sample_chars: int


class TerminologyAgent:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.ledger = AgentLedgerRepository(session)

    @staticmethod
    def registry() -> ToolRegistry:
        return book_tool_registry()

    @staticmethod
    def policy() -> PermissionPolicy:
        return terminology_policy()

    # --- turn creation --------------------------------------------------------

    def start_turn(
        self,
        *,
        document_id: str,
        model_name: str,
        extraction_client: Any | None,
        mode: str = MODE_SAMPLED,
        run_id: str | None = None,
        work_item_id: str | None = None,
        budget: TurnBudget | None = None,
        max_sample_chars: int = 40_000,
    ) -> TerminologyTurnSeed:
        document = self.session.get(Document, document_id)
        if document is None:
            raise ValueError(f"document not found: {document_id}")
        sample_texts = self._sample_texts(document_id, mode=mode, max_chars=max_sample_chars)
        candidates = self._extract_candidates(document_id, sample_texts, extraction_client, model_name, mode=mode)
        guide = load_book_guide(self.session, document_id)
        turn = self.ledger.create_turn(
            document_id=document_id,
            agent_kind=AGENT_KIND,
            scope_type="document",
            scope_id=document_id,
            run_id=run_id,
            work_item_id=work_item_id,
            model_name=model_name,
            budget=(budget or TurnBudget(max_steps=30, max_tool_calls=120)).to_json(),
            skills=["terminology/default"],
        )
        self.ledger.append_item(turn.id, kind=AgentItemKind.SYSTEM, content={"text": SYSTEM_PROMPT})
        self.ledger.append_item(
            turn.id,
            kind=AgentItemKind.USER,
            content={"text": self._user_brief(document, sample_texts, candidates, guide, mode=mode)},
        )
        self.session.flush()
        return TerminologyTurnSeed(turn_id=turn.id, candidate_count=len(candidates), sample_chars=sum(len(t) for t in sample_texts))

    # --- sampling ----------------------------------------------------------------

    def _sample_texts(self, document_id: str, *, mode: str, max_chars: int) -> list[str]:
        chapters = list(self.session.scalars(select(Chapter).where(Chapter.document_id == document_id).order_by(Chapter.ordinal)).all())
        texts: list[str] = []
        budget = max_chars if mode != MODE_THOROUGH else 10**9
        used = 0
        for chapter in chapters:
            blocks = list(
                self.session.scalars(
                    select(Block).where(Block.chapter_id == chapter.id).order_by(Block.ordinal)
                ).all()
            )
            if mode == MODE_THOROUGH:
                picked = [b for b in blocks if b.block_type in _PROSE_TYPES]
            else:
                picked = _stratified_sample(blocks)
            for block in picked:
                text = (block.source_text or "").strip()
                if not text:
                    continue
                if used + len(text) > budget:
                    return texts
                texts.append(text)
                used += len(text)
        return texts

    def _extract_candidates(
        self, document_id: str, texts: list[str], client: Any | None, model_name: str, *, mode: str
    ) -> list[GlossarySuggestion]:
        if client is None or not texts or mode == MODE_SKIP:
            return []
        service = GlossaryExtractionService(self.session, client, model_name=model_name)
        try:
            # Provider errors leave the session usable, and the failed call's event stays in it.
            return service.extract_from_texts(document_id, texts)
        except Exception as exc:
            from book_agent.workers.failures import FailureDisposition, classify_failure

            classification = classify_failure(exc)
            if classification.disposition == FailureDisposition.PAUSE:
                raise
            # The agent can still search the book itself; record why the candidates are missing.
            self.extraction_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            return []

    def _user_brief(self, document: Document, texts: list[str], candidates: list[GlossarySuggestion], guide, *, mode: str) -> str:
        lines = [
            f"Book: {document.title_src or document.title or '(untitled)'}" + (f" — {document.author}" if document.author else ""),
            f"Mode: {mode}. Sample: {len(texts)} blocks.",
            "",
            "Existing glossary:",
        ]
        if guide.locked_terms or guide.preferred_terms:
            lines += [f"- {s} => {t} (locked)" for s, t in guide.locked_terms]
            lines += [f"- {s} => {t} (preferred)" for s, t in guide.preferred_terms]
        else:
            lines.append("- (empty)")
        lines += ["", "Candidate terms from the sample (source => proposed rendering | type | occurrences | note):"]
        if candidates:
            for item in candidates[:120]:
                lines.append(
                    f"- {item.source_term} => {item.target_term} | {item.term_type.value} | {item.occurrences}"
                    + (f" | {item.note}" if item.note else "")
                    + (" | recommended lock" if item.recommended_lock else "")
                )
        else:
            reason = getattr(self, "extraction_error", None)
            lines.append(
                "- (none extracted"
                + (f": extraction failed ({reason})" if reason else "")
                + "; use search_book to find terms yourself)"
            )
        lines += ["", "Excerpts:"]
        for chunk in chunk_texts(texts, max_chars=6000)[:6]:
            lines.append(chunk)
            lines.append("---")
        return "\n".join(lines)


_PROSE_TYPES = {BlockType.PARAGRAPH, BlockType.LIST_ITEM, BlockType.QUOTE, BlockType.HEADING, BlockType.CAPTION}


def _stratified_sample(blocks: list[Block]) -> list[Block]:
    """Headings, the first two paragraphs of the chapter, then every eighth prose block."""
    picked: list[Block] = []
    paragraphs_taken = 0
    prose_seen = 0
    for block in blocks:
        if block.block_type == BlockType.HEADING:
            picked.append(block)
            continue
        if block.block_type not in _PROSE_TYPES:
            continue
        prose_seen += 1
        if paragraphs_taken < 2 or prose_seen % 8 == 0:
            picked.append(block)
            paragraphs_taken += 1
    return picked
