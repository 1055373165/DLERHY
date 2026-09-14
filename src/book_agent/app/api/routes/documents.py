from dataclasses import dataclass
import mimetypes
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from book_agent.app.api.deps import get_db_session
from book_agent.core.config import get_settings
from book_agent.domain.document_titles import document_display_title, safe_title_for_filename
from book_agent.domain.enums import DocumentRunStatus, DocumentRunType, DocumentStatus, ExportStatus, ExportType, MemoryProposalStatus, SourceType
from book_agent.app.api.routes.runs import _to_run_summary_response
from book_agent.app.runtime.document_run_executor import ensure_document_run_executor
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.infra.storage.blobs import UNRECOVERABLE_SCHEME

from book_agent.schemas.document import DocumentContractResponse
from book_agent.schemas.workflow import (
    BootstrapDocumentRequest,
    ChapterMemoryProposalResponse,
    ChapterMemoryProposalDecisionRequest,
    ChapterMemoryProposalDecisionResponse,
    ChapterMemoryProposalListResponse,
    ChapterWorklistAssignmentClearRequest,
    ChapterWorklistAssignmentClearResponse,
    ChapterWorklistAssignmentRequest,
    ChapterWorklistAssignmentResponse,
    DocumentChapterWorklistResponse,
    DocumentChapterWorklistDetailResponse,
    DocumentHistoryBackfillResponse,
    DocumentExportDashboardResponse,
    DocumentHistoryPageResponse,
    ExportDetailResponse,
    DocumentSummaryResponse,
    ExportDocumentRequest,
    TranslateDocumentRequest,
)
from book_agent.orchestrator.run_plan import RUN_REQUEST_KEY
from book_agent.schemas.run_control import DocumentRunSummaryResponse
from book_agent.services.run_control import RunControlService
from book_agent.services.workflows import DocumentBusyError, DocumentWorkflowService

router = APIRouter()
_ALLOWED_UPLOAD_SUFFIXES = {".epub", ".pdf"}


@dataclass(frozen=True, slots=True)
class ArchiveInput:
    path: Path
    archive_name: str | None = None


def _upload_root(request: Request) -> Path:
    configured = getattr(request.app.state, "upload_root", get_settings().upload_root)
    return Path(configured).resolve()


def _export_root(request: Request) -> Path:
    configured = getattr(request.app.state, "export_root", get_settings().export_root)
    return Path(configured).resolve()


def _artifact_roots(request: Request) -> tuple[Path, ...]:
    export_root = _export_root(request)
    artifact_root = export_root.parent.resolve()
    if artifact_root == export_root:
        return (export_root,)
    return (export_root, artifact_root)


def _safe_upload_filename(filename: str | None) -> str:
    candidate = Path(filename or "").name.strip()
    if not candidate or candidate in {".", ".."}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Upload a valid EPUB or PDF file.")
    if Path(candidate).suffix.lower() not in _ALLOWED_UPLOAD_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported file type. Upload an .epub or .pdf file.",
        )
    return candidate


