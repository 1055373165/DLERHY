from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, is_dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlalchemy import select

from book_agent.core.config import get_settings
from book_agent.core.ids import stable_id
from book_agent.domain.enums import ExportType, PacketStatus, TargetSegmentStatus
from book_agent.domain.models import Chapter, Document, Sentence
from book_agent.domain.models.review import ReviewIssue
from book_agent.ingestion.pdf.ocr import OcrPdfParser, OcrPdfTextExtractor, UvSuryaOcrRunner
from book_agent.domain.models.translation import AlignmentEdge, TargetSegment, TranslationPacket, TranslationRun
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory, session_scope
from book_agent.orchestrator.bootstrap import BootstrapOrchestrator
from book_agent.services.bootstrap import BootstrapPipeline, ParseService
from book_agent.services.chapter_concept_autolock import ChapterConceptAutoLockService
from book_agent.services.export import ExportGateError
from book_agent.services.review import ReviewService
from book_agent.services.workflows import DocumentWorkflowService
from book_agent.workers.factory import build_translation_worker


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "value"):
        return value.value
    if is_dataclass(value):
        return asdict(value)
    table = getattr(value, "__table__", None)
    columns = getattr(table, "columns", None)
    if columns is not None:
        return {column.name: getattr(value, column.name) for column in columns}
    raise TypeError(f"Unsupported JSON value: {type(value)!r}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bootstrap or resume a PDF and run a first-chapter translation smoke workflow."
    )
    parser.add_argument("--source-path", required=True)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--export-root", required=True)
    parser.add_argument("--report-path", required=True)
    parser.add_argument("--chapter-ordinal", default="auto")
    parser.add_argument("--sample-count", type=int, default=12)
    parser.add_argument("--packet-limit", type=int, default=None)
    parser.add_argument("--ocr-page-range", default=None)
    parser.add_argument("--auto-lock-unlocked-concepts", action="store_true")
    return parser


def _new_service(session, *, export_root: str) -> DocumentWorkflowService:
    settings = get_settings()
    return DocumentWorkflowService(
        session,
        export_root=export_root,
        translation_worker=build_translation_worker(settings),
    )


def _bootstrap_document(
    service: DocumentWorkflowService,
    source_path: Path,
    *,
    ocr_page_range: str | None,
) -> Any:
    if not ocr_page_range:
        return service.bootstrap_document(source_path)

    parse_service = ParseService(
        ocr_pdf_parser=OcrPdfParser(
            extractor=OcrPdfTextExtractor(
                runner=UvSuryaOcrRunner(page_range=ocr_page_range),
            )
        )
    )
    artifacts = BootstrapOrchestrator(
        BootstrapPipeline(parse_service=parse_service)
    ).bootstrap_document(source_path)
    service.bootstrap_repository.save(artifacts)
    return service.get_document_summary(artifacts.document.id)


def _write_report(report_path: Path, payload: dict[str, Any]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )


def _emit_progress(stage: str, **payload: Any) -> None:
    print(json.dumps({"stage": stage, **payload}, ensure_ascii=False), flush=True)


def _source_fingerprint(source_path: Path) -> str:
    return sha256(source_path.read_bytes()).hexdigest()


def _document_id_for_source(source_path: Path) -> str:
    return stable_id("document", _source_fingerprint(source_path))


def _resolve_document_id(session, source_path: Path) -> tuple[str, bool]:
    if source_path.exists():
        return _document_id_for_source(source_path), False

    existing_document = session.scalar(select(Document).where(Document.source_path == str(source_path)))
    if existing_document is not None:
        return existing_document.id, True

    raise FileNotFoundError(f"Source file not found and no matching document exists in database: {source_path}")


_FRONTMATTER_TITLE_PATTERNS = (
    "about this book",
    "about the author",
    "acknowledg",
    "author biography",
    "bibliography",
    "contents",
    "copyright",
    "cover",
    "dedication",
    "foreword",
    "glossary",
    "imprint",
    "index",
    "introduction to this edition",
    "list of figures",
    "list of tables",
    "preface",
    "table of contents",
    "title page",
)


def _normalize_title(title: str | None) -> str:
    if not title:
        return ""
    return " ".join(str(title).split()).strip()


def _looks_like_frontmatter_title(title: str | None) -> bool:
    normalized = _normalize_title(title).lower()
    if not normalized:
        return True
    if any(marker in normalized for marker in _FRONTMATTER_TITLE_PATTERNS):
        return True
    if normalized in {"contents", "references", "appendix", "appendices"}:
        return True
    return False


