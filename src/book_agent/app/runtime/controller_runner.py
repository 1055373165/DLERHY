from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from book_agent.app.runtime.controllers.chapter_controller import ChapterController
from book_agent.app.runtime.controllers.packet_controller import PacketController
from book_agent.app.runtime.controllers.run_controller import RunController
from book_agent.domain.enums import JobScopeType
from book_agent.domain.models.ops import DocumentRun
from book_agent.infra.repositories.runtime_resources import RuntimeResourcesRepository


@dataclass(slots=True)
class ControllerReconcileStats:
    created_chapter_runs: int = 0
    created_packet_tasks: int = 0
    created_review_sessions: int = 0
    mirrored_packet_tasks: int = 0
    projected_packet_lane_health: int = 0


class ControllerRunner:
    """
    Runtime V2 controller reconcile runner (round-1 scaffold).

    Contract:
    - deterministic, DB-backed reconcile loop structure
    - mirror-only side effects: create ChapterRun/PacketTask/Checkpoint rows and
      bind PacketTask rows to existing translate work-items
    - does NOT seed work_items or infer review/incident state yet
    """

    def __init__(self, session_factory: sessionmaker):
        self._session_factory = session_factory

    def reconcile_run(self, *, run_id: str) -> ControllerReconcileStats:
        with self._session_factory() as session:
            return self._reconcile_run_with_session(session=session, run_id=run_id)

    def _reconcile_run_with_session(self, *, session: Session, run_id: str) -> ControllerReconcileStats:
        run = session.get(DocumentRun, run_id)
        if run is None:
            raise ValueError(f"DocumentRun not found: {run_id}")

        stats = ControllerReconcileStats()

        run_controller = RunController(session=session)
        stats.created_chapter_runs += run_controller.ensure_chapter_runs(run_id=run_id)

        chapter_controller = ChapterController(session=session)
        stats.created_packet_tasks += chapter_controller.ensure_packet_tasks(run_id=run_id)
        stats.created_review_sessions += chapter_controller.ensure_review_sessions(run_id=run_id)

        packet_controller = PacketController(session=session)
        stats.mirrored_packet_tasks += packet_controller.mirror_bind_work_items(run_id=run_id)
        stats.projected_packet_lane_health += packet_controller.project_lane_health(run_id=run_id)

        runtime_repo = RuntimeResourcesRepository(session)
        runtime_repo.upsert_checkpoint(
            run_id=run_id,
            scope_type=JobScopeType.DOCUMENT,
            scope_id=run.document_id,
            checkpoint_key="controller_runner.phase_a",
            checkpoint_json={
                "created_chapter_runs": stats.created_chapter_runs,
                "created_packet_tasks": stats.created_packet_tasks,
                "created_review_sessions": stats.created_review_sessions,
                "mirrored_packet_tasks": stats.mirrored_packet_tasks,
                "projected_packet_lane_health": stats.projected_packet_lane_health,
            },
            generation=1,
        )
        for chapter_run in runtime_repo.list_chapter_runs_for_run(run_id=run_id):
            review_session = runtime_repo.get_review_session_by_identity(
                chapter_run_id=chapter_run.id,
                desired_generation=int(chapter_run.generation or 1),
            )
            runtime_repo.upsert_checkpoint(
                run_id=run_id,
                scope_type=JobScopeType.CHAPTER,
                scope_id=chapter_run.chapter_id,
                checkpoint_key="controller_runner.chapter.phase_a",
                checkpoint_json={
                    "chapter_run_id": chapter_run.id,
                    "chapter_id": chapter_run.chapter_id,
                    "generation": chapter_run.generation,
                    "review_session_id": review_session.id if review_session is not None else None,
                    "review_desired_generation": (
                        review_session.desired_generation if review_session is not None else None
                    ),
                },
                generation=int(chapter_run.generation or 1),
            )

        session.commit()
        return stats
