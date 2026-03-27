from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from book_agent.domain.enums import JobScopeType
from book_agent.domain.models.ops import DocumentRun
from book_agent.infra.repositories.runtime_resources import RuntimeResourcesRepository


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class ReviewSessionReconcileResult:
    review_session_id: str
    created: bool


class ReviewController:
    """
    Review-plane scaffold for explicit ReviewSession resources.

    Phase A behavior is intentionally narrow:
    - ensure one ReviewSession exists for the active ChapterRun generation
    - keep scope/runtime-bundle metadata mirrored from the owning run
    - materialize a chapter-scoped checkpoint for later review-lane controllers
    """

    def __init__(self, *, session: Session):
        self._session = session
        self._runtime_repo = RuntimeResourcesRepository(session)

    def reconcile_review_session(self, *, chapter_run_id: str) -> ReviewSessionReconcileResult:
        chapter_run = self._runtime_repo.get_chapter_run(chapter_run_id)
        run = self._session.get(DocumentRun, chapter_run.run_id)
        if run is None:
            raise ValueError(f"DocumentRun not found for ChapterRun: {chapter_run.run_id}")

        desired_generation = int(chapter_run.generation or 1)
        observed_generation = int(chapter_run.observed_generation or desired_generation)
        scope_json = {
            "run_id": chapter_run.run_id,
            "document_id": chapter_run.document_id,
            "chapter_id": chapter_run.chapter_id,
        }

        existing = self._runtime_repo.get_review_session_by_identity(
            chapter_run_id=chapter_run.id,
            desired_generation=desired_generation,
        )
        created = existing is None
        review_session = self._runtime_repo.ensure_review_session(
            chapter_run_id=chapter_run.id,
            desired_generation=desired_generation,
            observed_generation=observed_generation,
            scope_json=scope_json,
            runtime_bundle_revision_id=run.runtime_bundle_revision_id,
        )
        review_session = self._runtime_repo.update_review_session(
            review_session.id,
            observed_generation=observed_generation,
            scope_json=scope_json,
            runtime_bundle_revision_id=run.runtime_bundle_revision_id,
            last_reconciled_at=_utcnow(),
        )
        self._runtime_repo.upsert_checkpoint(
            run_id=chapter_run.run_id,
            scope_type=JobScopeType.CHAPTER,
            scope_id=chapter_run.chapter_id,
            checkpoint_key="review_controller.lane_health",
            checkpoint_json={
                "chapter_run_id": chapter_run.id,
                "review_session_id": review_session.id,
                "review_desired_generation": desired_generation,
                "review_observed_generation": observed_generation,
                "runtime_bundle_revision_id": run.runtime_bundle_revision_id,
            },
            generation=desired_generation,
        )
        return ReviewSessionReconcileResult(review_session_id=review_session.id, created=created)
