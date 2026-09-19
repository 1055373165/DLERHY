"""Export QA Agent: a model looks at rendered exports and files what the rule checks cannot see.

Runs as the opt-in ``export_review`` stage after the HTML exports. The queue
is the run document's exported HTML artifacts (bilingual chapters and the
merged document); sampled mode takes the artifacts with the most open
rule-based QA findings first, up to ``SAMPLED_ARTIFACT_LIMIT``.

For each artifact the model gets the deterministic QA report (the
``*.qa.json`` next to the file) and a text excerpt, can look at screenshots
of the rendered page, and files non-blocking ``EXPORT_QA_REVIEW`` issues
(detector=model): missing or broken images, broken tables, untranslated or
garbled text, overlapping layout, wrong heading levels. Nothing is edited.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.core.ids import stable_id
from book_agent.domain.enums import (
    AgentItemKind,
    Detector,
    ExportStatus,
    ExportType,
    IssueStatus,
    RootCauseLayer,
    Severity,
)
from book_agent.domain.models import Document
from book_agent.domain.models.review import Export, ReviewIssue
from book_agent.harness.kernel.budget import TurnBudget
from book_agent.harness.kernel.messages import IMAGE_OUTPUT_KEY
from book_agent.harness.tools.permissions import PermissionPolicy
from book_agent.harness.tools.registry import ToolContext, ToolError, ToolPermission, ToolRegistry, ToolSpec
from book_agent.infra.repositories.agent import AgentLedgerRepository
from book_agent.infra.repositories.review import ReviewRepository
from book_agent.orchestrator.rule_engine import build_issue_action

AGENT_KIND = "export_review"
ISSUE_TYPE = "EXPORT_QA_REVIEW"
MODE_SAMPLED = "sampled"
MODE_FULL = "full"
MODE_SKIP = "skip"
SAMPLED_ARTIFACT_LIMIT = 5
QUEUE_KEY = "export_review_queue"
EXCERPT_CHARS = 4000
VIEWPORT = (1200, 1600)
MAX_SCREEN = 30

SYSTEM_PROMPT = """You are the final reader of a translated book export (Chinese HTML) before it is delivered.
For each artifact from next_export_artifact: read the rule-based QA report and the text excerpt, look at
screenshots with render_export_screenshot (screen 0 is the top; take a few screens across long pages), and
file report_export_problem for problems a reader would notice:
- missing_image (placeholder or broken image), broken_table (collapsed or misaligned cells),
- untranslated_text (English left in the Chinese reading text, excluding code, URLs and names),
- garbled_text (mojibake, duplicated or truncated passages), layout_overlap (text overlapping or cut off),
- wrong_heading_level (hierarchy that does not match the book), other.
Quote the visible text involved. Do not repeat problems the rule report already lists. Call finish_export_artifact
after each artifact. When next_export_artifact says done, reply with a short Chinese summary and no tool calls."""

ProblemKind = Literal[
    "missing_image", "broken_table", "untranslated_text", "garbled_text", "layout_overlap", "wrong_heading_level", "other"
]

_AUDITED_TYPES = (ExportType.BILINGUAL_HTML, ExportType.MERGED_HTML)


def export_artifacts(session: Session, document_id: str, *, mode: str) -> list[dict[str, Any]]:
    if mode == MODE_SKIP:
        return []
    exports = session.scalars(
        select(Export)
        .where(Export.document_id == document_id, Export.export_type.in_(_AUDITED_TYPES), Export.status == ExportStatus.SUCCEEDED)
        .order_by(Export.export_type, Export.file_path)
    ).all()
    open_findings: dict[str, int] = {}
    for issue in session.scalars(
        select(ReviewIssue).where(
            ReviewIssue.document_id == document_id,
            ReviewIssue.issue_type == "EXPORT_QA_FAILURE",
            ReviewIssue.status.in_([IssueStatus.OPEN, IssueStatus.TRIAGED]),
        )
    ).all():
        path = str((issue.evidence_json or {}).get("html_path") or "")
        open_findings[path] = open_findings.get(path, 0) + 1
    artifacts = [
        {
            "export_id": export.id,
            "export_type": export.export_type.value,
            "chapter_id": (export.input_version_bundle_json or {}).get("chapter_id"),
            "file_path": export.file_path,
            "open_rule_findings": open_findings.get(export.file_path, 0),
        }
        for export in exports
        if Path(export.file_path).is_file()
    ]
    if mode != MODE_FULL:
        artifacts = sorted(artifacts, key=lambda item: (-item["open_rule_findings"], item["export_type"] != "merged_html", item["file_path"]))[
            :SAMPLED_ARTIFACT_LIMIT
        ]
    return artifacts


def _turn_queue(ctx: ToolContext) -> list[dict[str, Any]]:
    for item in AgentLedgerRepository(ctx.session).list_items(ctx.turn_id):
        if item.kind == AgentItemKind.USER and QUEUE_KEY in (item.content_json or {}):
            return list(item.content_json[QUEUE_KEY])
    return []


def _finished(ctx: ToolContext) -> set[str]:
    finished: set[str] = set()
    for item in AgentLedgerRepository(ctx.session).list_items(ctx.turn_id):
        content = item.content_json or {}
        if item.kind == AgentItemKind.TOOL_RESULT and content.get("name") == "finish_export_artifact":
            result = content.get("result") or {}
            if result.get("ok"):
                finished.add(str((result.get("output") or {}).get("export_id")))
    return finished


def _queued(ctx: ToolContext, export_id: str) -> dict[str, Any]:
    for artifact in _turn_queue(ctx):
        if artifact["export_id"] == export_id:
            return artifact
    raise ToolError(f"export {export_id} is not in this review queue")


def _text_excerpt(html: str) -> str:
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", body)
    return re.sub(r"\s+", " ", text).strip()[:EXCERPT_CHARS]


class NextExportArtifactArgs(BaseModel):
    pass


class RenderExportScreenshotArgs(BaseModel):
    export_id: str
    screen: int = Field(default=0, ge=0, le=MAX_SCREEN, description="0 is the top of the page; each screen is one viewport lower.")


class ReportExportProblemArgs(BaseModel):
    export_id: str
    kind: ProblemKind
    visible_text: str = Field(default="", description="Text visible where the problem is, quoted.")
    description: str = Field(min_length=1, description="What is wrong, in Chinese.")
    severity: Literal["low", "medium", "high"] = "medium"
    confidence: float = Field(ge=0.0, le=1.0)
    screen: int | None = Field(default=None, ge=0, le=MAX_SCREEN)


class FinishExportArtifactArgs(BaseModel):
    export_id: str


def next_export_artifact(ctx: ToolContext, args: NextExportArtifactArgs) -> dict[str, Any]:
    queue = _turn_queue(ctx)
    finished = _finished(ctx)
    remaining = [artifact for artifact in queue if artifact["export_id"] not in finished]
    if not remaining:
        return {"done": True, "reviewed": len(queue)}
    artifact = remaining[0]
    path = Path(artifact["file_path"])
    if not path.is_file():
        raise ToolError(f"export file is gone: {path.name}")
    report_path = path.with_name(path.stem + ".qa.json")
    rule_report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else None
    return {
        "done": False,
        **artifact,
        "remaining_after_this": len(remaining) - 1,
        "rule_qa_failed_checks": [check for check in (rule_report or {}).get("checks", []) if not check.get("ok")],
        "text_excerpt": _text_excerpt(path.read_text(encoding="utf-8")),
    }


def render_export_screenshot(ctx: ToolContext, args: RenderExportScreenshotArgs) -> dict[str, Any]:
    artifact = _queued(ctx, args.export_id)
    renderer = ctx.extras.get("html_screenshotter") or screenshot_html
    png = renderer(Path(artifact["file_path"]), args.screen)
    return {
        "export_id": args.export_id,
        "screen": args.screen,
        "note": "The screenshot is attached below.",
        IMAGE_OUTPUT_KEY: [{"media_type": "image/png", "data": base64.b64encode(png).decode("ascii")}],
    }


def screenshot_html(path: Path, screen: int) -> bytes:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ToolError("screenshots are unavailable: Playwright is not installed") from exc
    width, height = VIEWPORT
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.goto(path.resolve().as_uri(), wait_until="load")
            page.evaluate(f"window.scrollTo(0, {screen * height})")
            return page.screenshot()
        finally:
            browser.close()


def report_export_problem(ctx: ToolContext, args: ReportExportProblemArgs) -> dict[str, Any]:
    artifact = _queued(ctx, args.export_id)
    issue = ReviewIssue(
        id=stable_id("review-issue", ctx.document_id, args.export_id, ISSUE_TYPE, args.kind, args.visible_text[:120]),
        document_id=ctx.document_id,
        chapter_id=artifact.get("chapter_id"),
        issue_type=ISSUE_TYPE,
        root_cause_layer=RootCauseLayer.EXPORT,
        severity=Severity(args.severity),
        blocking=False,
        detector=Detector.MODEL,
        confidence=round(float(args.confidence), 3),
        evidence_json={
            "reason": "export_review",
            "export_id": args.export_id,
            "export_type": artifact["export_type"],
            "html_path": artifact["file_path"],
            "kind": args.kind,
            "visible_text": args.visible_text,
            "description": args.description,
            "screen": args.screen,
            "turn_id": ctx.turn_id,
        },
        status=IssueStatus.OPEN,
    )
    sync = ReviewRepository(ctx.session).sync_issues(
        [issue], [build_issue_action(issue)], owned_existing=[], resolution_note="", actor_id=f"agent:{AGENT_KIND}"
    )
    persisted = sync.issues[0]
    return {"issue_id": persisted.id, "status": persisted.status.value}


def finish_export_artifact(ctx: ToolContext, args: FinishExportArtifactArgs) -> dict[str, Any]:
    _queued(ctx, args.export_id)
    return {"export_id": args.export_id}


def export_review_tool_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            ToolSpec("next_export_artifact", "The next exported HTML to review: rule QA findings and a text excerpt, or done.", NextExportArtifactArgs, ToolPermission.READ, next_export_artifact),
            ToolSpec("render_export_screenshot", "Screenshot one screen of the rendered export and attach it.", RenderExportScreenshotArgs, ToolPermission.READ, render_export_screenshot, max_calls_per_turn=200),
            ToolSpec("report_export_problem", "File one problem a reader would notice (non-blocking).", ReportExportProblemArgs, ToolPermission.WRITE_REVERSIBLE, report_export_problem, max_calls_per_turn=300),
            ToolSpec("finish_export_artifact", "Mark an artifact as reviewed.", FinishExportArtifactArgs, ToolPermission.WRITE_REVERSIBLE, finish_export_artifact, max_calls_per_turn=300),
        ]
    )


@dataclass(slots=True)
class ExportReviewTurnSeed:
    turn_id: str
    queued_artifacts: int


class ExportReviewAgent:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.ledger = AgentLedgerRepository(session)

    @staticmethod
    def registry() -> ToolRegistry:
        return export_review_tool_registry()

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
        budget: TurnBudget | None = None,
    ) -> ExportReviewTurnSeed:
        document = self.session.get(Document, document_id)
        if document is None:
            raise ValueError(f"document not found: {document_id}")
        queue = export_artifacts(self.session, document_id, mode=mode)
        turn = self.ledger.create_turn(
            document_id=document_id,
            agent_kind=AGENT_KIND,
            scope_type="document",
            scope_id=document_id,
            run_id=run_id,
            work_item_id=work_item_id,
            model_name=model_name,
            budget=(budget or TurnBudget(max_steps=len(queue) * 8 + 10, max_tool_calls=len(queue) * 12 + 20)).to_json(),
            skills=["export-qa/default"],
        )
        self.ledger.append_item(turn.id, kind=AgentItemKind.SYSTEM, content={"text": SYSTEM_PROMPT})
        brief = (
            f"Book: {document.title_src or document.title or '(untitled)'}. Exported artifacts to review: {len(queue)}. "
            + ("Start with next_export_artifact." if queue else "Nothing to review; reply with a one-line summary.")
        )
        self.ledger.append_item(turn.id, kind=AgentItemKind.USER, content={"text": brief, QUEUE_KEY: queue})
        self.session.flush()
        return ExportReviewTurnSeed(turn_id=turn.id, queued_artifacts=len(queue))
