"""Reviewer Agent: a model reads finished translations and files issues in the review ledger.

The turn is document-scoped. ``start_turn`` picks the packets to read
(sampled or full) and stores the queue on the turn's first user item; the
model walks it with ``next_review_batch`` / ``finish_packet`` and files
findings with ``report_issue``. Progress is derived from the turn's own
tool results, so a resumed turn continues where it stopped.

Findings are ordinary ``review_issues`` rows (detector=model) written
through ``ReviewRepository.sync_issues``, using issue types the rule engine
already routes to repair actions. See docs/agent-upgrade/05.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from book_agent.core.ids import stable_id
from book_agent.domain.enums import (
    AgentItemKind,
    Detector,
    IssueStatus,
    PacketSentenceRole,
    PacketStatus,
    RootCauseLayer,
    RunStatus,
    Severity,
)
from book_agent.domain.models import Block, Chapter, Document, Sentence
from book_agent.domain.models.review import ReviewIssue
from book_agent.domain.models.translation import PacketSentenceMap, TargetSegment, TranslationPacket, TranslationRun
from book_agent.harness.context.book_md import load_book_guide
from book_agent.harness.kernel.budget import TurnBudget
from book_agent.harness.tools.book_tools import (
    GetGlossaryArgs,
    ReadBlockArgs,
    SearchBookArgs,
    get_glossary,
    read_block,
    search_book,
)
from book_agent.harness.tools.permissions import PermissionPolicy
from book_agent.harness.tools.registry import ToolContext, ToolError, ToolPermission, ToolRegistry, ToolSpec
from book_agent.infra.repositories.agent import AgentLedgerRepository
from book_agent.infra.repositories.review import ReviewRepository, active_target_texts
from book_agent.orchestrator.rule_engine import build_issue_action

AGENT_KIND = "reviewer"
MODE_SAMPLED = "sampled"
MODE_FULL = "full"
MODE_SKIP = "skip"
DEFAULT_PACKETS_PER_CHAPTER = 3
BLOCKING_CONFIDENCE = 0.85
# A model issue that keeps coming back after repair needs a human.
ESCALATE_AFTER_REOPENS = 2
QUEUE_KEY = "review_queue"

SYSTEM_PROMPT = """You are the reviewer for a professional English-to-Simplified-Chinese book translation.
You read source sentences next to their current Chinese translation and report real problems only.
Workflow: call next_review_batch, read every pair, call report_issue once per real problem, then call
finish_packet for that packet. Repeat until next_review_batch says done, then reply with a short Chinese
summary and no tool calls.
Report:
- MISTRANSLATION_SEMANTIC: the meaning is wrong (wrong sense, negation, number, actor).
- MISTRANSLATION_LOGIC: the logical relation, reference or emphasis is wrong.
- MISTRANSLATION_REFERENCE: it cannot be translated correctly without context the packet lacked.
- OMISSION: part of the source content is missing from the translation.
- STYLE_DRIFT: correct but clearly literal or unnatural Chinese that a professional editor would rewrite.
- TERM_CONFLICT: a term does not follow the glossary / BOOK.md (give source_term and expected_target_term).
Do not report matters of taste, punctuation variants, or problems the pair list marks as already known.
Severity: critical = misleads the reader on something important; high = wrong meaning; medium = noticeable
flaw; low = minor. Confidence is your probability that an expert editor agrees. Always give a short Chinese
explanation and, when you can, the corrected Chinese in suggested_target_text."""

ModelIssueType = Literal[
    "MISTRANSLATION_SEMANTIC",
    "MISTRANSLATION_LOGIC",
    "MISTRANSLATION_REFERENCE",
    "OMISSION",
    "STYLE_DRIFT",
    "TERM_CONFLICT",
]

_ROOT_CAUSE: dict[str, RootCauseLayer] = {
    "MISTRANSLATION_SEMANTIC": RootCauseLayer.TRANSLATION,
    "MISTRANSLATION_LOGIC": RootCauseLayer.TRANSLATION,
    "MISTRANSLATION_REFERENCE": RootCauseLayer.PACKET,
    "OMISSION": RootCauseLayer.TRANSLATION,
    "STYLE_DRIFT": RootCauseLayer.TRANSLATION,
    "TERM_CONFLICT": RootCauseLayer.MEMORY,
}
# A rule issue of these types on the same sentence already covers the finding.
_RULE_FAMILY: dict[str, set[str]] = {
    "OMISSION": {"OMISSION", "ALIGNMENT_FAILURE"},
    "STYLE_DRIFT": {"STYLE_DRIFT"},
    "TERM_CONFLICT": {"TERM_CONFLICT", "UNLOCKED_KEY_CONCEPT"},
}


# --- sampling -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _PacketCandidate:
    packet_id: str
    chapter_ordinal: int
    block_ordinal: int
    score: float


def select_review_packets(
    session: Session, document_id: str, *, mode: str, per_chapter: int = DEFAULT_PACKETS_PER_CHAPTER
) -> list[str]:
    """Translated packets to review, in reading order.

    ``full`` returns all of them. ``sampled`` keeps the ``per_chapter`` most
    suspicious packets of each chapter: a latest run carrying an output
    error code, then guardrail repairs, then low segment confidence.
    """
    if mode == MODE_SKIP:
        return []
    rows = session.execute(
        select(TranslationPacket.id, Chapter.ordinal, func.coalesce(Block.ordinal, 0))
        .join(Chapter, Chapter.id == TranslationPacket.chapter_id)
        .outerjoin(Block, Block.id == TranslationPacket.block_start_id)
        .where(Chapter.document_id == document_id, TranslationPacket.status == PacketStatus.TRANSLATED)
    ).all()
    if not rows:
        return []
    packet_ids = [str(row[0]) for row in rows]
    latest_runs: dict[str, TranslationRun] = {}
    for run in session.scalars(
        select(TranslationRun).where(
            TranslationRun.packet_id.in_(packet_ids), TranslationRun.status == RunStatus.SUCCEEDED
        )
    ).all():
        current = latest_runs.get(run.packet_id)
        if current is None or run.attempt > current.attempt:
            latest_runs[run.packet_id] = run
    confidence_by_run = dict(
        session.execute(
            select(TargetSegment.translation_run_id, func.avg(TargetSegment.confidence))
            .where(TargetSegment.translation_run_id.in_([run.id for run in latest_runs.values()]))
            .group_by(TargetSegment.translation_run_id)
        ).all()
    ) if latest_runs else {}
    candidates: list[_PacketCandidate] = []
    for packet_id, chapter_ordinal, block_ordinal in rows:
        run = latest_runs.get(str(packet_id))
        if run is None:
            continue
        score = 0.0
        if run.error_code:
            score += 4.0
        if int((run.model_config_json or {}).get("output_repairs") or 0) > 0:
            score += 2.0
        average = confidence_by_run.get(run.id)
        score += 1.0 - float(average if average is not None else 0.8)
        candidates.append(_PacketCandidate(str(packet_id), int(chapter_ordinal or 0), int(block_ordinal or 0), score))
    if mode != MODE_FULL:
        by_chapter: dict[int, list[_PacketCandidate]] = {}
        for candidate in candidates:
            by_chapter.setdefault(candidate.chapter_ordinal, []).append(candidate)
        candidates = [
            candidate
            for group in by_chapter.values()
            for candidate in sorted(group, key=lambda item: (-item.score, item.block_ordinal))[: max(1, per_chapter)]
        ]
    candidates.sort(key=lambda item: (item.chapter_ordinal, item.block_ordinal, item.packet_id))
    return [candidate.packet_id for candidate in candidates]


# --- queue state derived from the turn ledger --------------------------------------


def _turn_queue(ctx: ToolContext) -> list[str]:
    for item in AgentLedgerRepository(ctx.session).list_items(ctx.turn_id):
        if item.kind == AgentItemKind.USER and QUEUE_KEY in (item.content_json or {}):
            return [str(packet_id) for packet_id in item.content_json[QUEUE_KEY]]
    return []


def _finished_packets(ctx: ToolContext) -> set[str]:
    finished: set[str] = set()
    for item in AgentLedgerRepository(ctx.session).list_items(ctx.turn_id):
        content = item.content_json or {}
        if item.kind != AgentItemKind.TOOL_RESULT or content.get("name") != "finish_packet":
            continue
        result = content.get("result") or {}
        if result.get("ok"):
            finished.add(str((result.get("output") or {}).get("packet_id")))
    return finished


def _current_sentences(session: Session, packet_id: str) -> list[Sentence]:
    return list(
        session.scalars(
            select(Sentence)
            .join(PacketSentenceMap, PacketSentenceMap.sentence_id == Sentence.id)
            .join(Block, Block.id == Sentence.block_id)
            .where(PacketSentenceMap.packet_id == packet_id, PacketSentenceMap.role == PacketSentenceRole.CURRENT)
            .order_by(Block.ordinal.asc(), Sentence.ordinal_in_block.asc())
        ).all()
    )


def _packet_for_document(ctx: ToolContext, packet_id: str) -> tuple[TranslationPacket, Chapter]:
    packet = ctx.session.get(TranslationPacket, packet_id)
    chapter = ctx.session.get(Chapter, packet.chapter_id) if packet is not None else None
    if packet is None or chapter is None or chapter.document_id != ctx.document_id:
        raise ToolError(f"packet not found in this book: {packet_id}")
    return packet, chapter


# --- tools ------------------------------------------------------------------------


class NextReviewBatchArgs(BaseModel):
    pass


class ReportIssueArgs(BaseModel):
    packet_id: str = Field(description="packet_id from next_review_batch.")
    sentence_alias: str = Field(description="Alias of the sentence the problem is in, e.g. S2.")
    issue_type: ModelIssueType
    severity: Literal["low", "medium", "high", "critical"]
    confidence: float = Field(ge=0.0, le=1.0, description="Probability an expert editor agrees.")
    explanation: str = Field(min_length=1, description="What is wrong, in Chinese, one or two sentences.")
    suggested_target_text: str = Field(default="", description="Corrected Chinese for the sentence, or empty.")
    source_term: str = Field(default="", description="TERM_CONFLICT only: the English term.")
    expected_target_term: str = Field(default="", description="TERM_CONFLICT only: the rendering the glossary requires.")


class FinishPacketArgs(BaseModel):
    packet_id: str
    note: str = Field(default="", description="Optional one-line note, e.g. 'clean'.")


def next_review_batch(ctx: ToolContext, args: NextReviewBatchArgs) -> dict[str, Any]:
    queue = _turn_queue(ctx)
    finished = _finished_packets(ctx)
    remaining = [packet_id for packet_id in queue if packet_id not in finished]
    if not remaining:
        return {"done": True, "reviewed": len(finished), "queued": len(queue)}
    packet_id = remaining[0]
    _, chapter = _packet_for_document(ctx, packet_id)
    sentences = _current_sentences(ctx.session, packet_id)
    targets = active_target_texts(ctx.session, [sentence.id for sentence in sentences])
    known = _active_issue_types_by_sentence(ctx.session, [sentence.id for sentence in sentences])
    pairs = []
    for index, sentence in enumerate(sentences, start=1):
        pairs.append(
            {
                "alias": f"S{index}",
                "source": sentence.source_text,
                "target": targets.get(sentence.id, ""),
                **({"known_issues": sorted(known[sentence.id])} if known.get(sentence.id) else {}),
            }
        )
    return {
        "done": False,
        "packet_id": packet_id,
        "chapter": f"{chapter.ordinal}. {chapter.title_src or ''}".strip(),
        "remaining_after_this": len(remaining) - 1,
        "pairs": pairs,
    }


def _active_issue_types_by_sentence(session: Session, sentence_ids: list[str]) -> dict[str, set[str]]:
    if not sentence_ids:
        return {}
    rows = session.execute(
        select(ReviewIssue.sentence_id, ReviewIssue.issue_type).where(
            ReviewIssue.sentence_id.in_(sentence_ids),
            ReviewIssue.status.in_([IssueStatus.OPEN, IssueStatus.TRIAGED, IssueStatus.WONTFIX]),
        )
    ).all()
    known: dict[str, set[str]] = {}
    for sentence_id, issue_type in rows:
        known.setdefault(str(sentence_id), set()).add(str(issue_type))
    return known


def report_issue(ctx: ToolContext, args: ReportIssueArgs) -> dict[str, Any]:
    packet, chapter = _packet_for_document(ctx, args.packet_id)
    sentences = _current_sentences(ctx.session, packet.id)
    try:
        index = int(args.sentence_alias.strip().upper().lstrip("S")) - 1
        if index < 0:
            raise IndexError
        sentence = sentences[index]
    except (ValueError, IndexError) as exc:
        raise ToolError(f"unknown sentence alias {args.sentence_alias!r}; this packet has S1..S{len(sentences)}") from exc
    if args.issue_type == "TERM_CONFLICT" and not (args.source_term.strip() and args.expected_target_term.strip()):
        raise ToolError("TERM_CONFLICT needs source_term and expected_target_term")

    session = ctx.session
    covering = session.scalars(
        select(ReviewIssue).where(
            ReviewIssue.sentence_id == sentence.id,
            ReviewIssue.detector != Detector.MODEL,
            ReviewIssue.issue_type.in_(sorted(_RULE_FAMILY.get(args.issue_type, set()))),
            ReviewIssue.status.in_([IssueStatus.OPEN, IssueStatus.TRIAGED, IssueStatus.WONTFIX]),
        )
    ).first()
    if covering is not None:
        return {"recorded": False, "reason": f"already covered by {covering.issue_type} issue {covering.id}"}

    issue_id = stable_id("review-issue", ctx.document_id, chapter.id, sentence.id, args.issue_type, "model")
    existing = session.get(ReviewIssue, issue_id)
    severity = Severity(args.severity)
    blocking = severity in (Severity.HIGH, Severity.CRITICAL) and args.confidence >= BLOCKING_CONFIDENCE
    escalated = (
        existing is not None
        and existing.status == IssueStatus.RESOLVED
        and existing.decided_by is None
        and int(existing.reopen_count or 0) + 1 >= ESCALATE_AFTER_REOPENS
    )
    target_text = active_target_texts(session, [sentence.id]).get(sentence.id, "")
    evidence: dict[str, Any] = {
        "reason": "model_review",
        "explanation": args.explanation.strip(),
        "source_text": sentence.source_text,
        "actual_target_text": target_text,
        "confidence": round(float(args.confidence), 3),
        "turn_id": ctx.turn_id,
    }
    if args.suggested_target_text.strip():
        evidence["suggested_target_text"] = args.suggested_target_text.strip()
    if args.issue_type == "TERM_CONFLICT":
        evidence["source_term"] = args.source_term.strip()
        evidence["source_terms"] = [args.source_term.strip()]
        evidence["expected_target_term"] = args.expected_target_term.strip()
    if args.issue_type == "STYLE_DRIFT":
        evidence["prompt_guidance"] = args.explanation.strip()
        if args.suggested_target_text.strip():
            evidence["preferred_hint"] = args.suggested_target_text.strip()
    if escalated:
        evidence["escalation"] = "reopened_after_repair"
        blocking = True
    incoming = ReviewIssue(
        id=issue_id,
        document_id=ctx.document_id,
        chapter_id=chapter.id,
        sentence_id=sentence.id,
        packet_id=packet.id,
        issue_type=args.issue_type,
        root_cause_layer=_ROOT_CAUSE[args.issue_type],
        severity=severity,
        blocking=blocking,
        detector=Detector.MODEL,
        confidence=round(float(args.confidence), 3),
        evidence_json=evidence,
        status=IssueStatus.OPEN,
    )
    sync = ReviewRepository(session).sync_issues(
        [incoming],
        [build_issue_action(incoming)],
        owned_existing=[],
        resolution_note="",
        actor_id=f"agent:{AGENT_KIND}",
    )
    issue = sync.issues[0]
    return {
        "recorded": issue.status in (IssueStatus.OPEN, IssueStatus.TRIAGED),
        "issue_id": issue.id,
        "status": issue.status.value,
        "blocking": bool(issue.blocking),
        "reopened": bool(sync.reopened),
        **({"note": "a human closed this issue; it stays closed"} if sync.seen_while_closed else {}),
    }


def finish_packet(ctx: ToolContext, args: FinishPacketArgs) -> dict[str, Any]:
    if args.packet_id not in _turn_queue(ctx):
        raise ToolError(f"packet {args.packet_id} is not in this review queue")
    finished = _finished_packets(ctx) | {args.packet_id}
    return {"packet_id": args.packet_id, "remaining": len([p for p in _turn_queue(ctx) if p not in finished])}


def reviewer_tool_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            ToolSpec(
                "next_review_batch",
                "The next packet to review: source/target sentence pairs with aliases, or done.",
                NextReviewBatchArgs,
                ToolPermission.READ,
                next_review_batch,
            ),
            ToolSpec(
                "report_issue",
                "File one translation problem for a sentence of the current packet.",
                ReportIssueArgs,
                ToolPermission.WRITE_REVERSIBLE,
                report_issue,
                max_calls_per_turn=400,
            ),
            ToolSpec(
                "finish_packet",
                "Mark the packet as reviewed after reporting its problems (or none).",
                FinishPacketArgs,
                ToolPermission.WRITE_REVERSIBLE,
                finish_packet,
                max_calls_per_turn=2000,
            ),
            ToolSpec("search_book", "Find sentences containing a word or phrase.", SearchBookArgs, ToolPermission.READ, search_book),
            ToolSpec("read_block", "Read one block in full.", ReadBlockArgs, ToolPermission.READ, read_block),
            ToolSpec("get_glossary", "The current glossary (locked and preferred terms).", GetGlossaryArgs, ToolPermission.READ, get_glossary),
        ]
    )


# --- turn creation --------------------------------------------------------------


@dataclass(slots=True)
class ReviewerTurnSeed:
    turn_id: str
    queued_packets: int


class ReviewerAgent:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.ledger = AgentLedgerRepository(session)

    @staticmethod
    def registry() -> ToolRegistry:
        return reviewer_tool_registry()

    @staticmethod
    def policy() -> PermissionPolicy:
        return PermissionPolicy()

    def start_turn(
        self,
        *,
        document_id: str,
        model_name: str,
        mode: str = MODE_SAMPLED,
        run_id: str | None = None,
        work_item_id: str | None = None,
        per_chapter: int = DEFAULT_PACKETS_PER_CHAPTER,
        budget: TurnBudget | None = None,
    ) -> ReviewerTurnSeed:
        document = self.session.get(Document, document_id)
        if document is None:
            raise ValueError(f"document not found: {document_id}")
        queue = select_review_packets(self.session, document_id, mode=mode, per_chapter=per_chapter)
        guide = load_book_guide(self.session, document_id)
        turn = self.ledger.create_turn(
            document_id=document_id,
            agent_kind=AGENT_KIND,
            scope_type="document",
            scope_id=document_id,
            run_id=run_id,
            work_item_id=work_item_id,
            model_name=model_name,
            # About three model steps per packet (read, report, finish) plus slack.
            budget=(budget or TurnBudget(max_steps=len(queue) * 3 + 10, max_tool_calls=len(queue) * 6 + 20)).to_json(),
            skills=["review/default"],
        )
        self.ledger.append_item(turn.id, kind=AgentItemKind.SYSTEM, content={"text": SYSTEM_PROMPT})
        guidance = guide.render_prompt_guidance()
        if guidance.strip():
            self.ledger.append_item(turn.id, kind=AgentItemKind.SYSTEM, content={"text": guidance})
        brief = "\n".join(
            [
                f"Book: {document.title_src or document.title or '(untitled)'}",
                f"Mode: {mode}. Packets to review: {len(queue)}.",
                "Start with next_review_batch." if queue else "Nothing to review; reply with a one-line summary.",
            ]
        )
        self.ledger.append_item(turn.id, kind=AgentItemKind.USER, content={"text": brief, QUEUE_KEY: queue})
        self.session.flush()
        return ReviewerTurnSeed(turn_id=turn.id, queued_packets=len(queue))
