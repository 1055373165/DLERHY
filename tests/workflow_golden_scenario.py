"""A deterministic document workflow scenario for golden snapshots.

Drives ``DocumentWorkflowService`` through bootstrap, translation, memory
proposal decisions, review, a blocked export, an issue action, worklist
assignment and a successful export, then captures every read model the API
serves. ``normalize`` replaces ids, timestamps, temp paths and wall-clock
derived numbers with placeholders so snapshots compare across runs.
"""

from __future__ import annotations

import dataclasses
import re
import zipfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select

import tests.test_api_workflow as api_fixtures
from book_agent.domain.enums import ExportType, RootCauseLayer
from book_agent.domain.models import Document, Sentence
from book_agent.domain.models.review import IssueAction, ReviewIssue
from book_agent.domain.models.translation import AlignmentEdge
from book_agent.infra.db.session import session_scope
from book_agent.services.export import ExportGateError
from book_agent.services.workflows import DocumentWorkflowService

SECOND_CHAPTER_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch2">Chapter Two</h1>
    <p>Moats protect margins. Distribution beats product. Agents need memory.</p>
    <p>Teams that ship weekly learn faster than teams that plan quarterly.</p>
  </body>
</html>
"""

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\b[0-9a-f]{32}\b")
_ISO_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
# Numbers derived from "now" or from how long a call took.
_VOLATILE_KEY = re.compile(r"(^|_)(age|latency|duration|elapsed|seconds|minutes|hours|days)(_|$)")


def write_epub(root: Path) -> Path:
    chapters = [("Chapter One", "chapter1.xhtml"), ("Chapter Two", "chapter2.xhtml")]
    epub_path = root / "golden.epub"
    with zipfile.ZipFile(epub_path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", api_fixtures.CONTAINER_XML)
        archive.writestr("OEBPS/content.opf", api_fixtures._content_opf_with_chapters(chapters))
        archive.writestr("OEBPS/nav.xhtml", api_fixtures._nav_xhtml_with_chapters(chapters))
        archive.writestr("OEBPS/chapter1.xhtml", api_fixtures.CHAPTER_XHTML)
        archive.writestr("OEBPS/chapter2.xhtml", SECOND_CHAPTER_XHTML)
    return epub_path


def run_scenario(session_factory, root: Path) -> dict[str, Any]:
    export_root = str(root / "exports")
    captured: dict[str, Any] = {}

    def workflow(session) -> DocumentWorkflowService:
        return DocumentWorkflowService(session, export_root=export_root)

    with session_scope(session_factory) as session:
        document_id = workflow(session).bootstrap_document(write_epub(root)).document_id
    with session_scope(session_factory) as session:
        captured["translate"] = workflow(session).translate_document(document_id)

    with session_scope(session_factory) as session:
        summary = workflow(session).get_document_summary(document_id)
        chapter_ids = [chapter.chapter_id for chapter in summary.chapters]
    with session_scope(session_factory) as session:
        service = workflow(session)
        proposals = service.list_chapter_memory_proposals(document_id, chapter_ids[0])
        captured["proposals_before_decision"] = proposals
        captured["proposal_approval"] = service.approve_chapter_memory_proposal(
            document_id, chapter_ids[0], proposals[0].proposal_id, actor_name="golden-reviewer", note="looks right"
        )
    with session_scope(session_factory) as session:
        service = workflow(session)
        second_proposals = service.list_chapter_memory_proposals(document_id, chapter_ids[1], status="proposed")
        captured["proposal_rejection"] = service.reject_chapter_memory_proposal(
            document_id, chapter_ids[1], second_proposals[0].proposal_id, actor_name="golden-reviewer"
        )

    with session_scope(session_factory) as session:
        captured["review"] = workflow(session).review_document(document_id)
    with session_scope(session_factory) as session:
        captured["review_package_export"] = workflow(session).export_document(document_id, ExportType.REVIEW_PACKAGE)

    with session_scope(session_factory) as session:
        sentence_id = session.scalars(
            select(Sentence.id).where(Sentence.document_id == document_id, Sentence.source_text.like("Pricing power%"))
        ).one()
        session.execute(delete(AlignmentEdge).where(AlignmentEdge.sentence_id == sentence_id))
    with session_scope(session_factory) as session:
        try:
            workflow(session).export_document(document_id, ExportType.BILINGUAL_HTML)
        except ExportGateError as exc:
            captured["blocked_export"] = exc.to_http_detail()
        else:
            raise AssertionError("bilingual export should be blocked by the missing alignment")
    with session_scope(session_factory) as session:
        issue = session.scalars(
            select(ReviewIssue).where(
                ReviewIssue.document_id == document_id,
                ReviewIssue.issue_type == "ALIGNMENT_FAILURE",
                ReviewIssue.root_cause_layer == RootCauseLayer.EXPORT,
            )
        ).one()
        # Backdate into the previous UTC day (and at least 5h old, so SLAs are breached): the
        # issue-activity timeline buckets by day, so "now - 5h" changed shape with the time of day.
        start_of_today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        issue.created_at = start_of_today - timedelta(hours=5)
        issue_chapter_id = issue.chapter_id
        action_id = session.scalars(select(IssueAction.id).where(IssueAction.issue_id == issue.id)).first()

    with session_scope(session_factory) as session:
        service = workflow(session)
        captured["worklist_before_assignment"] = service.get_document_chapter_worklist(document_id)
        captured["assignment"] = service.assign_document_chapter_worklist_owner(
            document_id, issue_chapter_id, owner_name="golden-owner", assigned_by="golden-lead", note="take this"
        )
    with session_scope(session_factory) as session:
        service = workflow(session)
        captured["worklist_filtered"] = service.get_document_chapter_worklist(
            document_id, queue_priority="immediate", sla_status="breached", assigned=True, limit=1
        )
        captured["worklist_detail_blocked"] = service.get_document_chapter_worklist_detail(document_id, issue_chapter_id)

    with session_scope(session_factory) as session:
        captured["action"] = workflow(session).execute_action(action_id, run_followup=True)
    with session_scope(session_factory) as session:
        captured["bilingual_export"] = workflow(session).export_document(
            document_id, ExportType.BILINGUAL_HTML, auto_execute_followup_on_gate=True
        )
    with session_scope(session_factory) as session:
        captured["merged_export"] = workflow(session).export_document(document_id, ExportType.MERGED_HTML)
    with session_scope(session_factory) as session:
        service = workflow(session)
        captured["assignment_cleared"] = service.clear_document_chapter_worklist_owner(
            document_id, issue_chapter_id, cleared_by="golden-lead"
        )

    with session_scope(session_factory) as session:
        service = workflow(session)
        captured["summary"] = service.get_document_summary(document_id)
        captured["history"] = service.list_document_history(limit=10)
        captured["history_filtered"] = service.list_document_history(query="strategy", merged_export_ready=True)
        dashboard = service.get_document_export_dashboard(document_id)
        captured["export_dashboard"] = dashboard
        captured["export_dashboard_filtered"] = service.get_document_export_dashboard(
            document_id, export_type=ExportType.BILINGUAL_HTML, limit=1
        )
        captured["export_detail"] = service.get_document_export_detail(document_id, dashboard.records[0].export_id)
        captured["worklist_final"] = service.get_document_chapter_worklist(document_id)
        captured["worklist_details_final"] = [
            service.get_document_chapter_worklist_detail(document_id, chapter_id) for chapter_id in chapter_ids
        ]
        captured["proposals_final"] = [
            service.list_chapter_memory_proposals(document_id, chapter_id) for chapter_id in chapter_ids
        ]
        captured["document_title"] = session.get(Document, document_id).title
    return captured


def normalize(value: Any, *, root: Path) -> Any:
    ids: dict[str, str] = {}
    root_text = str(root)
    resolved_root_text = str(root.resolve())

    def _string(text: str) -> str:
        text = text.replace(resolved_root_text, "<root>").replace(root_text, "<root>")
        text = _ISO_TIMESTAMP.sub("<ts>", text)
        text = _DATE.sub("<date>", text)
        return _UUID.sub(lambda match: ids.setdefault(match.group(0), f"<id{len(ids) + 1}>"), text)

    def _walk(item: Any, key: str = "") -> Any:
        if dataclasses.is_dataclass(item) and not isinstance(item, type):
            return {field.name: _walk(getattr(item, field.name), field.name) for field in dataclasses.fields(item)}
        if isinstance(item, dict):
            return {_string(str(k)): _walk(v, str(k)) for k, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [_walk(v, key) for v in item]
        table = getattr(type(item), "__table__", None)
        if table is not None:
            return {column.key: _walk(getattr(item, column.key), column.key) for column in table.columns}
        if isinstance(item, Enum):
            return item.value
        if isinstance(item, (datetime, date)):
            return "<ts>"
        if isinstance(item, Path):
            return _string(str(item))
        if isinstance(item, bool) or item is None:
            return item
        if isinstance(item, (int, float, Decimal)):
            if _VOLATILE_KEY.search(key):
                return "<volatile>"
            return round(float(item), 6) if isinstance(item, (float, Decimal)) else item
        if isinstance(item, str):
            return _string(item)
        return _string(repr(item))

    return _walk(value)