def _cleanup_path(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    finally:
        parent = path.parent
        try:
            parent.rmdir()
        except OSError:
            pass


def _artifact_fallback_candidates(path: Path) -> list[Path]:
    if path.exists():
        return [path]
    candidates: list[Path] = []
    if path.suffix.lower() == ".html" and path.stem == "merged-document":
        candidates.extend(
            sorted(
                path.parent.glob("merged-document*.html"),
                key=lambda candidate: (len(candidate.name), candidate.name),
            )
        )
    return [candidate.resolve() for candidate in candidates if candidate.exists()]


def _is_within_any_root(path: Path, roots: tuple[Path, ...]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
        except ValueError:
            continue
        return True
    return False


def _by_basename_under_document(
    basename: str,
    document_id: str,
    roots: tuple[Path, ...],
) -> Path | None:
    # Phase-2 self-heal: a stored file_path can point at a path that is
    # no longer reachable (e.g. a tempdir produced by a smoke run that
    # leaked into prod DB). The physical artifact is often still present
    # under the canonical layout <root>/[exports/]<document_id>/<basename>.
    # Try both layouts for each configured root.
    for root in roots:
        for candidate in (
            root / document_id / basename,
            root / "exports" / document_id / basename,
        ):
            resolved = candidate.resolve()
            if _is_within_any_root(resolved, roots) and resolved.exists():
                return resolved
    return None


def _assert_record_serviceable(record: Any) -> None:
    # Short-circuit deterministic 410 Gone when the row is known-stale.
    # This is set either by the M1 legacy backfill migration (for old
    # tempdir-rooted rows) or by future verifier/reaper jobs. Doing this
    # check BEFORE filesystem resolution means we never accidentally
    # serve a half-healed artifact when the metadata says the row is
    # poisoned.
    stale = getattr(record, "stale_reason", None)
    file_path = getattr(record, "file_path", "") or ""
    if stale or file_path.startswith(UNRECOVERABLE_SCHEME):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=(
                f"Export artifact is permanently unavailable "
                f"(reason: {stale or 'unrecoverable_sentinel'}). "
                "Re-export the document to generate a fresh artifact."
            ),
        )


def _blob_path(content_sha256: str, roots: tuple[Path, ...]) -> Path | None:
    # M2.2b: the CAS layout populated by scripts/materialize_blob_store.py
    # lives at ``<artifact_root>/blobs/<aa>/<bb>/<sha>``. We probe every
    # configured root because tests and prod use different parents, and
    # only accept a hit that lies under one of them so the existing
    # _is_within_any_root safety net keeps working.
    if not content_sha256 or len(content_sha256) < 4:
        return None
    relative = Path("blobs") / content_sha256[:2] / content_sha256[2:4] / content_sha256
    for root in roots:
        candidate = (root / relative).resolve()
        if _is_within_any_root(candidate, roots) and candidate.exists():
            return candidate
    return None


def _canonical_artifact_path(
    candidate: str | Path,
    *,
    roots: tuple[Path, ...],
    document_id: str | None = None,
) -> Path | None:
    # Non-raising variant of ``_resolve_artifact_path`` that intentionally
    # does NOT consult the CAS blob tree. We use it whenever we need the
    # writer-layout directory (for sidecar asset discovery) or the
    # original filename (which the blob tree has replaced with a sha).
    try:
        return _resolve_artifact_path(
            candidate,
            roots=roots,
            document_id=document_id,
            content_sha256=None,
        )
    except HTTPException:
        return None


_ASCII_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._\-]+")


def _ascii_fallback_name(filename: str) -> str:
    # RFC 6266 §4.3 asks for an ASCII ``filename=`` token alongside the
    # ``filename*=`` UTF-8 form. Browsers that parse the extended form
    # happily use the Chinese title; browsers that do not (some Safari
    # releases on macOS notoriously drop the extension when consuming
    # only ``filename*=``) need a clean ASCII fallback so the saved
    # file keeps its ``.html`` / ``.md`` / ``.pdf`` suffix.
    stem = Path(filename).stem
    ext = Path(filename).suffix
    ascii_stem = re.sub(r"-+", "-", _ASCII_FILENAME_UNSAFE.sub("-", stem)).strip("-")
    ascii_stem = ascii_stem or "export"
    return f"{ascii_stem}{ext}" if ext else ascii_stem


def _content_disposition(filename: str) -> str:
    # Produce a dual-form Content-Disposition header per RFC 6266.
    # Starlette only emits one form; when the payload name is non-ASCII
    # it drops the ``filename=`` alternative entirely, which loses the
    # extension on at least macOS Safari downloads.
    ascii_name = _ascii_fallback_name(filename)
    encoded = quote(filename, safe="")
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded}'


def _canonical_basename(file_path: str | Path) -> str:
    # The record's stored ``file_path`` is authoritative for naming even
    # when we serve bytes from the CAS tree — the blob on disk is named
    # by its sha256, so ``resolved.suffix`` would be empty and user
    # downloads would land without an extension.
    return Path(file_path).name


