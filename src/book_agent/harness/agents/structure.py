"""Structure Agent: a model looks at suspicious PDF pages, files structure issues and can propose block edits.

Triggered only for pages the parser itself doubts (high layout risk or a
layout-suspect verdict in ``pdf_page_evidence``). For each page the model can
read the recovered blocks (type, role, text, box) and look at the rendered
page image, then file ``STRUCTURE_SUGGESTION`` issues: a block of the wrong
type, a missed or false heading, text wrongly merged or split, a caption
linked to the wrong artifact, reading order.

With the run request ``structure_edits=on`` it also gets ``relabel_block``,
``merge_blocks`` and ``link_caption``. They are irreversible-tier tools: the
turn waits for a person to approve each call, which is then applied through
``services/structure_edits.py`` (a parse-revision fork that keeps
translations) and logged for replay after later reparses. Splitting a block
stays an issue for a person.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.core.ids import stable_id
from book_agent.domain.enums import (
    AgentItemKind,
    ArtifactStatus,
    Detector,
    IssueStatus,
    RootCauseLayer,
    Severity,
    SourceType,
)
from book_agent.domain.models import Block, Chapter, Document
from book_agent.domain.models.review import ReviewIssue
from book_agent.harness.kernel.budget import TurnBudget
from book_agent.harness.kernel.messages import IMAGE_OUTPUT_KEY
from book_agent.harness.tools.permissions import PermissionPolicy
from book_agent.harness.tools.registry import ToolContext, ToolError, ToolPermission, ToolRegistry, ToolSpec
from book_agent.infra.repositories.agent import AgentLedgerRepository
from book_agent.infra.repositories.review import ReviewRepository
from book_agent.orchestrator.rule_engine import build_issue_action

AGENT_KIND = "structure"
ISSUE_TYPE = "STRUCTURE_SUGGESTION"
MODE_SAMPLED = "sampled"
MODE_FULL = "full"
MODE_SKIP = "skip"
SAMPLED_PAGE_LIMIT = 20
QUEUE_KEY = "structure_queue"
PAGE_IMAGE_DPI = 110
MAX_BLOCKS_PER_PAGE = 80

SYSTEM_PROMPT = """You check the structure a PDF parser recovered for a book before it is translated.
For each page you get from next_suspect_page: read the blocks (type, role, text, box), call render_page_image
to look at the page, and compare. File report_structure_problem for each real problem:
- wrong_block_type: e.g. a heading parsed as a paragraph, code parsed as prose, a table parsed as text;
- missed_heading / false_heading;
- bad_merge: two separate paragraphs/items joined; bad_split: one paragraph cut into pieces;
- caption_link: a caption attached to the wrong figure/table, or not attached;
- reading_order: blocks out of order (columns, sidebars);
- lost_content: text visible on the page but missing from the blocks.
Name the block ids involved and say concretely what the structure should be. Do not report styling,
hyphenation or OCR typos. Call finish_page after each page. When next_suspect_page says done, reply with a
short Chinese summary and no tool calls."""

ProblemKind = Literal[
    "wrong_block_type", "missed_heading", "false_heading", "bad_merge", "bad_split", "caption_link", "reading_order", "lost_content"
]


def suspect_pages(document: Document, *, mode: str) -> list[int]:
    if mode == MODE_SKIP or document.source_type not in {SourceType.PDF_TEXT, SourceType.PDF_MIXED, SourceType.PDF_SCAN}:
        return []
    evidence = (document.metadata_json or {}).get("pdf_page_evidence") or {}
    pages = evidence.get("pdf_pages") if isinstance(evidence, dict) else None
    scored: list[tuple[int, int]] = []
    for page in pages or []:
        if not isinstance(page, dict) or not isinstance(page.get("page_number"), int):
            continue
        risk = str(page.get("page_layout_risk") or "").lower()
        score = (2 if risk == "high" else 0) + (1 if page.get("layout_suspect") else 0)
        if score:
            scored.append((score, page["page_number"]))
    scored.sort(key=lambda item: (-item[0], item[1]))
    selected = scored if mode == MODE_FULL else scored[:SAMPLED_PAGE_LIMIT]
    return sorted(page for _, page in selected)


def _page_evidence(document: Document, page_number: int) -> dict[str, Any]:
    evidence = (document.metadata_json or {}).get("pdf_page_evidence") or {}
    for page in (evidence.get("pdf_pages") if isinstance(evidence, dict) else None) or []:
        if isinstance(page, dict) and page.get("page_number") == page_number:
            return page
    return {}


def _turn_queue(ctx: ToolContext) -> list[int]:
    for item in AgentLedgerRepository(ctx.session).list_items(ctx.turn_id):
        if item.kind == AgentItemKind.USER and QUEUE_KEY in (item.content_json or {}):
            return [int(page) for page in item.content_json[QUEUE_KEY]]
    return []


def _finished_pages(ctx: ToolContext) -> set[int]:
    finished: set[int] = set()
    for item in AgentLedgerRepository(ctx.session).list_items(ctx.turn_id):
        content = item.content_json or {}
        if item.kind == AgentItemKind.TOOL_RESULT and content.get("name") == "finish_page":
            result = content.get("result") or {}
            if result.get("ok"):
                finished.add(int((result.get("output") or {}).get("page_number") or 0))
    return finished


def _page_blocks(session: Session, document_id: str, page_number: int) -> list[Block]:
    blocks = session.scalars(
        select(Block)
        .join(Chapter, Chapter.id == Block.chapter_id)
        .where(Chapter.document_id == document_id, Block.status == ArtifactStatus.ACTIVE)
        .order_by(Chapter.ordinal, Block.ordinal)
    ).all()
    on_page = []
    for block in blocks:
        span = block.source_span_json or {}
        start, end = span.get("source_page_start"), span.get("source_page_end", span.get("source_page_start"))
        if isinstance(start, int) and isinstance(end, int) and start <= page_number <= end:
            on_page.append(block)
    return on_page


def _document(ctx: ToolContext) -> Document:
    document = ctx.session.get(Document, ctx.document_id)
    if document is None:
        raise ToolError("document not found")
    return document


# --- tools ------------------------------------------------------------------------


class NextSuspectPageArgs(BaseModel):
    pass


class RenderPageImageArgs(BaseModel):
    page_number: int = Field(ge=1)


class ReportStructureProblemArgs(BaseModel):
    page_number: int = Field(ge=1)
    kind: ProblemKind
    block_ids: list[str] = Field(default_factory=list, description="Blocks involved, from next_suspect_page.")
    suggestion: str = Field(min_length=1, description="What the structure should be, concretely.")
    severity: Literal["low", "medium", "high"] = "medium"
    confidence: float = Field(ge=0.0, le=1.0)


class FinishPageArgs(BaseModel):
    page_number: int = Field(ge=1)


def next_suspect_page(ctx: ToolContext, args: NextSuspectPageArgs) -> dict[str, Any]:
    queue = _turn_queue(ctx)
    remaining = [page for page in queue if page not in _finished_pages(ctx)]
    if not remaining:
        return {"done": True, "reviewed": len(queue)}
    page_number = remaining[0]
    document = _document(ctx)
    evidence = _page_evidence(document, page_number)
    blocks = _page_blocks(ctx.session, ctx.document_id, page_number)
    return {
        "done": False,
        "page_number": page_number,
        "remaining_after_this": len(remaining) - 1,
        "layout_risk": evidence.get("page_layout_risk"),
        "layout_reasons": evidence.get("page_layout_reasons"),
        "page_family": evidence.get("page_family"),
        "blocks": [
            {
                "block_id": block.id,
                "type": block.block_type.value,
                "role": (block.source_span_json or {}).get("pdf_block_role"),
                "bbox": (block.source_span_json or {}).get("source_bbox_json"),
                "text": (block.source_text or "")[:300],
            }
            for block in blocks[:MAX_BLOCKS_PER_PAGE]
        ],
        **({"truncated_block_count": len(blocks) - MAX_BLOCKS_PER_PAGE} if len(blocks) > MAX_BLOCKS_PER_PAGE else {}),
    }


def render_page_image(ctx: ToolContext, args: RenderPageImageArgs) -> dict[str, Any]:
    document = _document(ctx)
    source = Path(str(document.source_path or "")).expanduser()
    if not source.is_file():
        raise ToolError("the source PDF is not available")
    renderer = ctx.extras.get("page_renderer") or render_pdf_page_png
    png = renderer(source, args.page_number)
    return {
        "page_number": args.page_number,
        "note": "The page image is attached below.",
        IMAGE_OUTPUT_KEY: [{"media_type": "image/png", "data": base64.b64encode(png).decode("ascii")}],
    }


def render_pdf_page_png(path: Path, page_number: int, *, dpi: int = PAGE_IMAGE_DPI) -> bytes:
    import fitz  # PyMuPDF, already used by extraction

    with fitz.open(str(path)) as pdf:
        if page_number > pdf.page_count:
            raise ToolError(f"page {page_number} is beyond the last page ({pdf.page_count})")
        return pdf.load_page(page_number - 1).get_pixmap(dpi=dpi).tobytes("png")


def report_structure_problem(ctx: ToolContext, args: ReportStructureProblemArgs) -> dict[str, Any]:
    document = _document(ctx)
    page_blocks = {block.id: block for block in _page_blocks(ctx.session, ctx.document_id, args.page_number)}
    unknown = [block_id for block_id in args.block_ids if block_id not in page_blocks]
    if unknown:
        raise ToolError(f"blocks not on page {args.page_number}: {', '.join(unknown)}")
    anchor = page_blocks[args.block_ids[0]] if args.block_ids else None
    chapter_id = anchor.chapter_id if anchor is not None else None
    if chapter_id is None:
        first = next(iter(page_blocks.values()), None)
        chapter_id = first.chapter_id if first is not None else None
    issue = ReviewIssue(
        id=stable_id("review-issue", document.id, "page", str(args.page_number), ISSUE_TYPE, args.kind, *sorted(args.block_ids)),
        document_id=document.id,
        chapter_id=chapter_id,
        block_id=anchor.id if anchor is not None else None,
        issue_type=ISSUE_TYPE,
        root_cause_layer=RootCauseLayer.STRUCTURE,
        severity=Severity(args.severity),
        blocking=False,
        detector=Detector.MODEL,
        confidence=round(float(args.confidence), 3),
        evidence_json={
            "reason": "structure_review",
            "page_number": args.page_number,
            "kind": args.kind,
            "block_ids": list(args.block_ids),
            "suggestion": args.suggestion,
            "turn_id": ctx.turn_id,
        },
        status=IssueStatus.OPEN,
    )
    sync = ReviewRepository(ctx.session).sync_issues(
        [issue], [build_issue_action(issue)], owned_existing=[], resolution_note="", actor_id=f"agent:{AGENT_KIND}"
    )
    persisted = sync.issues[0]
    return {"issue_id": persisted.id, "status": persisted.status.value, "recorded": persisted.status == IssueStatus.OPEN}


def finish_page(ctx: ToolContext, args: FinishPageArgs) -> dict[str, Any]:
    if args.page_number not in _turn_queue(ctx):
        raise ToolError(f"page {args.page_number} is not in this review queue")
    return {"page_number": args.page_number}


EDITABLE_TYPES = Literal["heading", "paragraph", "quote", "footnote", "caption", "code", "list_item", "equation"]


class RelabelBlockArgs(BaseModel):
    block_id: str
    block_type: EDITABLE_TYPES
    heading_level: int | None = Field(default=None, ge=1, le=6, description="Only for headings.")
    reason: str = Field(min_length=1, description="What on the page shows this; a person reads it before approving.")


class MergeBlocksArgs(BaseModel):
    first_block_id: str
    second_block_id: str = Field(description="Must directly follow the first block, with the same type.")
    reason: str = Field(min_length=1)


class LinkCaptionArgs(BaseModel):
    caption_block_id: str = Field(description="A caption block (relabel it first if it is parsed as something else).")
    artifact_block_id: str = Field(description="The figure, image, table, equation or code block it describes.")
    reason: str = Field(min_length=1)


def _edit(ctx: ToolContext, apply) -> dict[str, Any]:
    from book_agent.services.structure_edits import StructureEditRejected, StructureEditService

    try:
        outcome = apply(StructureEditService(ctx.session))
    except StructureEditRejected as exc:
        raise ToolError(str(exc)) from exc
    return {"applied": True, **outcome.to_json()}


def relabel_block(ctx: ToolContext, args: RelabelBlockArgs) -> dict[str, Any]:
    return _edit(
        ctx,
        lambda service: service.relabel_block(
            ctx.document_id, args.block_id, args.block_type, heading_level=args.heading_level,
            actor_id=f"agent:{AGENT_KIND}", reason=args.reason, turn_id=ctx.turn_id,
        ),
    )


def merge_blocks(ctx: ToolContext, args: MergeBlocksArgs) -> dict[str, Any]:
    return _edit(
        ctx,
        lambda service: service.merge_blocks(
            ctx.document_id, args.first_block_id, args.second_block_id,
            actor_id=f"agent:{AGENT_KIND}", reason=args.reason, turn_id=ctx.turn_id,
        ),
    )


def link_caption(ctx: ToolContext, args: LinkCaptionArgs) -> dict[str, Any]:
    return _edit(
        ctx,
        lambda service: service.link_caption(
            ctx.document_id, args.caption_block_id, args.artifact_block_id,
            actor_id=f"agent:{AGENT_KIND}", reason=args.reason, turn_id=ctx.turn_id,
        ),
    )


EDIT_TOOL_NAMES = ("relabel_block", "merge_blocks", "link_caption")

EDIT_PROMPT = """
You can also fix structure directly with relabel_block (wrong block type or heading level), merge_blocks (one
paragraph or item cut into two adjacent blocks of the same type) and link_caption (a caption attached to the wrong
artifact or to none). A person approves every edit before it is applied, so give a concrete reason from the page
image. Prefer an edit over report_structure_problem when one of these tools fixes the problem exactly; report
everything else (e.g. a bad merge that needs a split, reading order, lost content)."""


def structure_tool_registry(*, allow_edits: bool = False) -> ToolRegistry:
    tools = [
        ToolSpec("next_suspect_page", "The next doubtful page: layout evidence and its recovered blocks, or done.", NextSuspectPageArgs, ToolPermission.READ, next_suspect_page),
        ToolSpec("render_page_image", "Render a page of the source PDF and attach the image.", RenderPageImageArgs, ToolPermission.READ, render_page_image, max_calls_per_turn=200),
        ToolSpec("report_structure_problem", "File one structure problem on a page (non-blocking).", ReportStructureProblemArgs, ToolPermission.WRITE_REVERSIBLE, report_structure_problem, max_calls_per_turn=400),
        ToolSpec("finish_page", "Mark a page as reviewed.", FinishPageArgs, ToolPermission.WRITE_REVERSIBLE, finish_page, max_calls_per_turn=400),
    ]
    if allow_edits:
        tools += [
            ToolSpec("relabel_block", "Change a block's type (and heading level). Needs approval.", RelabelBlockArgs, ToolPermission.WRITE_IRREVERSIBLE, relabel_block, max_calls_per_turn=200),
            ToolSpec("merge_blocks", "Merge a block into the block right before it. Needs approval.", MergeBlocksArgs, ToolPermission.WRITE_IRREVERSIBLE, merge_blocks, max_calls_per_turn=200),
            ToolSpec("link_caption", "Attach a caption to its figure, table or other artifact. Needs approval.", LinkCaptionArgs, ToolPermission.WRITE_IRREVERSIBLE, link_caption, max_calls_per_turn=200),
        ]
    return ToolRegistry(tools)


@dataclass(slots=True)
class StructureTurnSeed:
    turn_id: str
    queued_pages: int


class StructureAgent:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.ledger = AgentLedgerRepository(session)

    @staticmethod
    def registry(*, allow_edits: bool = False) -> ToolRegistry:
        return structure_tool_registry(allow_edits=allow_edits)

    @staticmethod
    def policy() -> PermissionPolicy:
        return PermissionPolicy()

    def start_turn(
        self,
        *,
        document_id: str,
        model_name: str,
        mode: str = MODE_SAMPLED,
        allow_edits: bool = False,
        run_id: str | None = None,
        work_item_id: str | None = None,
        budget: TurnBudget | None = None,
    ) -> StructureTurnSeed:
        document = self.session.get(Document, document_id)
        if document is None:
            raise ValueError(f"document not found: {document_id}")
        queue = suspect_pages(document, mode=mode)
        turn = self.ledger.create_turn(
            document_id=document_id,
            agent_kind=AGENT_KIND,
            scope_type="document",
            scope_id=document_id,
            run_id=run_id,
            work_item_id=work_item_id,
            model_name=model_name,
            budget=(budget or TurnBudget(max_steps=len(queue) * 4 + 10, max_tool_calls=len(queue) * 8 + 20)).to_json(),
            skills=["structure/default"],
        )
        self.ledger.append_item(
            turn.id, kind=AgentItemKind.SYSTEM, content={"text": SYSTEM_PROMPT + (EDIT_PROMPT if allow_edits else "")}
        )
        brief = (
            f"Book: {document.title_src or document.title or '(untitled)'}. Doubtful pages to check: {len(queue)}. "
            + ("Start with next_suspect_page." if queue else "Nothing to check; reply with a one-line summary.")
        )
        self.ledger.append_item(turn.id, kind=AgentItemKind.USER, content={"text": brief, QUEUE_KEY: queue})
        self.session.flush()
        return StructureTurnSeed(turn_id=turn.id, queued_pages=len(queue))
