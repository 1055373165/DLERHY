import shutil
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.app.api.deps import get_db_session
from book_agent.core.config import get_settings
from book_agent.domain.enums import DocumentRunStatus, DocumentRunType, DocumentStatus, ExportStatus, ExportType, MemoryProposalStatus, SourceType
from book_agent.app.api.routes.runs import _to_run_summary_response
from book_agent.app.runtime.document_run_executor import ensure_document_run_executor
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.app.api.export_downloads import chapter_export_response, cleanup_path, document_export_response

from book_agent.schemas.document import DocumentContractResponse
from book_agent.schemas.workflow import (
    BootstrapDocumentRequest,
    RecoverySkillResponse,
    RecoverySkillsResponse,
    RecoverySkillsUpdateRequest,
    StructureRefreshResponse,
    ExportVersionHistoryResponse,
    ExportVersionResponse,
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
        cleanup_path(target_path)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception:
        cleanup_path(target_path)
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
    return chapter_export_response(
        _workflow_service(request, session).export_repository,
        document_id,
        chapter_id,
        export_type,
        artifact_roots=_artifact_roots(request),
    )


@router.get("/{document_id}/exports/download")
def download_document_export(
    document_id: str,
    request: Request,
    export_type: ExportType = Query(...),
    session: Session = Depends(get_db_session),
) -> FileResponse:
    return document_export_response(
        _workflow_service(request, session).export_repository,
        document_id,
        export_type,
        artifact_roots=_artifact_roots(request),
    )


def _recovery_skills_response(document) -> RecoverySkillsResponse:
    from book_agent.domain.structure import recovery_skills as registry

    enabled = registry.resolve((document.metadata_json or {}).get(registry.METADATA_KEY))
    return RecoverySkillsResponse(
        document_id=document.id,
        applies_to="pdf",
        skills=[
            RecoverySkillResponse(
                name=skill.name,
                title=skill.title,
                description=skill.description,
                passes=list(skill.passes),
                default_enabled=skill.default_enabled,
                enabled=enabled[skill.name],
            )
            for skill in registry.RECOVERY_SKILLS
        ],
    )


def _load_document_or_404(session: Session, document_id: str):
    from book_agent.domain.models import Document

    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    return document


@router.get("/{document_id}/recovery-skills", response_model=RecoverySkillsResponse)
def get_recovery_skills(document_id: str, session: Session = Depends(get_db_session)) -> RecoverySkillsResponse:
    """PDF recovery skills (publisher- or genre-specific passes) and whether this document uses them."""
    return _recovery_skills_response(_load_document_or_404(session, document_id))


@router.put("/{document_id}/recovery-skills", response_model=RecoverySkillsResponse)
def update_recovery_skills(
    document_id: str,
    payload: RecoverySkillsUpdateRequest,
    session: Session = Depends(get_db_session),
) -> RecoverySkillsResponse:
    """Switch skills on or off for this document; takes effect at the next structure refresh."""
    from book_agent.domain.structure import recovery_skills as registry

    document = _load_document_or_404(session, document_id)
    try:
        overrides = registry.validate_overrides(payload.skills)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    metadata = dict(document.metadata_json or {})
    metadata[registry.METADATA_KEY] = {**dict(metadata.get(registry.METADATA_KEY) or {}), **overrides}
    document.metadata_json = metadata
    session.flush()
    return _recovery_skills_response(document)


@router.post("/{document_id}/structure-refresh", response_model=StructureRefreshResponse)
def refresh_document_structure(
    document_id: str,
    request: Request,
    session: Session = Depends(get_db_session),
) -> StructureRefreshResponse:
    """Reparse the source file with the document's current recovery skills and fork changed sentences."""
    document = _load_document_or_404(session, document_id)
    workflow = _workflow_service(request, session)
    try:
        if document.source_type == SourceType.EPUB:
            artifacts = workflow.refresh_epub_structure(document_id)
        else:
            artifacts = workflow.refresh_pdf_structure(document_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    fork = artifacts.parse_revision_fork
    return StructureRefreshResponse(
        document_id=document_id,
        source_type=document.source_type.value,
        refreshed_chapter_count=len(artifacts.refreshed_chapter_ids),
        refreshed_block_count=len(artifacts.refreshed_block_ids),
        parse_revision_version=fork.parse_revision_version if fork is not None else None,
        retired_sentence_count=fork.retired_sentence_count if fork is not None else 0,
        created_sentence_count=fork.created_sentence_count if fork is not None else 0,
        carried_ratio=round(fork.carried_ratio, 3) if fork is not None and fork.forked else None,
        retranslate_packet_count=len(fork.retranslate_packet_ids) if fork is not None else 0,
    )


@router.get("/{document_id}/exports/{export_id}/versions", response_model=ExportVersionHistoryResponse)
def get_document_export_versions(
    document_id: str,
    export_id: str,
    request: Request,
    session: Session = Depends(get_db_session),
) -> ExportVersionHistoryResponse:
    """What each re-export of this artifact produced, newest first (bytes kept in the blob store)."""
    from book_agent.domain.models.review import Export

    export = session.get(Export, export_id)
    if export is None or export.document_id != document_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="export not found")
    versions = _workflow_service(request, session).export_repository.list_export_versions(export_id)
    return ExportVersionHistoryResponse(
        document_id=document_id,
        export_id=export_id,
        export_type=export.export_type.value,
        current_version=int(export.version or 1),
        versions=[
            ExportVersionResponse(
                version=row.version,
                file_path=row.file_path,
                manifest_path=row.manifest_path,
                content_sha256=row.content_sha256,
                byte_count=row.byte_count,
                created_at=row.created_at.isoformat() if row.created_at else None,
            )
            for row in versions
        ],
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
