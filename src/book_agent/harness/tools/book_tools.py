"""Tools that let an agent read the book ledger and settle terminology.

Reads are plain queries. Reversible writes create PREFERRED glossary
entries or decisions (later rows supersede). Locking a term is irreversible
in the sense that review will block on it, so it needs an approval unless an
auto-approval rule (names, abbreviations, frequent unambiguous terms) applies.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from book_agent.domain.enums import DecisionScope, LockLevel, TermType
from book_agent.domain.models import Block, Chapter, Sentence
from book_agent.domain.terminology.matching import SourceTermIndex
from book_agent.harness.tools.permissions import AutoApproveRule, PermissionPolicy
from book_agent.harness.tools.registry import ToolContext, ToolError, ToolPermission, ToolRegistry, ToolSpec
from book_agent.infra.repositories.agent import AgentLedgerRepository
from book_agent.services.glossary_service import GlossaryService

FREQUENT_TERM_AUTO_LOCK_OCCURRENCES = 10
_AUTO_LOCK_TERM_TYPES = {TermType.PERSON, TermType.ORG, TermType.ABBR, TermType.TITLE, TermType.PLACE}


# --- argument models -----------------------------------------------------------


class SearchBookArgs(BaseModel):
    query: str = Field(description="Word or phrase to look for in the source text (case-insensitive).")
    limit: int = Field(default=8, ge=1, le=40, description="Maximum number of example sentences to return.")


class ReadBlockArgs(BaseModel):
    block_id: str = Field(description="Block id as returned by search_book or read_chapter_outline.")


class ReadChapterOutlineArgs(BaseModel):
    chapter_id: str | None = Field(default=None, description="Chapter id; omit for the whole book's chapter list.")
    max_headings: int = Field(default=12, ge=1, le=60)


class GetGlossaryArgs(BaseModel):
    pass


class TermArgs(BaseModel):
    source_term: str = Field(description="The English term exactly as it appears in the book (base form).")
    target_term: str = Field(description="The Chinese rendering every occurrence should use.")
    term_type: str = Field(default="concept", description="concept | abbr | person | org | title | place | other")
    target_variants: list[str] = Field(default_factory=list, description="Other acceptable renderings (short forms).")
    note: str = Field(default="", description="A few words for reviewers, or empty.")


class RecordDecisionArgs(BaseModel):
    key: str = Field(description="book.genre | book.register | book.audience | book.preservation_policy | book.style_notes | any dotted key")
    text: str = Field(description="The decision, in Chinese, as it should appear in BOOK.md.")
    rationale: str = Field(default="", description="Why, in one sentence.")


# --- handlers -----------------------------------------------------------------


def _term_type(value: str) -> TermType:
    try:
        return TermType(str(value or "concept").strip().lower())
    except ValueError as exc:
        raise ToolError(f"unknown term_type {value!r}; use one of {[t.value for t in TermType]}") from exc


def count_term_occurrences(ctx: ToolContext, term: str) -> int:
    """Sentences of the document that contain the term as a whole word (longest-match index)."""
    index = SourceTermIndex([term])
    rows = ctx.session.execute(
        select(Sentence.source_text).where(
            Sentence.document_id == ctx.document_id,
            Sentence.retired_by_revision_id.is_(None),
            Sentence.translatable.is_(True),
            func.lower(Sentence.source_text).like(f"%{term.lower()}%"),
        )
    )
    return sum(1 for (text,) in rows if index.find(text or ""))


def search_book(ctx: ToolContext, args: SearchBookArgs) -> dict[str, Any]:
    pattern = f"%{args.query.strip().lower()}%"
    rows = ctx.session.execute(
        select(Sentence.id, Sentence.block_id, Sentence.source_text, Chapter.ordinal, Chapter.title_src)
        .join(Chapter, Chapter.id == Sentence.chapter_id)
        .where(Sentence.document_id == ctx.document_id, Sentence.retired_by_revision_id.is_(None), func.lower(Sentence.source_text).like(pattern))
        .order_by(Chapter.ordinal, Sentence.created_at)
        .limit(args.limit)
    ).all()
    total = ctx.session.scalar(
        select(func.count(Sentence.id)).where(
            Sentence.document_id == ctx.document_id, Sentence.retired_by_revision_id.is_(None), func.lower(Sentence.source_text).like(pattern)
        )
    ) or 0
    return {
        "query": args.query,
        "total_sentences": int(total),
        "examples": [
            {
                "sentence_id": sentence_id,
                "block_id": block_id,
                "chapter": f"{ordinal}. {title or ''}".strip(),
                "text": (text or "")[:400],
            }
            for sentence_id, block_id, text, ordinal, title in rows
        ],
    }


def read_block(ctx: ToolContext, args: ReadBlockArgs) -> dict[str, Any]:
    block = ctx.session.get(Block, args.block_id)
    if block is None:
        raise ToolError(f"block not found: {args.block_id}")
    chapter = ctx.session.get(Chapter, block.chapter_id)
    if chapter is None or chapter.document_id != ctx.document_id:
        raise ToolError("block belongs to another document")
    return {
        "block_id": block.id,
        "chapter": f"{chapter.ordinal}. {chapter.title_src or ''}".strip(),
        "block_type": block.block_type.value,
        "text": (block.source_text or "")[:4000],
    }


def read_chapter_outline(ctx: ToolContext, args: ReadChapterOutlineArgs) -> dict[str, Any]:
    stmt = select(Chapter).where(Chapter.document_id == ctx.document_id).order_by(Chapter.ordinal)
    if args.chapter_id:
        stmt = stmt.where(Chapter.id == args.chapter_id)
    chapters = list(ctx.session.scalars(stmt).all())
    outline = []
    for chapter in chapters:
        headings = ctx.session.execute(
            select(Block.id, Block.source_text)
            .where(Block.chapter_id == chapter.id, Block.block_type == "heading")
            .order_by(Block.ordinal)
            .limit(args.max_headings)
        ).all()
        outline.append(
            {
                "chapter_id": chapter.id,
                "ordinal": chapter.ordinal,
                "title": chapter.title_src,
                "headings": [{"block_id": block_id, "text": (text or "")[:200]} for block_id, text in headings],
            }
        )
    return {"chapters": outline}


def get_glossary(ctx: ToolContext, args: GetGlossaryArgs) -> dict[str, Any]:
    entries = GlossaryService(ctx.session).list_document_entries(ctx.document_id)
    return {
        "terms": [
            {
                "source_term": entry.source_term,
                "target_term": entry.target_term,
                "term_type": entry.term_type.value,
                "lock_level": entry.lock_level.value,
                "variants": list(entry.target_variants_json or []),
            }
            for entry in sorted(entries, key=lambda e: e.source_term.casefold())
        ]
    }


def _write_term(ctx: ToolContext, args: TermArgs, *, lock_level: LockLevel) -> dict[str, Any]:
    if not args.source_term.strip() or not args.target_term.strip():
        raise ToolError("source_term and target_term are required")
    occurrences = count_term_occurrences(ctx, args.source_term)
    if occurrences == 0:
        raise ToolError(f"'{args.source_term}' does not occur in the book as a whole term; check the spelling with search_book")
    entry = GlossaryService(ctx.session).lock_term(
        ctx.document_id,
        args.source_term,
        args.target_term,
        term_type=_term_type(args.term_type),
        target_variants=args.target_variants,
        lock_level=lock_level,
    )
    if args.note.strip():
        AgentLedgerRepository(ctx.session).record_decision(
            document_id=ctx.document_id,
            scope=DecisionScope.BOOK,
            key=f"term:{entry.source_term.casefold()}",
            value={"target_term": entry.target_term, "note": args.note.strip(), "lock_level": lock_level.value},
            decided_by=ctx.actor_id,
            turn_id=ctx.turn_id,
        )
    return {
        "source_term": entry.source_term,
        "target_term": entry.target_term,
        "lock_level": entry.lock_level.value,
        "version": entry.version,
        "occurrences": occurrences,
    }


def propose_term(ctx: ToolContext, args: TermArgs) -> dict[str, Any]:
    return _write_term(ctx, args, lock_level=LockLevel.PREFERRED)


def lock_term(ctx: ToolContext, args: TermArgs) -> dict[str, Any]:
    return _write_term(ctx, args, lock_level=LockLevel.LOCKED)


def record_decision(ctx: ToolContext, args: RecordDecisionArgs) -> dict[str, Any]:
    key = args.key.strip()
    if not key or not args.text.strip():
        raise ToolError("key and text are required")
    decision = AgentLedgerRepository(ctx.session).record_decision(
        document_id=ctx.document_id,
        scope=DecisionScope.BOOK,
        key=key,
        value={"text": args.text.strip()},
        rationale=args.rationale.strip() or None,
        decided_by=ctx.actor_id,
        turn_id=ctx.turn_id,
    )
    return {"decision_id": decision.id, "key": key}


# --- registry and policy --------------------------------------------------------


def book_tool_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            ToolSpec("search_book", "Find sentences containing a word or phrase; returns the count and examples.", SearchBookArgs, ToolPermission.READ, search_book),
            ToolSpec("read_block", "Read one block (paragraph, heading, ...) in full.", ReadBlockArgs, ToolPermission.READ, read_block),
            ToolSpec("read_chapter_outline", "List chapters with their headings.", ReadChapterOutlineArgs, ToolPermission.READ, read_chapter_outline),
            ToolSpec("get_glossary", "The current glossary (locked and preferred terms).", GetGlossaryArgs, ToolPermission.READ, get_glossary),
            ToolSpec(
                "propose_term",
                "Record the preferred Chinese rendering of a term. Translation prompts use it; review does not block on it.",
                TermArgs,
                ToolPermission.WRITE_REVERSIBLE,
                propose_term,
            ),
            ToolSpec(
                "lock_term",
                "Lock a term's rendering: every occurrence must use it and review blocks on deviations. Use for names, abbreviations and unambiguous frequent terms.",
                TermArgs,
                ToolPermission.WRITE_IRREVERSIBLE,
                lock_term,
            ),
            ToolSpec(
                "record_decision",
                "Record a book-level decision for BOOK.md (genre, register, audience, preservation policy, style notes, resolved ambiguities).",
                RecordDecisionArgs,
                ToolPermission.WRITE_REVERSIBLE,
                record_decision,
                max_calls_per_turn=40,
            ),
        ]
    )


def terminology_policy() -> PermissionPolicy:
    def _is_name_like(ctx: ToolContext, args: BaseModel) -> bool:
        try:
            return _term_type(getattr(args, "term_type", "concept")) in _AUTO_LOCK_TERM_TYPES
        except ToolError:
            return False

    def _is_frequent(ctx: ToolContext, args: BaseModel) -> bool:
        term = str(getattr(args, "source_term", "") or "")
        return bool(term) and count_term_occurrences(ctx, term) >= FREQUENT_TERM_AUTO_LOCK_OCCURRENCES

    return PermissionPolicy(
        auto_approve=[
            AutoApproveRule("auto.lock_names_and_abbreviations", "lock_term", _is_name_like, "names, abbreviations and titles lock without review"),
            AutoApproveRule(
                "auto.lock_frequent_terms",
                "lock_term",
                _is_frequent,
                f"terms occurring in at least {FREQUENT_TERM_AUTO_LOCK_OCCURRENCES} sentences lock without review",
            ),
        ]
    )
