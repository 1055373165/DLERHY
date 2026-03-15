from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from book_agent.app.api.deps import get_db_session
from book_agent.core.config import get_settings
from book_agent.schemas.workflow import ExecuteActionResponse
from book_agent.services.workflows import DocumentWorkflowService
from book_agent.workers.factory import build_translation_worker

router = APIRouter()


@router.post("/{action_id}/execute", response_model=ExecuteActionResponse)
def execute_action(
    action_id: str,
    request: Request,
    run_followup: bool = False,
    session: Session = Depends(get_db_session),
) -> ExecuteActionResponse:
    try:
        translation_worker = getattr(request.app.state, "translation_worker", None)
        if translation_worker is None:
            translation_worker = build_translation_worker(get_settings())
        result = DocumentWorkflowService(
            session,
            export_root=getattr(request.app.state, "export_root", "artifacts/exports"),
            translation_worker=translation_worker,
        ).execute_action(action_id, run_followup=run_followup)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    review_artifacts = result.rerun_execution.review_artifacts if result.rerun_execution else None
    rebuild_artifacts = result.rerun_execution.rebuild_artifacts if result.rerun_execution else None
    return ExecuteActionResponse(
        action_id=action_id,
        status="completed",
        invalidation_count=len(result.action_execution.invalidations),
        rerun_scope_type=result.action_execution.rerun_plan.scope_type.value,
        rerun_scope_ids=result.action_execution.rerun_plan.scope_ids,
        followup_executed=result.rerun_execution is not None,
        rebuild_applied=rebuild_artifacts is not None,
        rebuilt_packet_ids=(rebuild_artifacts.rebuilt_packet_ids if rebuild_artifacts else []),
        rebuilt_snapshot_ids=(rebuild_artifacts.rebuilt_snapshot_ids if rebuild_artifacts else []),
        rebuilt_snapshots=(
            [
                {
                    "snapshot_id": snapshot.snapshot_id,
                    "snapshot_type": snapshot.snapshot_type,
                    "version": snapshot.version,
                }
                for snapshot in rebuild_artifacts.rebuilt_snapshots
            ]
            if rebuild_artifacts
            else []
        ),
        chapter_brief_version=(rebuild_artifacts.chapter_brief_version if rebuild_artifacts else None),
        termbase_version=(rebuild_artifacts.termbase_version if rebuild_artifacts else None),
        entity_snapshot_version=(rebuild_artifacts.entity_snapshot_version if rebuild_artifacts else None),
        rerun_packet_ids=(result.rerun_execution.translated_packet_ids if result.rerun_execution else []),
        rerun_translation_run_ids=(result.rerun_execution.translation_run_ids if result.rerun_execution else []),
        issue_resolved=(result.rerun_execution.issue_resolved if result.rerun_execution else None),
        recheck_issue_count=(len(review_artifacts.issues) if review_artifacts else None),
    )
