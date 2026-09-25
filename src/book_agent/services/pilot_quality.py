"""Run the pilot's layered QA (L1–L7) on its chapters and keep the report.

docs/agent-upgrade/08-pilot-quality-gate.md §6. The pilot run's ``pilot_qa`` stage calls this after
its exports; ``POST /v1/documents/{id}/pilot-quality`` runs it again by hand. The rules live in
``domain/quality/pilot_qa.py``; this module loads what they read:

* the reading copy's render blocks and chapter sections, the same the merged HTML export renders
  (``ExportService.reading_copy_view``), each block's HTML, and whether each figure has a picture;
* the exported reading copy itself when there is one (its chapter sections and pictures are what
  the reader actually opens), else the sections the export would write;
* the pilot chapters' sentences with their current translation and packet;
* the glossary (locked and preferred terms) and the Reviewer Agent's open findings (the pilot's
  model review; no new model call is made here);
* parse health's inputs for the pilot chapters' blocks, with the PDF pages for figure crops.

A QA run never fails the pilot: an error is kept as a report with status "error" and the pilot
goes to the reader with its other results (the product decision: blocking issues are listed, not
hidden).
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import unquote

from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.domain.enums import (
    Detector,
    ExportStatus,
    ExportType,
    IssueStatus,
    PacketSentenceRole,
    LockLevel,
    TermStatus,
)
from book_agent.domain.models import Chapter, Document, Sentence
from book_agent.domain.models.ops import DocumentRun
from book_agent.domain.models.review import Export, PilotQualityReport, ReviewIssue
from book_agent.domain.models.translation import PacketSentenceMap, TermEntry
from book_agent.domain.quality import pilot_qa
from book_agent.domain.quality.pilot_qa import (
    ExportedChapter,
    ExtraCheck,
    GlossaryTerm,
    HealthInputs,
    PilotQaInputs,
    QaBlock,
    QaChapter,
    QaSentence,
    ReviewFinding,
)
from book_agent.export import markup, pdf_crop
from book_agent.infra.repositories.export import ExportRepository
from book_agent.infra.repositories.review import active_target_texts
from book_agent.orchestrator.run_plan import CHAPTER_IDS_REQUEST_KEY, RUN_REQUEST_KEY
from book_agent.services.parse_health import PDF_SOURCE_TYPES, _is_academic_paper, _PdfPages, load_document_inputs
from book_agent.services.pilot import latest_pilot_run, select_pilot_chapters

logger = logging.getLogger(__name__)

# Render modes whose sentences the reader gets in Chinese.
_TRANSLATED_RENDER_MODES = frozenset(
    {
        "zh_primary_with_optional_source",
        "image_anchor_with_translated_caption",
        "translated_wrapper_with_preserved_artifact",
        "reference_preserve_with_translated_label",
    }
)


class NoPilotChapters(LookupError):
    pass


def pilot_chapter_ids(session: Session, document_id: str, run: DocumentRun | None = None) -> list[str]:
    """The chapters a pilot run translated, else the chapters a pilot would take."""
    run = run or latest_pilot_run(session, document_id)
    if run is not None:
        request = (run.status_detail_json or {}).get(RUN_REQUEST_KEY) or {}
        chapter_ids = [str(chapter_id) for chapter_id in request.get(CHAPTER_IDS_REQUEST_KEY) or []]
        if chapter_ids:
            return chapter_ids
    selection = select_pilot_chapters(session, document_id)
    return selection.chapter_ids


# ---------------------------------------------------------------------------
# The exported reading copy

_CHAPTER_SECTION_RE = re.compile(r"<section class='chapter' id='chapter-([^']+)'>")
_H2_RE = re.compile(r"<div class='chapter-head'><h2>(.*?)</h2>", re.DOTALL)
_IMAGE_ANCHOR_RE = re.compile(r"<section class='block artifact image-anchor[^']*'>(.*?)</section>", re.DOTALL)
_ZH_RE = re.compile(r"<div class='zh'>(.*?)</div>", re.DOTALL)


def exported_chapters(html_text: str) -> list[ExportedChapter]:
    """The chapter sections of a merged HTML export, with their titles."""
    starts = list(_CHAPTER_SECTION_RE.finditer(html_text))
    chapters = []
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(html_text)
        body = html_text[match.start() : end]
        title = _H2_RE.search(body)
        chapters.append(
            ExportedChapter(
                chapter_id=match.group(1),
                title=pilot_qa.visible_text(title.group(1)) if title else "",
                html=body,
            )
        )
    return chapters


_IMG_SRC_RE = re.compile(r"<img [^>]*src='([^']+)'")
_BLOCK_ASSET_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.\w+$")


def _picture_shown(body: str, base_dir: Path) -> bool:
    """An <img> whose file is next to the export: a reader sees a picture, not a broken image."""
    source = _IMG_SRC_RE.search(body)
    if source is None:
        return False
    src = unquote(source.group(1))
    return src.startswith(("http://", "https://", "data:")) or (base_dir / src).is_file()


def _figure_pictures(section_html: str, base_dir: Path) -> list[tuple[str, bool]]:
    """(Chinese caption, picture shown) for each figure of an exported chapter, in reading order."""
    figures = []
    for body in _IMAGE_ANCHOR_RE.findall(section_html):
        caption = _ZH_RE.search(body)
        figures.append((_caption_key(pilot_qa.visible_text(caption.group(1)) if caption else ""), _picture_shown(body, base_dir)))
    return figures


def _pictures_by_block(html_text: str, base_dir: Path) -> dict[str, bool]:
    """PDF figure assets are named after their block: block id -> picture shown."""
    shown: dict[str, bool] = {}
    for match in _IMG_SRC_RE.finditer(html_text):
        src = unquote(match.group(1))
        block = _BLOCK_ASSET_RE.search(src)
        if block:
            shown[block.group(1)] = (base_dir / src).is_file()
    return shown


def _caption_key(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _latest_reading_copy(session: Session, document_id: str) -> Path | None:
    export = session.scalars(
        select(Export)
        .where(
            Export.document_id == document_id,
            Export.export_type == ExportType.MERGED_HTML,
            Export.status == ExportStatus.SUCCEEDED,
        )
        .order_by(Export.updated_at.desc(), Export.id.desc())
        .limit(1)
    ).first()
    if export is None or not export.file_path:
        return None
    path = Path(export.file_path)
    return path if path.is_file() else None


# ---------------------------------------------------------------------------
# Loading the pilot


def _has_picture(block: Any, document: Document, images_by_block: set[str]) -> bool:
    metadata = block.source_metadata or {}
    image_path = str(metadata.get("image_path") or "").strip()
    if image_path and Path(image_path).is_file():
        return True
    if block.block_id in images_by_block:
        return True
    if document.source_type in PDF_SOURCE_TYPES:
        return bool(document.source_path) and pdf_crop.pdf_asset_crop_spec(block) is not None
    return bool(str(metadata.get("image_src") or "").strip())


def _packet_by_sentence(session: Session, sentence_ids: Sequence[str]) -> dict[str, str]:
    packets: dict[str, str] = {}
    ids = list(dict.fromkeys(sentence_ids))
    for start in range(0, len(ids), 1000):
        rows = session.execute(
            select(PacketSentenceMap.sentence_id, PacketSentenceMap.packet_id).where(
                PacketSentenceMap.sentence_id.in_(ids[start : start + 1000]),
                PacketSentenceMap.role == PacketSentenceRole.CURRENT,
            )
        ).all()
        for sentence_id, packet_id in rows:
            packets.setdefault(str(sentence_id), str(packet_id))
    return packets


def _glossary(session: Session, document_id: str) -> list[GlossaryTerm]:
    rows = session.scalars(
        select(TermEntry).where(TermEntry.document_id == document_id, TermEntry.status == TermStatus.ACTIVE)
    ).all()
    terms = []
    for row in rows:
        renderings = [str(row.target_term or "").strip(), *[str(v).strip() for v in row.target_variants_json or []]]
        renderings = [rendering for rendering in dict.fromkeys(renderings) if rendering]
        if row.source_term and renderings:
            terms.append(GlossaryTerm(row.source_term, tuple(renderings), locked=row.lock_level == LockLevel.LOCKED))
    return terms


def _model_issues(session: Session, document_id: str, chapter_ids: Sequence[str]) -> list[ReviewFinding]:
    rows = session.scalars(
        select(ReviewIssue).where(
            ReviewIssue.document_id == document_id,
            ReviewIssue.chapter_id.in_(list(chapter_ids)),
            ReviewIssue.detector == Detector.MODEL,
            ReviewIssue.status.in_((IssueStatus.OPEN, IssueStatus.TRIAGED)),
        )
    ).all()
    sentence_ids = [row.sentence_id for row in rows if row.sentence_id]
    sources = dict(
        session.execute(select(Sentence.id, Sentence.source_text).where(Sentence.id.in_(sentence_ids))).all()
    ) if sentence_ids else {}
    targets = active_target_texts(session, sentence_ids)
    findings = []
    for row in rows:
        evidence = row.evidence_json or {}
        findings.append(
            ReviewFinding(
                issue_id=str(row.id),
                issue_type=str(row.issue_type),
                severity=row.severity.value if hasattr(row.severity, "value") else str(row.severity),
                chapter_id=row.chapter_id,
                block_id=row.block_id,
                sentence_id=row.sentence_id,
                packet_id=row.packet_id,
                source_text=str(sources.get(row.sentence_id) or evidence.get("source_text") or ""),
                target_text=str(targets.get(str(row.sentence_id)) or evidence.get("target_text") or ""),
                explanation=str(evidence.get("explanation") or evidence.get("reason") or ""),
                suggested_target_text=str(evidence.get("suggested_target_text") or ""),
            )
        )
    return findings


class _Loaded:
    """The pilot's inputs plus the open PDF behind the figure-crop check (closed after the run)."""

    def __init__(self, inputs: PilotQaInputs, export_source: str, pages: _PdfPages | None):
        self.inputs = inputs
        self.export_source = export_source
        self.pages = pages

    def close(self) -> None:
        if self.pages is not None:
            self.pages.close()