def _resolve_artifact_path(
    candidate: str | Path,
    *,
    roots: tuple[Path, ...],
    document_id: str | None = None,
    content_sha256: str | None = None,
) -> Path:
    # Belt-and-suspenders: even if a caller forgot to run
    # _assert_record_serviceable, an `unrecoverable://…` path must never
    # be interpreted as a filesystem path.
    candidate_str = str(candidate)
    if candidate_str.startswith(UNRECOVERABLE_SCHEME):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Export artifact is permanently unavailable (unrecoverable sentinel).",
        )
    # M2.2b: prefer the content-addressable blob when we have its sha.
    # The blob tree is the authoritative location — its path IS the
    # hash, so there is no way for it to drift. If the blob is missing
    # (not yet materialized), we fall through to the canonical path
    # logic below and everything behaves as before.
    if content_sha256:
        blob = _blob_path(content_sha256, roots)
        if blob is not None:
            return blob
    resolved = Path(candidate).resolve()
    fallback_candidates = [resolved, *_artifact_fallback_candidates(resolved)]
    for fallback_path in fallback_candidates:
        if _is_within_any_root(fallback_path, roots) and fallback_path.exists():
            return fallback_path
    # Phase 2: canonical-layout fallback keyed by (document_id, basename).
    if document_id:
        healed = _by_basename_under_document(resolved.name, document_id, roots)
        if healed is not None:
            return healed
    allowed_root_label = ", ".join(str(root) for root in roots)
    if any(_is_within_any_root(fallback_path, roots) for fallback_path in fallback_candidates):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Export artifact not found under allowed roots: {allowed_root_label}",
        )
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Export artifact is no longer available.",
    )


def _artifact_media_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"



def _export_sidecar_paths(file_path: Path) -> list[Path]:
    if file_path.suffix.lower() not in {".html", ".md"}:
        return []
    assets_dir = file_path.parent / "assets"
    if not assets_dir.is_dir():
        return []
    return sorted(path for path in assets_dir.rglob("*") if path.is_file())


def _sidecar_archive_name(sidecar_path: Path, canonical_path: Path) -> str | None:
    try:
        return sidecar_path.relative_to(canonical_path.parent).as_posix()
    except ValueError:
        return None


def _build_export_archive(
    document_id: str,
    export_type: ExportType,
    files: list[ArchiveInput],
    *,
    folder_name: str | None = None,
) -> Path:
    temp_file = tempfile.NamedTemporaryFile(
        prefix=f"book-agent-{document_id}-{export_type.value}-",
        suffix=".zip",
        delete=False,
    )
    archive_path = Path(temp_file.name)
    temp_file.close()
    if not folder_name:
        folder_name = f"{document_id}-{export_type.value}"
    common_root = Path(os.path.commonpath([str(file.path) for file in files]))
    with zipfile.ZipFile(archive_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        seen_names: set[str] = set()
        for index, file in enumerate(files, start=1):
            file_path = file.path
            if file.archive_name:
                archive_name = file.archive_name
            else:
                try:
                    archive_name = file_path.relative_to(common_root).as_posix()
                except ValueError:
                    archive_name = file_path.name
            if archive_name in seen_names:
                archive_name = f"{file_path.stem}-{index}{file_path.suffix}"
            seen_names.add(archive_name)
            archive.write(file_path, arcname=f"{folder_name}/{archive_name}")
    return archive_path


def _preferred_archive_name(original_path: str | Path, resolved_path: Path) -> str | None:
    original_name = Path(original_path).name
    if not original_name or original_name == resolved_path.name:
        return None
    return original_name



def _chapter_export_download_filename(
    document,
    chapter,
    export_type: ExportType,
    *,
    file_suffix: str,
    archive: bool = False,
) -> str:
    book_title = safe_title_for_filename(document_display_title(document), wrap_book_quotes=True)
    chapter_ordinal = getattr(chapter, "ordinal", None)
    chapter_title = safe_title_for_filename(
        getattr(chapter, "title_tgt", None) or getattr(chapter, "title_src", None),
        fallback="未命名章节",
    )
    chapter_prefix = f"第{chapter_ordinal}章" if isinstance(chapter_ordinal, int) and chapter_ordinal > 0 else "章节导出"
    label_map = {
        ExportType.BILINGUAL_HTML: "双语章节包",
        ExportType.REVIEW_PACKAGE: "审校包",
    }
    label = label_map.get(export_type, export_type.value)
    suffix = ".zip" if archive else file_suffix
    return f"{book_title}-{chapter_prefix}-{chapter_title}-{label}{suffix}"


def _append_archive_input(
    archive_inputs: list[ArchiveInput],
    seen_paths: set[str],
    file_path: Path,
    *,
    preferred_archive_name: str | None = None,
) -> None:
    for index, candidate in enumerate([file_path, *_export_sidecar_paths(file_path)]):
        resolved = str(candidate.resolve())
        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)
        archive_inputs.append(
            ArchiveInput(
                path=candidate,
                archive_name=(preferred_archive_name if index == 0 else None),
            )
        )


