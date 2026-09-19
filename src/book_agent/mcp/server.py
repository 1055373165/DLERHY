"""MCP server (stdio) exposing book-agent's read and reversible tools to MCP clients.

Claude Code, Codex and other MCP clients can inspect books, runs and issues
and make reversible changes (preferred terms, book decisions, issue triage)
through the same typed tools the in-product agents use. Irreversible tools
(locking terms, marking issues wontfix, anything that spends provider money)
are not exposed.

Transport: newline-delimited JSON-RPC 2.0 on stdin/stdout (MCP stdio). The
protocol subset implemented is initialize, notifications/initialized, ping,
tools/list and tools/call. Run with ``book-agent mcp``.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TextIO

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from book_agent.domain.enums import IssueStatus
from book_agent.domain.models import Document
from book_agent.harness.tools import book_tools
from book_agent.harness.tools.registry import ToolContext, ToolError

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "book-agent", "version": "h4"}


@dataclass(frozen=True, slots=True)
class McpTool:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[Session, BaseModel], Any]
    writes: bool = False

    def schema(self) -> dict[str, Any]:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": schema,
            "annotations": {"readOnlyHint": not self.writes, "destructiveHint": False},
        }


# --- tool arguments ------------------------------------------------------------------------


class ListDocumentsArgs(BaseModel):
    query: str = Field(default="", description="Filter by title or author substring.")
    limit: int = Field(default=20, ge=1, le=100)


class DocumentArgs(BaseModel):
    document_id: str


class SearchBookArgs(book_tools.SearchBookArgs):
    document_id: str


class ReadBlockArgs(book_tools.ReadBlockArgs):
    document_id: str


class ReadOutlineArgs(book_tools.ReadChapterOutlineArgs):
    document_id: str


class ListIssuesArgs(BaseModel):
    document_id: str
    status: str = Field(default="active", description="active (open+triaged), all, open, triaged, resolved, wontfix")
    blocking: bool | None = None
    limit: int = Field(default=50, ge=1, le=200)


class IssueArgs(BaseModel):
    issue_id: str


class TriageIssueArgs(BaseModel):
    issue_id: str
    note: str = Field(default="", description="Why, recorded in the issue history.")


class RunArgs(BaseModel):
    run_id: str


class ProposeTermArgs(book_tools.TermArgs):
    document_id: str


class RecordDecisionArgs(book_tools.RecordDecisionArgs):
    document_id: str


# --- handlers ---------------------------------------------------------------------------


def _ctx(session: Session, document_id: str) -> ToolContext:
    if session.get(Document, document_id) is None:
        raise ToolError(f"document not found: {document_id}")
    return ToolContext(session=session, document_id=document_id, agent_kind="mcp", turn_id="", actor_id="mcp")


def _without(args: BaseModel, cls: type[BaseModel]) -> BaseModel:
    return cls.model_validate(args.model_dump(exclude={"document_id"}))


def list_documents(session: Session, args: ListDocumentsArgs) -> dict[str, Any]:
    stmt = select(Document).order_by(Document.updated_at.desc()).limit(args.limit)
    if args.query.strip():
        pattern = f"%{args.query.strip()}%"
        stmt = stmt.where((Document.title.ilike(pattern)) | (Document.author.ilike(pattern)))
    return {
        "documents": [
            {"document_id": d.id, "title": d.title, "author": d.author, "source_type": d.source_type.value, "status": d.status.value}
            for d in session.scalars(stmt).all()
        ]
    }


def document_summary(session: Session, args: DocumentArgs) -> dict[str, Any]:
    from dataclasses import asdict

    from book_agent.services.workflows import DocumentWorkflowService

    _ctx(session, args.document_id)
    summary = asdict(DocumentWorkflowService(session).get_document_summary(args.document_id))
    summary.pop("chapters", None)
    return summary


def book_guide(session: Session, args: DocumentArgs) -> dict[str, Any]:
    from book_agent.harness.context.book_md import load_book_guide

    _ctx(session, args.document_id)
    return {"markdown": load_book_guide(session, args.document_id).render_markdown()}


def list_issues(session: Session, args: ListIssuesArgs) -> dict[str, Any]:
    from book_agent.services.issues import IssueService

    _ctx(session, args.document_id)
    page = IssueService(session).list_issues(args.document_id, status=args.status, blocking=args.blocking, limit=args.limit)
    return {
        "total_count": page.total_count,
        "issues": [
            {
                "issue_id": issue.id,
                "issue_type": issue.issue_type,
                "severity": issue.severity.value,
                "blocking": issue.blocking,
                "detector": issue.detector.value,
                "status": issue.status.value,
                "evidence": issue.evidence_json,
            }
            for issue in page.entries
        ],
    }


def get_issue(session: Session, args: IssueArgs) -> dict[str, Any]:
    from book_agent.services.issues import IssueService

    try:
        detail = IssueService(session).detail(args.issue_id)
    except LookupError as exc:
        raise ToolError(str(exc)) from exc
    return {
        "issue_id": detail.issue.id,
        "issue_type": detail.issue.issue_type,
        "status": detail.issue.status.value,
        "evidence": detail.issue.evidence_json,
        "source_text": detail.source_text,
        "target_text": detail.target_text,
        "history": [{"kind": e.kind.value, "actor": e.actor_id, "note": e.note} for e in detail.events],
        "actions": [{"action_id": a.id, "action_type": a.action_type.value, "status": a.status.value} for a in detail.actions],
    }


def triage_issue(session: Session, args: TriageIssueArgs) -> dict[str, Any]:
    from book_agent.services.issues import IssueService, IssueTransitionError

    try:
        issue = IssueService(session).transition(args.issue_id, to_status=IssueStatus.TRIAGED, actor_id="mcp", note=args.note or None)
    except (LookupError, IssueTransitionError) as exc:
        raise ToolError(str(exc)) from exc
    return {"issue_id": issue.id, "status": issue.status.value}


def run_summary(session: Session, args: RunArgs) -> dict[str, Any]:
    from dataclasses import asdict

    from book_agent.infra.repositories.run_control import RunControlRepository
    from book_agent.services.run_control import RunControlService

    try:
        summary = RunControlService(RunControlRepository(session)).get_run_summary(args.run_id)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return json.loads(json.dumps(asdict(summary), default=str))


TOOLS: tuple[McpTool, ...] = (
    McpTool("list_documents", "List books in the workspace (newest first).", ListDocumentsArgs, list_documents),
    McpTool("document_summary", "Status, counts and latest run of one book.", DocumentArgs, document_summary),
    McpTool("book_guide", "The book's BOOK.md: glossary and translation decisions.", DocumentArgs, book_guide),
    McpTool(
        "search_book", "Find source sentences containing a word or phrase.", SearchBookArgs,
        lambda s, a: book_tools.search_book(_ctx(s, a.document_id), _without(a, book_tools.SearchBookArgs)),
    ),
    McpTool(
        "read_block", "Read one source block in full.", ReadBlockArgs,
        lambda s, a: book_tools.read_block(_ctx(s, a.document_id), _without(a, book_tools.ReadBlockArgs)),
    ),
    McpTool(
        "read_chapter_outline", "Chapters and their headings.", ReadOutlineArgs,
        lambda s, a: book_tools.read_chapter_outline(_ctx(s, a.document_id), _without(a, book_tools.ReadChapterOutlineArgs)),
    ),
    McpTool(
        "get_glossary", "Locked and preferred terms.", DocumentArgs,
        lambda s, a: book_tools.get_glossary(_ctx(s, a.document_id), book_tools.GetGlossaryArgs()),
    ),
    McpTool("list_issues", "Review issues of a book, blocking first.", ListIssuesArgs, list_issues),
    McpTool("get_issue", "One issue with source/target text, history and planned actions.", IssueArgs, get_issue),
    McpTool("run_summary", "Status and stage progress of a run.", RunArgs, run_summary),
    McpTool(
        "propose_term", "Record a preferred rendering for a term (translation prompts follow it; reversible).", ProposeTermArgs,
        lambda s, a: book_tools.propose_term(_ctx(s, a.document_id), _without(a, book_tools.TermArgs)), writes=True,
    ),
    McpTool(
        "record_decision", "Record a book-level decision in BOOK.md (reversible; later decisions supersede).", RecordDecisionArgs,
        lambda s, a: book_tools.record_decision(_ctx(s, a.document_id), _without(a, book_tools.RecordDecisionArgs)), writes=True,
    ),
    McpTool("triage_issue", "Mark an issue as triaged with a note.", TriageIssueArgs, triage_issue, writes=True),
)
_BY_NAME = {tool.name: tool for tool in TOOLS}


class McpServer:
    def __init__(self, session_factory: sessionmaker) -> None:
        self.session_factory = session_factory

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        request_id = message.get("id")
        if request_id is None:  # notification
            return None
        try:
            if method == "initialize":
                result: Any = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER_INFO}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [tool.schema() for tool in TOOLS]}
            elif method == "tools/call":
                result = self._call(message.get("params") or {})
            else:
                return _error(request_id, -32601, f"method not found: {method}")
        except Exception as exc:  # protocol-level failure
            return _error(request_id, -32603, f"{type(exc).__name__}: {exc}")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _call(self, params: dict[str, Any]) -> dict[str, Any]:
        tool = _BY_NAME.get(str(params.get("name")))
        if tool is None:
            return _tool_error(f"unknown tool: {params.get('name')}")
        try:
            arguments = tool.input_model.model_validate(params.get("arguments") or {})
        except ValidationError as exc:
            return _tool_error(f"invalid arguments: {exc.errors(include_url=False)}")
        session = self.session_factory()
        try:
            output = tool.handler(session, arguments)
            if tool.writes:
                session.commit()
            else:
                session.rollback()
        except ToolError as exc:
            session.rollback()
            return _tool_error(str(exc))
        except Exception as exc:
            session.rollback()
            return _tool_error(f"{type(exc).__name__}: {str(exc)[:500]}")
        finally:
            session.close()
        text = json.dumps(output, ensure_ascii=False, default=str)
        return {"content": [{"type": "text", "text": text}], "structuredContent": output if isinstance(output, dict) else {"result": output}, "isError": False}

    def serve(self, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                response: dict[str, Any] | None = _error(None, -32700, "parse error")
            else:
                response = self.handle(message) if isinstance(message, dict) else _error(None, -32600, "invalid request")
            if response is not None:
                stdout.write(json.dumps(response, ensure_ascii=False, default=str) + "\n")
                stdout.flush()


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}