def load_pilot_inputs(
    session: Session, document_id: str, chapter_ids: Sequence[str], *, use_export_file: bool = True
) -> _Loaded:
    """``use_export_file=False`` judges what the export would write now (the repair loop's view,
    before it re-exports), not the reading copy last written to disk."""
    from book_agent.services.export import ExportService

    document = session.get(Document, document_id)
    if document is None:
        raise LookupError(f"document not found: {document_id}")
    chapter_ids = [str(chapter_id) for chapter_id in chapter_ids]
    service = ExportService(ExportRepository(session))
    bundle, render_blocks, sections = service.reading_copy_view(document_id, chapter_ids=chapter_ids)
    images_by_block = {
        str(image.block_id)
        for chapter_bundle in bundle.chapters
        for image in chapter_bundle.document_images
        if getattr(image, "block_id", None)
    }
    chapters = [
        QaChapter(
            id=chapter_bundle.chapter.id,
            ordinal=int(chapter_bundle.chapter.ordinal),
            title_src=chapter_bundle.chapter.title_src or "",
            title_tgt=chapter_bundle.chapter.title_tgt,
            page_start=(chapter_bundle.chapter.metadata_json or {}).get("source_page_start"),
            page_end=(chapter_bundle.chapter.metadata_json or {}).get("source_page_end"),
        )
        for chapter_bundle in bundle.chapters
    ]

    # Sentences: the current translation of each, and which packet translates it.
    sentences_by_id = {
        sentence.id: sentence
        for chapter_bundle in bundle.chapters
        for sentence in chapter_bundle.sentences
        if sentence.translatable and sentence.retired_by_revision_id is None
    }
    targets = active_target_texts(session, list(sentences_by_id))
    packets = _packet_by_sentence(session, list(sentences_by_id))

    # The exported reading copy: its sections, and which figures have a picture in it.
    reading_copy = _latest_reading_copy(session, document_id) if use_export_file else None
    exported: list[ExportedChapter] | None
    pictures_by_chapter: dict[str, list[tuple[str, bool]]] = {}
    pictures_by_block: dict[str, bool] = {}
    if reading_copy is not None:
        export_source = "file"
        reading_html = reading_copy.read_text(encoding="utf-8")
        exported = exported_chapters(reading_html)
        pictures_by_block = _pictures_by_block(reading_html, reading_copy.parent)
        for chapter in exported:
            if chapter.chapter_id:
                pictures_by_chapter[chapter.chapter_id] = _figure_pictures(chapter.html, reading_copy.parent)
        # The file is scoped to the chapters exported last; this pilot's chapters decide what matters.
        if not any(chapter.chapter_id in chapter_ids for chapter in exported):
            exported, export_source = None, "none"
    else:
        export_source = "rendered"
        exported = [ExportedChapter(chapter_id=chapter_id, title=title or "", html="") for chapter_id, title in sections]

    block_texts = {
        candidate.id: candidate.source_text for chapter_bundle in bundle.chapters for candidate in chapter_bundle.blocks
    }
    blocks: list[QaBlock] = []
    sentence_rendering: dict[str, str] = {}
    sentence_page: dict[str, int | None] = {}
    for chapter in chapters:
        # Pictures in the exported file, by caption; a figure block takes the first one with its caption.
        file_pictures = list(pictures_by_chapter.get(chapter.id, []))
        if reading_copy is not None and chapter.id not in pictures_by_chapter:
            # The export folded this chapter into the previous section: its figures are there.
            file_pictures = [picture for pictures in pictures_by_chapter.values() for picture in pictures]
        for block in render_blocks.get(chapter.id, []):
            is_figure = block.artifact_kind in {"image", "figure"} or block.block_type in {"image", "figure"}
            has_image: bool | None = None
            if is_figure:
                has_image = _has_picture(block, document, images_by_block)
                if block.block_id in pictures_by_block:
                    has_image = pictures_by_block[block.block_id]
                elif reading_copy is not None and block.render_mode == "image_anchor_with_translated_caption":
                    key = _caption_key(block.target_text or "")
                    match = next((index for index, (caption, _img) in enumerate(file_pictures) if caption == key), None)
                    if match is not None:
                        has_image = file_pictures.pop(match)[1]
            asset_map = {block.block_id: f"assets/{block.block_id}.png"} if has_image else {}
            block_html = markup.render_block_html(
                block, asset_map, include_source_toggle=False, include_notice=False, include_source_caption=False
            )
            sentence_ids = [sentence_id for sentence_id in block.source_sentence_ids if sentence_id in sentences_by_id]
            page = block.source_metadata.get("source_page_start")
            for sentence_id in sentence_ids:
                sentence_rendering.setdefault(sentence_id, block.render_mode)
                sentence_page.setdefault(sentence_id, page if isinstance(page, int) else None)
            packet_id = next((packets[sentence_id] for sentence_id in sentence_ids if sentence_id in packets), None)
            caption_text = block.source_metadata.get("linked_caption_text")
            context_ids = block.source_metadata.get("artifact_group_context_block_ids") or []
            blocks.append(
                QaBlock(
                    block_id=block.block_id,
                    chapter_id=chapter.id,
                    block_type=block.block_type,
                    render_mode=block.render_mode,
                    artifact_kind=block.artifact_kind,
                    source_text=block.source_text or "",
                    target_text=block.target_text or "",
                    html=block_html,
                    page=page if isinstance(page, int) else None,
                    has_image=has_image,
                    packet_id=packet_id,
                    caption_text=str(caption_text) if caption_text else None,
                    context_texts=tuple(str(block_texts.get(str(context_id)) or "") for context_id in context_ids),
                    parsed_text=str(block_texts.get(block.block_id) or ""),
                )
            )

    sentences = [
        QaSentence(
            id=sentence.id,
            chapter_id=sentence.chapter_id,
            block_id=sentence.block_id,
            source_text=sentence.source_text or "",
            target_text=targets.get(sentence.id, ""),
            packet_id=packets.get(sentence.id),
            page=sentence_page.get(sentence.id),
            rendered_as_translation=sentence_rendering[sentence.id] in _TRANSLATED_RENDER_MODES,
        )
        for sentence in sentences_by_id.values()
        # Sentences the reading copy does not show (running headers, hidden repairs) are not checked.
        if sentence.id in sentence_rendering
    ]

    # Parse health on the pilot chapters' blocks, with the PDF for figure crops.
    _document, health_chapters, health_blocks = load_document_inputs(session, document_id)
    wanted = set(chapter_ids)
    is_pdf = document.source_type in PDF_SOURCE_TYPES
    pages: _PdfPages | None = None
    if is_pdf and document.source_path and Path(document.source_path).exists():
        try:
            pages = _PdfPages(Path(document.source_path))
        except Exception:  # pragma: no cover - an unreadable file only loses the crop check
            logger.warning("pilot QA: cannot open %s", document.source_path, exc_info=True)
    health = HealthInputs(
        chapters=health_chapters,
        # A block a repair hid from the reading copy (a running header, a figure's label) is not the reader's.
        blocks=[
            block
            for block in health_blocks
            if block.chapter_id in wanted and not block.source_span_json.get("repair_hidden_from_export")
        ],
        is_pdf=is_pdf,
        outline=pages.outline() if pages else (),
        document_title=document.title_src or document.title,
        page_sizes=pages.page_sizes() if pages else None,
        find_figure_problems=pages.figure_problems if pages else None,
        academic_paper=_is_academic_paper(document),
    )
    inputs = PilotQaInputs(
        chapters=chapters,
        blocks=blocks,
        sentences=sentences,
        glossary=_glossary(session, document_id),
        model_issues=_model_issues(session, document_id, chapter_ids),
        exported=exported,
        health=health,
    )
    return _Loaded(inputs, export_source, pages)