def _workflow_service(request: Request, session: Session) -> DocumentWorkflowService:
    return DocumentWorkflowService(
        session,
        export_root=getattr(request.app.state, "export_root", "artifacts/exports"),
        translation_worker=request.app.state.resolve_translation_worker(),
    )


def _proposal_http_exception(exc: ValueError) -> HTTPException:
    message = str(exc)
    lowered = message.casefold()
    if "not found" in lowered or "does not belong" in lowered:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)


@router.get("/contract", response_model=DocumentContractResponse)
def document_contract() -> DocumentContractResponse:
    return DocumentContractResponse(
        supported_source_types=["epub", "pdf_text"],
        current_phase="p1_text_pdf_guarded_bootstrap",
        notes=[
            "P1-A supports EPUB plus text PDFs with geometry-aware provenance and explicit layout-risk metadata.",
            "High-risk text PDFs now enter a guarded bootstrap path instead of being rejected up front; OCR-required PDFs still route outside the text-PDF path.",
            "Sentence coverage, packet-based context, provenance, and rerunability remain hard requirements.",
        ],
    )


@router.post("/bootstrap", response_model=DocumentSummaryResponse, status_code=status.HTTP_201_CREATED)
def bootstrap_document(
    payload: BootstrapDocumentRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> DocumentSummaryResponse:
    source_path = Path(payload.source_path)
    if not source_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source file not found: {source_path}",
        )
    try:
        summary = _workflow_service(request, session).bootstrap_document(source_path)
        # Commit before returning so a follow-up read from the web UI can resolve
        # the newly created document immediately.
        session.commit()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return DocumentSummaryResponse.model_validate(summary, from_attributes=True)


@router.post("/bootstrap-upload", response_model=DocumentSummaryResponse, status_code=status.HTTP_201_CREATED)
def bootstrap_uploaded_document(
    request: Request,
    source_file: UploadFile = File(...),
    session: Session = Depends(get_db_session),
) -> DocumentSummaryResponse:
    filename = _safe_upload_filename(source_file.filename)
    upload_root = _upload_root(request)
    upload_root.mkdir(parents=True, exist_ok=True)
    target_dir = upload_root / uuid4().hex
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / filename
    try:
        with target_path.open("wb") as buffer:
            shutil.copyfileobj(source_file.file, buffer)
        summary = _workflow_service(request, session).bootstrap_document(target_path)
        # Commit before returning so a follow-up read from the web UI can resolve
        # the newly created document immediately.
        session.commit()
    except ValueError as exc:
        _cleanup_path(target_path)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception:
        _cleanup_path(target_path)
        raise
    finally:
        source_file.file.close()
    return DocumentSummaryResponse.model_validate(summary, from_attributes=True)