def _looks_like_structural_heading_title(title: str | None) -> bool:
    normalized = _normalize_title(title)
    if not normalized:
        return False
    lowered = normalized.lower()
    if lowered.startswith(("chapter ", "part ", "section ")):
        return True
    if re.match(r"^\d+(?:\.\d+)*\b", normalized):
        return True
    if re.match(r"^[ivxlcdm]+\.\s", lowered):
        return True
    return False


def _chapter_packet_status_counts(session, chapter_id: str) -> dict[str, int]:
    rows = _chapter_packet_rows(session, chapter_id)
    counts: dict[str, int] = {}
    for _, status in rows:
        status_value = status.value if hasattr(status, "value") else str(status)
        counts[status_value] = counts.get(status_value, 0) + 1
    return counts


def _auto_select_chapter_ordinal(
    chapters: list[Any],
    *,
    packet_counts_by_chapter_id: dict[str, dict[str, int]],
) -> int | None:
    ranked_candidates: list[tuple[tuple[int, int, int, int], int]] = []
    for chapter in chapters:
        chapter_id = getattr(chapter, "chapter_id", None)
        chapter_ordinal = getattr(chapter, "ordinal", None)
        if not isinstance(chapter_id, str) or not isinstance(chapter_ordinal, int):
            continue
        counts = packet_counts_by_chapter_id.get(chapter_id, {})
        built_count = int(counts.get(PacketStatus.BUILT.value, 0))
        if built_count <= 0:
            continue
        title_src = getattr(chapter, "title_src", "")
        frontmatter_penalty = 1 if _looks_like_frontmatter_title(title_src) else 0
        structural_penalty = 0 if _looks_like_structural_heading_title(title_src) else 1
        content_penalty = 0 if (int(getattr(chapter, "sentence_count", 0) or 0) >= 8) else 1
        ranked_candidates.append(
            (
                (frontmatter_penalty, structural_penalty, content_penalty, chapter_ordinal),
                chapter_ordinal,
            )
        )
    if not ranked_candidates:
        return None
    ranked_candidates.sort(key=lambda item: item[0])
    return ranked_candidates[0][1]


def _resolve_requested_chapter_ordinal(
    session,
    document_summary: Any,
    requested_chapter_ordinal: str,
) -> int | None:
    normalized = str(requested_chapter_ordinal).strip().lower()
    if normalized != "auto":
        return int(normalized)
    packet_counts_by_chapter_id = {
        str(chapter.chapter_id): _chapter_packet_status_counts(session, str(chapter.chapter_id))
        for chapter in document_summary.chapters
    }
    return _auto_select_chapter_ordinal(
        list(document_summary.chapters),
        packet_counts_by_chapter_id=packet_counts_by_chapter_id,
    )


def _chapter_packet_ids(session, chapter_id: str) -> list[str]:
    stmt = (
        select(TranslationPacket.id)
        .where(TranslationPacket.chapter_id == chapter_id)
        .order_by(TranslationPacket.created_at.asc(), TranslationPacket.id.asc())
    )
    return [row[0] for row in session.execute(stmt).all()]


def _chapter_packet_rows(session, chapter_id: str) -> list[tuple[str, PacketStatus]]:
    stmt = (
        select(TranslationPacket.id, TranslationPacket.status)
        .where(TranslationPacket.chapter_id == chapter_id)
        .order_by(TranslationPacket.created_at.asc(), TranslationPacket.id.asc())
    )
    return [(str(packet_id), status) for packet_id, status in session.execute(stmt).all()]


def _select_packet_ids_for_translation(
    packet_rows: list[tuple[str, PacketStatus]],
    *,
    packet_limit: int | None,
) -> tuple[list[str], list[str]]:
    all_packet_ids = [packet_id for packet_id, _ in packet_rows]
    runnable_packet_ids = [
        packet_id for packet_id, status in packet_rows if status == PacketStatus.BUILT
    ]
    if packet_limit is not None:
        runnable_packet_ids = runnable_packet_ids[: packet_limit]
    return all_packet_ids, runnable_packet_ids