# ---------------------------------------------------------------------------
# The service


class PilotQualityService:
    def __init__(self, session: Session, *, extra_checks: Sequence[ExtraCheck] = ()):
        self.session = session
        # Pluggable reviewers (a vision pass over figures, a sampled model read); none by default.
        self.extra_checks = tuple(extra_checks)

    def evaluate(
        self, document_id: str, chapter_ids: Sequence[str], *, use_export_file: bool = True
    ) -> tuple[list[dict[str, Any]], str]:
        """The layers of a QA run on these chapters, not kept: (layers, export source)."""
        loaded = load_pilot_inputs(self.session, document_id, chapter_ids, use_export_file=use_export_file)
        try:
            return pilot_qa.run_layers(loaded.inputs, self.extra_checks), loaded.export_source
        finally:
            loaded.close()

    def run(
        self,
        document_id: str,
        *,
        run: DocumentRun | None = None,
        trigger: str = "manual",
        chapter_ids: Sequence[str] | None = None,
    ) -> PilotQualityReport:
        started = time.monotonic()
        chapter_ids = list(chapter_ids or pilot_chapter_ids(self.session, document_id, run))
        if not chapter_ids:
            raise NoPilotChapters("这本书没有试译章节，无法做试译质检。")
        layers, export_source = self.evaluate(document_id, chapter_ids)
        summary = pilot_qa.summarize(layers)
        report = PilotQualityReport(
            document_id=document_id,
            run_id=run.id if run is not None else None,
            trigger=trigger,
            status=summary["status"],
            blocking_count=summary["counts"]["blocking"],
            major_count=summary["counts"]["major"],
            minor_count=summary["counts"]["minor"],
            chapter_ids_json=list(chapter_ids),
            layers_json=layers,
            export_source=export_source,
            duration_ms=int((time.monotonic() - started) * 1000),
            # Set here, not by the database: SQLite's now() has whole seconds, and "latest" must be exact.
            created_at=datetime.now(timezone.utc),
        )
        self.session.add(report)
        self.session.flush()
        return report

    def run_for_pilot(self, run: DocumentRun, *, trigger: str = "pilot_run") -> PilotQualityReport:
        """The pilot run's ``pilot_qa`` stage: a failure is kept as an "error" report, never raised."""
        try:
            with self.session.begin_nested():
                return self.run(run.document_id, run=run, trigger=trigger)
        except Exception as exc:
            logger.exception("pilot QA failed for run %s", run.id)
            report = PilotQualityReport(
                document_id=run.document_id,
                run_id=run.id,
                trigger=trigger,
                status="error",
                blocking_count=0,
                major_count=0,
                minor_count=0,
                chapter_ids_json=[],
                layers_json=[],
                export_source="none",
                error_text=f"{type(exc).__name__}: {exc}"[:2000],
                duration_ms=0,
                created_at=datetime.now(timezone.utc),
            )
            self.session.add(report)
            self.session.flush()
            return report