@router.get("/history", response_model=DocumentHistoryPageResponse)
def list_document_history(
    request: Request,
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    query: str | None = Query(default=None, min_length=1, max_length=200),
    source_type: SourceType | None = Query(default=None),
    status: DocumentStatus | None = Query(default=None),
    latest_run_status: DocumentRunStatus | None = Query(default=None),
    merged_export_ready: bool | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> DocumentHistoryPageResponse:
    page = _workflow_service(request, session).list_document_history(
        limit=limit,
        offset=offset,
        query=query,
        source_type=source_type,
        status=status,
        latest_run_status=latest_run_status,
        merged_export_ready=merged_export_ready,
    )
    return DocumentHistoryPageResponse.model_validate(page, from_attributes=True)


@router.post("/history/backfill", response_model=DocumentHistoryBackfillResponse)
def backfill_document_history(
    session: Session = Depends(get_db_session),
) -> DocumentHistoryBackfillResponse:
    # Legacy SQLite backfill removed — PostgreSQL is the sole backend.
    return DocumentHistoryBackfillResponse(
        imported_document_count=0,
    )


@router.get("/{document_id}", response_model=DocumentSummaryResponse)
def get_document(
    document_id: str,
    request: Request,
    session: Session = Depends(get_db_session),
) -> DocumentSummaryResponse:
    try:
        summary = _workflow_service(request, session).get_document_summary(document_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return DocumentSummaryResponse.model_validate(summary, from_attributes=True)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: str,
    request: Request,
    session: Session = Depends(get_db_session),
) -> Response:
    try:
        _workflow_service(request, session).delete_document(document_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DocumentBusyError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{document_id}/exports", response_model=DocumentExportDashboardResponse)
def get_document_exports(
    document_id: str,
    request: Request,
    export_type: ExportType | None = Query(default=None),
    export_status: ExportStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db_session),
) -> DocumentExportDashboardResponse:
    try:
        dashboard = _workflow_service(request, session).get_document_export_dashboard(
            document_id,
            export_type=export_type,
            status=export_status,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return DocumentExportDashboardResponse.model_validate(dashboard, from_attributes=True)


@router.get("/{document_id}/chapters")
def list_document_chapters(
    document_id: str,
    request: Request,
    session: Session = Depends(get_db_session),
):
    """List all chapters for a document (lightweight, no issue/worklist data)."""
    from book_agent.domain.models.document import Chapter
    chapters = (
        session.execute(
            select(Chapter)
            .where(Chapter.document_id == document_id)
            .order_by(Chapter.ordinal)
        )
        .scalars()
        .all()
    )
    return [
        {
            "chapter_id": ch.id,
            "ordinal": ch.ordinal,
            "title_src": ch.title_src,
            "title_tgt": ch.title_tgt,
            "status": ch.status.value if hasattr(ch.status, "value") else str(ch.status),
        }
        for ch in chapters
    ]


@router.get("/{document_id}/chapters/worklist", response_model=DocumentChapterWorklistResponse)
def get_document_chapter_worklist(
    document_id: str,
    request: Request,
    queue_priority: Literal["immediate", "high", "medium"] | None = Query(default=None),
    sla_status: Literal["on_track", "due_soon", "breached", "unknown"] | None = Query(default=None),
    owner_ready: bool | None = Query(default=None),
    needs_immediate_attention: bool | None = Query(default=None),
    assigned: bool | None = Query(default=None),
    assigned_owner_name: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db_session),
) -> DocumentChapterWorklistResponse:
    try:
        worklist = _workflow_service(request, session).get_document_chapter_worklist(
            document_id,
            queue_priority=queue_priority,
            sla_status=sla_status,
            owner_ready=owner_ready,
            needs_immediate_attention=needs_immediate_attention,
            assigned=assigned,
            assigned_owner_name=assigned_owner_name,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return DocumentChapterWorklistResponse.model_validate(worklist, from_attributes=True)


@router.get(
    "/{document_id}/chapters/{chapter_id}/worklist",
    response_model=DocumentChapterWorklistDetailResponse,
)
def get_document_chapter_worklist_detail(
    document_id: str,
    chapter_id: str,
    request: Request,
    session: Session = Depends(get_db_session),
) -> DocumentChapterWorklistDetailResponse:
    try:
        detail = _workflow_service(request, session).get_document_chapter_worklist_detail(
            document_id,
            chapter_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return DocumentChapterWorklistDetailResponse.model_validate(detail, from_attributes=True)


@router.put(
    "/{document_id}/chapters/{chapter_id}/worklist/assignment",
    response_model=ChapterWorklistAssignmentResponse,
)
def assign_document_chapter_worklist(
    document_id: str,
    chapter_id: str,
    payload: ChapterWorklistAssignmentRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> ChapterWorklistAssignmentResponse:
    try:
        assignment = _workflow_service(request, session).assign_document_chapter_worklist_owner(
            document_id,
            chapter_id,
            owner_name=payload.owner_name,
            assigned_by=payload.assigned_by,
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ChapterWorklistAssignmentResponse.model_validate(assignment, from_attributes=True)


@router.post(
    "/{document_id}/chapters/{chapter_id}/worklist/assignment/clear",
    response_model=ChapterWorklistAssignmentClearResponse,
)
def clear_document_chapter_worklist_assignment(
    document_id: str,
    chapter_id: str,
    payload: ChapterWorklistAssignmentClearRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> ChapterWorklistAssignmentClearResponse:
    try:
        assignment = _workflow_service(request, session).clear_document_chapter_worklist_owner(
            document_id,
            chapter_id,
            cleared_by=payload.cleared_by,
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ChapterWorklistAssignmentClearResponse(
        document_id=document_id,
        chapter_id=chapter_id,
        cleared=True,
        cleared_by=payload.cleared_by,
        note=payload.note,
        cleared_assignment_id=assignment.assignment_id,
    )


@router.get("/{document_id}/chapters/{chapter_id}/exports/download")
def download_document_chapter_export(
    document_id: str,
    chapter_id: str,
    request: Request,
    export_type: ExportType = Query(default=ExportType.BILINGUAL_HTML),
    session: Session = Depends(get_db_session),
) -> FileResponse:
    workflow = _workflow_service(request, session)
    chapter_bundle = workflow.export_repository.load_chapter_bundle(chapter_id)
    try:
        records = workflow.export_repository.list_document_exports_filtered(
            document_id,
            export_type=export_type,
            status=ExportStatus.SUCCEEDED,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    chapter_record = next(
        (
            record
            for record in records
            if str((record.input_version_bundle_json or {}).get("chapter_id") or "") == chapter_id
        ),
        None,
    )
    if chapter_record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No successful {export_type.value} export is available for chapter {chapter_id}.",
        )

    _assert_record_serviceable(chapter_record)
    artifact_roots = _artifact_roots(request)
    file_path = _resolve_artifact_path(
        chapter_record.file_path,
        roots=artifact_roots,
        document_id=document_id,
        content_sha256=chapter_record.content_sha256,
    )
    canonical_basename = _canonical_basename(chapter_record.file_path)
    canonical_suffix = Path(canonical_basename).suffix or ""
    canonical_path = _canonical_artifact_path(
        chapter_record.file_path, roots=artifact_roots, document_id=document_id
    ) or file_path
    archive_inputs = [
        ArchiveInput(path=file_path, archive_name=_preferred_archive_name(chapter_record.file_path, file_path)),
        *[
            ArchiveInput(
                path=sidecar_path,
                archive_name=_sidecar_archive_name(sidecar_path, canonical_path),
            )
            for sidecar_path in _export_sidecar_paths(canonical_path)
        ],
    ]
    if len(archive_inputs) == 1:
        dl_name = _chapter_export_download_filename(
            chapter_bundle.document,
            chapter_bundle.chapter,
            export_type,
            file_suffix=canonical_suffix,
        )
        return FileResponse(
            path=file_path,
            media_type=mimetypes.guess_type(canonical_basename)[0]
            or "application/octet-stream",
            headers={"content-disposition": _content_disposition(dl_name)},
        )

    archive_path = _build_export_archive(document_id, export_type, archive_inputs)
    dl_name = _chapter_export_download_filename(
        chapter_bundle.document,
        chapter_bundle.chapter,
        export_type,
        file_suffix=".zip",
        archive=True,
    )
    return FileResponse(
        path=archive_path,
        media_type="application/zip",
        headers={"content-disposition": _content_disposition(dl_name)},
        background=BackgroundTask(_cleanup_path, archive_path),
    )


@router.get("/{document_id}/exports/download")
def download_document_export(
    document_id: str,
    request: Request,
    export_type: ExportType = Query(...),
    session: Session = Depends(get_db_session),
) -> FileResponse:
    workflow = _workflow_service(request, session)
    document = workflow.export_repository.get_document(document_id)

    # Bilingual document downloads reuse merged exports (which already contain both languages)
    _BILINGUAL_TO_MERGED = {
        ExportType.BILINGUAL_HTML: ExportType.MERGED_HTML,
        ExportType.BILINGUAL_MARKDOWN: ExportType.MERGED_MARKDOWN,
    }
    lookup_type = _BILINGUAL_TO_MERGED.get(export_type, export_type)

    try:
        primary_records = workflow.export_repository.list_document_exports_filtered(
            document_id,
            export_type=lookup_type,
            status=ExportStatus.SUCCEEDED,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    if not primary_records:
        # Downloads only serve existing exports; POST /export enqueues one.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No successful {lookup_type.value} exports are available for download.",
        )

    book_title = safe_title_for_filename(document_display_title(document), wrap_book_quotes=True)
    label_map = {
        ExportType.MERGED_HTML: "中文阅读稿",
        ExportType.MERGED_MARKDOWN: "中文阅读稿-Markdown",
        ExportType.BILINGUAL_HTML: "中英文对照",
        ExportType.BILINGUAL_MARKDOWN: "中英文对照-Markdown",
        ExportType.REBUILT_EPUB: "重建EPUB",
        ExportType.REBUILT_PDF: "重建PDF",
        ExportType.REVIEW_PACKAGE: "审校包",
    }
    label = label_map.get(export_type, export_type.value)

    # If every matching record is marked stale, fail fast with a
    # consistent 410 rather than letting _resolve_artifact_path bubble
    # a 404 and confuse operators.
    for record in primary_records:
        _assert_record_serviceable(record)
    artifact_roots = _artifact_roots(request)
    files = [
        _resolve_artifact_path(
            record.file_path,
            roots=artifact_roots,
            document_id=document_id,
            content_sha256=record.content_sha256,
        )
        for record in primary_records
    ]

    # Use the latest (last) primary record only — deliver a single merged file
    file_path = files[-1]
    primary_record = primary_records[-1]
    canonical_basename = _canonical_basename(primary_record.file_path)
    ext = Path(canonical_basename).suffix or ""
    main_filename = f"{book_title}-{label}{ext}"
    # Sidecar assets (images, CSS) live beside the canonical writer
    # output, not the blob. Probe the canonical layout; fall back to
    # the served path (still correct for rows with no blob preference).
    canonical_path = _canonical_artifact_path(
        primary_record.file_path, roots=artifact_roots, document_id=document_id
    ) or file_path

    archive_inputs: list[ArchiveInput] = []
    seen_paths: set[str] = set()
    _append_archive_input(
        archive_inputs,
        seen_paths,
        file_path,
        preferred_archive_name=main_filename,
    )
    for sidecar_path in _export_sidecar_paths(canonical_path):
        _append_archive_input(
            archive_inputs,
            seen_paths,
            sidecar_path,
            preferred_archive_name=_sidecar_archive_name(sidecar_path, canonical_path),
        )

    # Single file with no sidecar assets → return directly
    if len(archive_inputs) == 1:
        return FileResponse(
            path=file_path,
            media_type=mimetypes.guess_type(canonical_basename)[0]
            or "application/octet-stream",
            headers={"content-disposition": _content_disposition(main_filename)},
        )

    # Multiple files (main + assets/) → zip with book-title folder
    archive_folder = f"{book_title}-{label}"
    archive_path = _build_export_archive(
        document_id,
        export_type,
        archive_inputs,
        folder_name=archive_folder,
    )
    zip_name = f"{archive_folder}.zip"
    return FileResponse(
        path=archive_path,
        media_type="application/zip",
        headers={"content-disposition": _content_disposition(zip_name)},
        background=BackgroundTask(_cleanup_path, archive_path),
    )


@router.get("/{document_id}/exports/{export_id}", response_model=ExportDetailResponse)
def get_document_export_detail(
    document_id: str,
    export_id: str,
    request: Request,
    session: Session = Depends(get_db_session),
) -> ExportDetailResponse:
    try:
        detail = _workflow_service(request, session).get_document_export_detail(document_id, export_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ExportDetailResponse.model_validate(detail, from_attributes=True)


@router.get(
    "/{document_id}/chapters/{chapter_id}/memory-proposals",
    response_model=ChapterMemoryProposalListResponse,
)
def list_chapter_memory_proposals(
    document_id: str,
    chapter_id: str,
    request: Request,
    proposal_status: MemoryProposalStatus | None = Query(default=None, alias="status"),
    session: Session = Depends(get_db_session),
) -> ChapterMemoryProposalListResponse:
    try:
        proposals = _workflow_service(request, session).list_chapter_memory_proposals(
            document_id,
            chapter_id,
            status=proposal_status.value if proposal_status is not None else None,
        )
    except ValueError as exc:
        raise _proposal_http_exception(exc) from exc
    return ChapterMemoryProposalListResponse(
        document_id=document_id,
        chapter_id=chapter_id,
        status_filter=proposal_status.value if proposal_status is not None else None,
        proposal_count=len(proposals),
        proposals=[ChapterMemoryProposalResponse.model_validate(proposal, from_attributes=True) for proposal in proposals],
    )


@router.post(
    "/{document_id}/chapters/{chapter_id}/memory-proposals/{proposal_id}/approve",
    response_model=ChapterMemoryProposalDecisionResponse,
)
def approve_chapter_memory_proposal(
    document_id: str,
    chapter_id: str,
    proposal_id: str,
    request: Request,
    payload: ChapterMemoryProposalDecisionRequest | None = None,
    session: Session = Depends(get_db_session),
) -> ChapterMemoryProposalDecisionResponse:
    try:
        result = _workflow_service(request, session).approve_chapter_memory_proposal(
            document_id,
            chapter_id,
            proposal_id,
            actor_name=(payload.actor_name if payload is not None else None),
            note=(payload.note if payload is not None else None),
        )
    except ValueError as exc:
        raise _proposal_http_exception(exc) from exc
    return ChapterMemoryProposalDecisionResponse.model_validate(result, from_attributes=True)


@router.post(
    "/{document_id}/chapters/{chapter_id}/memory-proposals/{proposal_id}/reject",
    response_model=ChapterMemoryProposalDecisionResponse,
)
def reject_chapter_memory_proposal(
    document_id: str,
    chapter_id: str,
    proposal_id: str,
    request: Request,
    payload: ChapterMemoryProposalDecisionRequest | None = None,
    session: Session = Depends(get_db_session),
) -> ChapterMemoryProposalDecisionResponse:
    try:
        result = _workflow_service(request, session).reject_chapter_memory_proposal(
            document_id,
            chapter_id,
            proposal_id,
            actor_name=(payload.actor_name if payload is not None else None),
            note=(payload.note if payload is not None else None),
        )
    except ValueError as exc:
        raise _proposal_http_exception(exc) from exc
    return ChapterMemoryProposalDecisionResponse.model_validate(result, from_attributes=True)


def _enqueue_document_run(
    request: Request,
    session: Session,
    *,
    document_id: str,
    run_type: DocumentRunType,
    run_request: dict[str, Any],
) -> DocumentRunSummaryResponse:
    control = RunControlService(RunControlRepository(session))
    try:
        created = control.create_run(
            document_id=document_id,
            run_type=run_type,
            requested_by="api.documents",
            status_detail_json={"source": "api.documents", RUN_REQUEST_KEY: run_request},
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    summary = control.resume_run(created.run_id, actor_id="api.documents", note="enqueued")
    # The executor reads the run from its own session; commit before waking it.
    session.commit()
    ensure_document_run_executor(request.app).wake(summary.run_id)
    return _to_run_summary_response(summary)


@router.post(
    "/{document_id}/translate",
    response_model=DocumentRunSummaryResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def translate_document(
    document_id: str,
    payload: TranslateDocumentRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> DocumentRunSummaryResponse:
    return _enqueue_document_run(
        request,
        session,
        document_id=document_id,
        run_type=DocumentRunType.TRANSLATE_TARGETED,
        run_request={"packet_ids": list(payload.packet_ids)},
    )


@router.post(
    "/{document_id}/review",
    response_model=DocumentRunSummaryResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def review_document(
    document_id: str,
    request: Request,
    session: Session = Depends(get_db_session),
) -> DocumentRunSummaryResponse:
    return _enqueue_document_run(
        request,
        session,
        document_id=document_id,
        run_type=DocumentRunType.REVIEW_FULL,
        run_request={},
    )


@router.post(
    "/{document_id}/export",
    response_model=DocumentRunSummaryResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def export_document(
    document_id: str,
    payload: ExportDocumentRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> DocumentRunSummaryResponse:
    return _enqueue_document_run(
        request,
        session,
        document_id=document_id,
        run_type=DocumentRunType.EXPORT_FULL,
        run_request={
            "export_type": payload.export_type,
            "auto_execute_followup_on_gate": payload.auto_execute_followup_on_gate,
            "max_auto_followup_attempts": payload.max_auto_followup_attempts,
        },
    )