def _chapter_translation_samples(
    session,
    chapter_id: str,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    stmt = (
        select(
            Sentence.source_text,
            TargetSegment.text_zh,
            TranslationRun.model_name,
            TranslationRun.created_at,
        )
        .join(AlignmentEdge, AlignmentEdge.sentence_id == Sentence.id)
        .join(TargetSegment, TargetSegment.id == AlignmentEdge.target_segment_id)
        .join(TranslationRun, TranslationRun.id == TargetSegment.translation_run_id)
        .where(
            Sentence.chapter_id == chapter_id,
            Sentence.translatable.is_(True),
            TargetSegment.final_status != TargetSegmentStatus.SUPERSEDED,
        )
        .order_by(TargetSegment.ordinal.asc(), Sentence.ordinal_in_block.asc())
        .limit(limit)
    )
    rows = session.execute(stmt).all()
    samples: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for row in rows:
        key = (row.source_text, row.text_zh)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        samples.append(
            {
                "source_text": row.source_text,
                "target_text": row.text_zh,
                "model_name": row.model_name,
                "translated_at": row.created_at.isoformat() if row.created_at else None,
            }
        )
    return samples


def _chapter_issue_summary(session, chapter_id: str) -> list[dict[str, Any]]:
    stmt = (
        select(ReviewIssue)
        .where(ReviewIssue.chapter_id == chapter_id)
        .order_by(ReviewIssue.updated_at.desc(), ReviewIssue.created_at.desc())
    )
    issues = session.scalars(stmt).all()
    return [
        {
            "issue_id": issue.id,
            "issue_type": issue.issue_type,
            "root_cause_layer": issue.root_cause_layer.value,
            "severity": issue.severity.value,
            "status": issue.status.value,
            "blocking": issue.blocking,
            "created_at": issue.created_at.isoformat(),
            "updated_at": issue.updated_at.isoformat(),
            "evidence": issue.evidence_json,
        }
        for issue in issues
    ]


def _chapter_packet_status_snapshot(session, chapter_id: str) -> dict[str, Any]:
    packets = session.execute(
        select(TranslationPacket.id, TranslationPacket.status)
        .where(TranslationPacket.chapter_id == chapter_id)
        .order_by(TranslationPacket.created_at.asc(), TranslationPacket.id.asc())
    ).all()
    counts: dict[str, int] = {}
    packet_statuses: list[dict[str, str]] = []
    for packet_id, status in packets:
        status_value = status.value if hasattr(status, "value") else str(status)
        counts[status_value] = counts.get(status_value, 0) + 1
        packet_statuses.append({"packet_id": packet_id, "status": status_value})
    return {
        "counts": counts,
        "packet_statuses": packet_statuses,
    }


def _chapter_summary_payload(session, document_id: str, chapter_ordinal: int, *, export_root: str) -> dict[str, Any]:
    service = _new_service(session, export_root=export_root)
    summary = service.get_document_summary(document_id)
    chapter_summary = next((chapter for chapter in summary.chapters if chapter.ordinal == chapter_ordinal), None)
    if chapter_summary is None:
        raise ValueError(f"Chapter ordinal {chapter_ordinal} not found in document {document_id}")
    chapter = session.get(Chapter, chapter_summary.chapter_id)
    packet_ids = _chapter_packet_ids(session, chapter_summary.chapter_id)
    return {
        "chapter_id": chapter_summary.chapter_id,
        "ordinal": chapter_summary.ordinal,
        "title_src": chapter_summary.title_src,
        "status": chapter_summary.status,
        "packet_count": chapter_summary.packet_count,
        "sentence_count": chapter_summary.sentence_count,
        "pdf_image_summary": chapter_summary.pdf_image_summary,
        "metadata": (chapter.metadata_json if chapter is not None else {}),
        "packet_ids": packet_ids,
        "packet_status_snapshot": _chapter_packet_status_snapshot(session, chapter_summary.chapter_id),
    }


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    source_path = Path(args.source_path).resolve()
    export_root = Path(args.export_root).resolve()
    report_path = Path(args.report_path).resolve()
    export_root.mkdir(parents=True, exist_ok=True)

    engine = build_engine(database_url=args.database_url)
    Base.metadata.create_all(engine)
    session_factory = build_session_factory(engine=engine)

    report: dict[str, Any] = {
        "source_path": str(source_path),
        "database_url": args.database_url,
        "export_root": str(export_root),
        "chapter_ordinal_requested": args.chapter_ordinal,
        "packet_limit": args.packet_limit,
        "ocr_page_range": args.ocr_page_range,
        "auto_lock_unlocked_concepts": args.auto_lock_unlocked_concepts,
        "document_id": None,
        "bootstrap_started_at": datetime.now().isoformat(),
        "bootstrap_in_progress": True,
    }
    _write_report(report_path, report)
    _emit_progress("bootstrap_start", document_id=None, source_path=str(source_path))

    with session_scope(session_factory) as session:
        document_id, source_missing_resumed = _resolve_document_id(session, source_path)
        report["document_id"] = document_id
        report["source_missing_resumed"] = source_missing_resumed
        _write_report(report_path, report)
        service = _new_service(session, export_root=str(export_root))
        existing_document = session.get(Document, document_id)
        if existing_document is None:
            bootstrap = _bootstrap_document(
                service,
                source_path,
                ocr_page_range=args.ocr_page_range,
            )
            report["bootstrap_summary"] = asdict(bootstrap)
            report["bootstrap_reused_existing"] = False
            _emit_progress(
                "bootstrapped",
                document_id=document_id,
                chapter_count=len(bootstrap.chapters),
                packet_count=sum(chapter.packet_count for chapter in bootstrap.chapters),
            )
        else:
            report["bootstrap_reused_existing"] = True
            _emit_progress(
                "resume_existing_document",
                document_id=document_id,
                document_status=existing_document.status.value,
            )

        report["bootstrap_in_progress"] = False
        report["bootstrap_finished_at"] = datetime.now().isoformat()
        document_summary = service.get_document_summary(document_id)
        resolved_chapter_ordinal = _resolve_requested_chapter_ordinal(
            session,
            document_summary,
            str(args.chapter_ordinal),
        )
        report["chapter_ordinal_resolved"] = resolved_chapter_ordinal
        report["document_summary_after_bootstrap"] = asdict(document_summary)
        if resolved_chapter_ordinal is None:
            report["no_work_remaining"] = {
                "reason": "no_eligible_chapter_with_built_packets",
            }
            _write_report(report_path, report)
            print(
                json.dumps(
                    {
                        "report_path": str(report_path),
                        "document_id": document_id,
                        "chapter_id": None,
                        "fully_translated": True,
                        "no_work_remaining": True,
                    }
                )
            )
            return 0
        report["selected_chapter"] = _chapter_summary_payload(
            session,
            document_id,
            resolved_chapter_ordinal,
            export_root=str(export_root),
        )
        chapter_id = str(report["selected_chapter"]["chapter_id"])
        all_packet_ids, packet_ids = _select_packet_ids_for_translation(
            _chapter_packet_rows(session, chapter_id),
            packet_limit=args.packet_limit,
        )
        report["selected_chapter"]["all_packet_ids"] = all_packet_ids
        report["selected_chapter"]["packet_ids"] = packet_ids
        report["selected_chapter"]["eligible_packet_count"] = len(packet_ids)
        _write_report(report_path, report)

    translated_packets: list[dict[str, Any]] = []
    skipped_packet_ids: list[str] = []
    for index, packet_id in enumerate(packet_ids, start=1):
        with session_scope(session_factory) as session:
            packet = session.get(TranslationPacket, packet_id)
            if packet is None:
                raise ValueError(f"Translation packet not found: {packet_id}")

            if packet.status != PacketStatus.BUILT:
                skipped_packet_ids.append(packet_id)
                report["translate"] = {
                    "requested_packet_count": len(packet_ids),
                    "translated_packet_count": len(translated_packets),
                    "skipped_packet_ids": skipped_packet_ids,
                    "translated_packets": translated_packets,
                    "remaining_packet_ids": packet_ids[index:],
                }
                report["selected_chapter"]["packet_status_snapshot"] = _chapter_packet_status_snapshot(session, chapter_id)
                _write_report(report_path, report)
                _emit_progress(
                    "skip_packet",
                    packet_id=packet_id,
                    packet_status=packet.status.value,
                    index=index,
                    total=len(packet_ids),
                )
                continue

            service = _new_service(session, export_root=str(export_root))
            artifacts = service.translation_service.execute_packet(packet_id)
            translated_packets.append(
                {
                    "packet_id": packet_id,
                    "translation_run_id": artifacts.translation_run.id,
                    "latency_ms": artifacts.translation_run.latency_ms,
                    "token_in": artifacts.translation_run.token_in,
                    "token_out": artifacts.translation_run.token_out,
                    "cost_usd": float(artifacts.translation_run.cost_usd or 0),
                    "target_segment_count": len(artifacts.target_segments),
                    "alignment_count": len(artifacts.alignment_edges),
                    "review_required_sentence_ids": [
                        sentence.id
                        for sentence in artifacts.updated_sentences
                        if sentence.sentence_status.value == "review_required"
                    ],
                }
            )
            report["translate"] = {
                "requested_packet_count": len(packet_ids),
                "translated_packet_count": len(translated_packets),
                "skipped_packet_ids": skipped_packet_ids,
                "translated_packets": translated_packets,
                "remaining_packet_ids": packet_ids[index:],
            }
            report["selected_chapter"]["packet_status_snapshot"] = _chapter_packet_status_snapshot(session, chapter_id)
            _write_report(report_path, report)
            _emit_progress(
                "packet_translated",
                packet_id=packet_id,
                index=index,
                total=len(packet_ids),
                latency_ms=artifacts.translation_run.latency_ms,
                token_in=artifacts.translation_run.token_in,
                token_out=artifacts.translation_run.token_out,
            )

    with session_scope(session_factory) as session:
        report["selected_chapter"]["packet_status_snapshot"] = _chapter_packet_status_snapshot(session, chapter_id)
        counts = report["selected_chapter"]["packet_status_snapshot"]["counts"]
        chapter_fully_translated = (
            counts.get(PacketStatus.BUILT.value, 0) == 0 and counts.get(PacketStatus.RUNNING.value, 0) == 0
        )
        report["selected_chapter"]["fully_translated"] = chapter_fully_translated
        if chapter_fully_translated:
            service = _new_service(session, export_root=str(export_root))
            review = service.review_document(document_id)
            report["review"] = asdict(review)
            report["chapter_worklist_detail"] = asdict(
                service.get_document_chapter_worklist_detail(document_id, chapter_id)
            )
            report["document_summary_after_review"] = asdict(service.get_document_summary(document_id))
            report["chapter_review_issues"] = _chapter_issue_summary(session, chapter_id)
            report["chapter_translation_samples"] = _chapter_translation_samples(
                session,
                chapter_id,
                limit=args.sample_count,
            )
            _emit_progress(
                "review_complete",
                document_id=document_id,
                chapter_id=chapter_id,
                total_issue_count=report["review"]["total_issue_count"],
            )
            if args.auto_lock_unlocked_concepts:
                source_terms = [
                    str(issue.get("evidence", {}).get("source_term") or "").strip()
                    for issue in report["chapter_review_issues"]
                    if issue.get("issue_type") == "UNLOCKED_KEY_CONCEPT"
                ]
                source_terms = [term for term in source_terms if term]
                if source_terms:
                    try:
                        auto_lock_artifacts = ChapterConceptAutoLockService(session).auto_lock_chapter_concepts(
                            chapter_id,
                            source_terms=source_terms,
                        )
                        report["concept_auto_lock"] = asdict(auto_lock_artifacts)
                        review_artifacts = ReviewService(service.review_repository).review_chapter(chapter_id)
                        report["review_after_concept_auto_lock"] = {
                            "issue_count": len(review_artifacts.issues),
                            "action_count": len(review_artifacts.actions),
                            "resolved_issue_ids": list(review_artifacts.resolved_issue_ids),
                            "summary": asdict(review_artifacts.summary),
                        }
                        report["chapter_review_issues_after_concept_auto_lock"] = _chapter_issue_summary(
                            session,
                            chapter_id,
                        )
                        report["document_summary_after_concept_auto_lock"] = asdict(
                            service.get_document_summary(document_id)
                        )
                        _emit_progress(
                            "concept_auto_lock_complete",
                            chapter_id=chapter_id,
                            locked_count=len(auto_lock_artifacts.locked_records),
                            remaining_issue_count=len(review_artifacts.issues),
                        )
                    except Exception as exc:
                        report["concept_auto_lock_error"] = {"message": str(exc), "source_terms": source_terms}
        else:
            report["review_skipped"] = {
                "reason": "selected_chapter_not_fully_translated",
                "packet_status_snapshot": report["selected_chapter"]["packet_status_snapshot"],
            }
        _write_report(report_path, report)

    if report["selected_chapter"]["fully_translated"]:
        with session_scope(session_factory) as session:
            service = _new_service(session, export_root=str(export_root))
            review_export = service.export_service.export_chapter(chapter_id, ExportType.REVIEW_PACKAGE)
            report["review_package_export"] = asdict(review_export)
            _write_report(report_path, report)
            _emit_progress(
                "review_package_exported",
                chapter_id=chapter_id,
                export_id=review_export.export_record.id,
            )

        try:
            with session_scope(session_factory) as session:
                service = _new_service(session, export_root=str(export_root))
                bilingual_export = service.export_service.export_chapter(chapter_id, ExportType.BILINGUAL_HTML)
                report["bilingual_export"] = asdict(bilingual_export)
                _emit_progress(
                    "bilingual_exported",
                    chapter_id=chapter_id,
                    export_id=bilingual_export.export_record.id,
                )
        except ExportGateError as exc:
            report["bilingual_export_error"] = {
                "message": str(exc),
                "detail": exc.to_http_detail(),
            }
        _write_report(report_path, report)

    _write_report(report_path, report)
    print(
        json.dumps(
            {
                "report_path": str(report_path),
                "document_id": document_id,
                "chapter_id": chapter_id,
                "fully_translated": report["selected_chapter"].get("fully_translated"),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