def report_for_run(session: Session, run_id: str) -> PilotQualityReport | None:
    return session.scalars(
        select(PilotQualityReport)
        .where(PilotQualityReport.run_id == run_id)
        .order_by(PilotQualityReport.created_at.desc(), PilotQualityReport.id.desc())
        .limit(1)
    ).first()


def latest_report(session: Session, document_id: str) -> PilotQualityReport | None:
    return session.scalars(
        select(PilotQualityReport)
        .where(PilotQualityReport.document_id == document_id)
        .order_by(PilotQualityReport.created_at.desc(), PilotQualityReport.id.desc())
        .limit(1)
    ).first()


def _chapter_titles(session: Session, chapter_ids: Sequence[str]) -> list[dict[str, Any]]:
    if not chapter_ids:
        return []
    rows = {
        str(chapter.id): chapter
        for chapter in session.scalars(select(Chapter).where(Chapter.id.in_(list(chapter_ids)))).all()
    }
    return [
        {"chapter_id": chapter_id, "ordinal": rows[chapter_id].ordinal, "title": rows[chapter_id].title_tgt or rows[chapter_id].title_src}
        for chapter_id in chapter_ids
        if chapter_id in rows
    ]


def report_payload(session: Session, report: PilotQualityReport) -> dict[str, Any]:
    return {
        "report_id": report.id,
        "document_id": report.document_id,
        "run_id": report.run_id,
        "trigger": report.trigger,
        "status": report.status,
        "counts": {"blocking": report.blocking_count, "major": report.major_count, "minor": report.minor_count},
        "chapters": _chapter_titles(session, list(report.chapter_ids_json or [])),
        "layers": list(report.layers_json or []),
        "export_source": report.export_source,
        "error": report.error_text,
        "duration_ms": report.duration_ms,
        "checked_at": report.created_at.isoformat() if report.created_at else None,
    }


def overview_summary(report: PilotQualityReport | None) -> dict[str, Any] | None:
    """What the book page says of the pilot's QA: the verdict and each layer's counts (blocking first)."""
    if report is None:
        return None
    return {
        "report_id": report.id,
        "status": report.status,
        "checked_at": report.created_at.isoformat() if report.created_at else None,
        "counts": {"blocking": report.blocking_count, "major": report.major_count, "minor": report.minor_count},
        "layers": [
            {"layer": layer.get("layer"), "name": layer.get("name"), "status": layer.get("status"), "counts": layer.get("counts")}
            for layer in report.layers_json or []
        ],
    }

