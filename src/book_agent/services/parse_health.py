"""Run the parse health checks on a stored document and keep the report.

Reads what the parser already stored (chapters, active blocks); for a PDF it also opens the file
with PyMuPDF for the outline and, per figure, the page the figure region is checked against. No
model is called. Runs after bootstrap and after each structure refresh (services/workflows.py).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.domain.enums import ArtifactStatus, SourceType
from book_agent.domain.models import Block, Chapter, Document, ParseHealthReport
from book_agent.domain.structure import parse_health
from book_agent.domain.structure.parse_health import (
    BLOCKING,
    CheckResult,
    HealthBlock,
    HealthChapter,
    OutlineEntry,
)

logger = logging.getLogger(__name__)

PDF_SOURCE_TYPES = frozenset({SourceType.PDF_TEXT, SourceType.PDF_MIXED, SourceType.PDF_SCAN})


def load_document_inputs(session: Session, document_id: str) -> tuple[Document, list[HealthChapter], list[HealthBlock]]:
    document = session.get(Document, document_id)
    if document is None:
        raise LookupError(f"document not found: {document_id}")
    chapter_rows = session.scalars(
        select(Chapter).where(Chapter.document_id == document_id).order_by(Chapter.ordinal)
    ).all()
    chapters = [
        HealthChapter(
            id=chapter.id,
            ordinal=chapter.ordinal,
            title=chapter.title_src or "",
            page_start=_int((chapter.metadata_json or {}).get("source_page_start")),
            page_end=_int((chapter.metadata_json or {}).get("source_page_end")),
        )
        for chapter in chapter_rows
    ]
    chapter_order = {chapter.id: chapter.ordinal for chapter in chapter_rows}
    block_rows = session.scalars(
        select(Block).where(Block.chapter_id.in_(list(chapter_order)), Block.status == ArtifactStatus.ACTIVE)
    ).all()
    blocks = [
        HealthBlock(
            id=block.id,
            chapter_id=block.chapter_id,
            ordinal=block.ordinal,
            block_type=block.block_type.value if hasattr(block.block_type, "value") else str(block.block_type),
            source_text=block.source_text or "",
            source_span_json=dict(block.source_span_json or {}),
        )
        for block in sorted(block_rows, key=lambda row: (chapter_order.get(row.chapter_id, 0), row.ordinal))
    ]
    return document, chapters, blocks


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, int) else None


class _PdfPages:
    """The source PDF, opened once: its outline, page sizes and ``figure_region_problems`` per page.

    What it reads is remembered: the PDF does not change while it is open, and a repair judges every
    attempt with a QA pass that reads the same tables and figures again (2–5 s a pass on a book).
    """

    def __init__(self, path: Path):
        import fitz

        self.document = fitz.open(path)
        self._outline: list[OutlineEntry] | None = None
        self._page_sizes: dict[int, tuple[float, float]] | None = None
        self._table_rows: dict[tuple[Any, ...], list[list[str]] | None] = {}
        self._figure_problems: dict[tuple[Any, ...], list[Any]] = {}

    def close(self) -> None:
        self.document.close()

    def outline(self) -> list[OutlineEntry]:
        if self._outline is None:
            self._outline = [
                OutlineEntry(level=int(level), title=str(title), page=int(page))
                for level, title, page, *_rest in self.document.get_toc(simple=True)
                if int(page) >= 1
            ]
        return list(self._outline)

    def inline_word_counts(self) -> Counter[str]:
        """The words each PDF line holds whole: the evidence for how the book writes a hyphenated word."""
        from book_agent.ingestion.pdf.hyphenation import inline_word_counts

        return inline_word_counts(line for page in self.document for line in page.get_text("text").splitlines())

    def page_sizes(self) -> dict[int, tuple[float, float]]:
        if self._page_sizes is None:
            self._page_sizes = {
                index + 1: (float(page.rect.width), float(page.rect.height)) for index, page in enumerate(self.document)
            }
        return dict(self._page_sizes)

    def table_rows(self, page_number: int, bbox: Sequence[float]) -> list[list[str]] | None:
        """The ruled table at ``bbox`` read again with the current extractor (None: no data table there)."""
        from book_agent.ingestion.pdf.extract import ruled_table_rows_at

        if not 1 <= page_number <= self.document.page_count:
            return None
        key = (page_number, tuple(bbox))
        if key not in self._table_rows:
            self._table_rows[key] = ruled_table_rows_at(self.document[page_number - 1], bbox)
        rows = self._table_rows[key]
        return [list(row) for row in rows] if rows is not None else None

    def figure_problems(self, page_number: int, bbox: Sequence[float], furniture: list[Sequence[float]]) -> list[Any]:
        from book_agent.ingestion.pdf.figure_extent import figure_region_problems

        if not 1 <= page_number <= self.document.page_count:
            return []
        furniture_bboxes = [tuple(value) for value in furniture]
        key = (page_number, tuple(bbox), tuple(furniture_bboxes))
        if key not in self._figure_problems:
            page = self.document[page_number - 1]
            self._figure_problems[key] = figure_region_problems(page, bbox, furniture_bboxes=furniture_bboxes)
        return list(self._figure_problems[key])


def _is_academic_paper(document: Document) -> bool:
    profile = (document.metadata_json or {}).get("pdf_profile") or {}
    return isinstance(profile, dict) and str(profile.get("recovery_lane") or "") == "academic_paper"


def compute_checks(session: Session, document_id: str) -> tuple[Document, list[CheckResult]]:
    document, chapters, blocks = load_document_inputs(session, document_id)
    is_pdf = document.source_type in PDF_SOURCE_TYPES
    pages: _PdfPages | None = None
    if is_pdf and document.source_path and Path(document.source_path).exists():
        try:
            pages = _PdfPages(Path(document.source_path))
        except Exception:  # pragma: no cover - an unreadable file is reported by the missing checks
            logger.warning("parse health: cannot open %s", document.source_path, exc_info=True)
    try:
        checks = parse_health.run_checks(
            is_pdf=is_pdf,
            chapters=chapters,
            blocks=blocks,
            outline=pages.outline() if pages else (),
            document_title=document.title_src or document.title,
            page_sizes=pages.page_sizes() if pages else None,
            find_figure_problems=pages.figure_problems if pages else None,
            inline_counts=pages.inline_word_counts() if pages else None,
            academic_paper=_is_academic_paper(document),
        )
    finally:
        if pages is not None:
            pages.close()
    return document, checks


class ParseHealthService:
    def __init__(self, session: Session):
        self.session = session

    def run(self, document_id: str, *, trigger: str = "manual") -> ParseHealthReport:
        started = time.monotonic()
        document, checks = compute_checks(self.session, document_id)
        summary = parse_health.summarize(checks)
        parse_ir = (document.metadata_json or {}).get("parse_ir") or {}
        report = ParseHealthReport(
            document_id=document.id,
            parse_revision_id=parse_ir.get("revision_id") if isinstance(parse_ir, dict) else None,
            trigger=trigger,
            status=summary["status"],
            blocking_failed=summary["failed"]["blocking"],
            major_failed=summary["failed"]["major"],
            minor_failed=summary["failed"]["minor"],
            checks_json=[check.to_dict() for check in checks],
            duration_ms=int((time.monotonic() - started) * 1000),
            # Set here, not by the database: SQLite's now() has whole seconds, and "latest" must be exact.
            created_at=datetime.now(timezone.utc),
        )
        self.session.add(report)
        self.session.flush()
        return report

    def run_quietly(self, document_id: str, *, trigger: str) -> ParseHealthReport | None:
        """Run after an import or refresh: a failing check run must not fail the import."""
        try:
            with self.session.begin_nested():
                return self.run(document_id, trigger=trigger)
        except Exception:
            logger.exception("parse health check failed for document %s", document_id)
            return None

    def latest(self, document_id: str) -> ParseHealthReport | None:
        return latest_report(self.session, document_id)


def latest_report(session: Session, document_id: str) -> ParseHealthReport | None:
    return session.scalars(
        select(ParseHealthReport)
        .where(ParseHealthReport.document_id == document_id)
        .order_by(ParseHealthReport.created_at.desc(), ParseHealthReport.id.desc())
        .limit(1)
    ).first()


def report_payload(report: ParseHealthReport) -> dict[str, Any]:
    return {
        "report_id": report.id,
        "document_id": report.document_id,
        "parse_revision_id": report.parse_revision_id,
        "trigger": report.trigger,
        "status": report.status,
        "failed": {"blocking": report.blocking_failed, "major": report.major_failed, "minor": report.minor_failed},
        "checks": list(report.checks_json or []),
        "duration_ms": report.duration_ms,
        "checked_at": report.created_at.isoformat() if report.created_at else None,
    }


def overview_summary(report: ParseHealthReport | None) -> dict[str, Any] | None:
    """What the book page says about the parse: the status and the checks that failed, worst first."""
    if report is None:
        return None
    order = {severity: index for index, severity in enumerate(parse_health.SEVERITY_ORDER)}
    failed = sorted(
        (check for check in report.checks_json or [] if not check.get("passed")),
        key=lambda check: (order.get(str(check.get("severity")), 9), -int(check.get("count") or 0)),
    )
    return {
        "status": report.status,
        "needs_attention": report.status == BLOCKING,
        "checked_at": report.created_at.isoformat() if report.created_at else None,
        "failed": [
            {"name": check.get("name"), "severity": check.get("severity"), "count": check.get("count"), "allowed": check.get("allowed")}
            for check in failed
        ],
        "check_count": len(report.checks_json or []),
    }
