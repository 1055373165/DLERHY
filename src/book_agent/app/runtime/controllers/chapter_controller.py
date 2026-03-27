from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.app.runtime.controllers.review_controller import ReviewController
from book_agent.domain.enums import PacketTaskAction, PacketType
from book_agent.domain.models.ops import ChapterRun
from book_agent.domain.models.translation import TranslationPacket
from book_agent.infra.repositories.runtime_resources import RuntimeResourcesRepository


class ChapterController:
    """
    Chapter-scoped controller (Phase A mirror-only).

    Responsibility:
    - ensure PacketTask rows exist for TranslationPacket rows in the chapter
    - ensure ReviewSession rows exist for the active ChapterRun generation
    - do not seed review work items or infer review health yet
    """

    def __init__(self, *, session: Session):
        self._session = session
        self._runtime_repo = RuntimeResourcesRepository(session)
        self._review_controller = ReviewController(session=session)

    def ensure_packet_tasks(self, *, run_id: str) -> int:
        chapter_runs = self._session.scalars(select(ChapterRun).where(ChapterRun.run_id == run_id)).all()
        created = 0
        for chapter_run in chapter_runs:
            packet_rows = self._session.execute(
                select(TranslationPacket.id, TranslationPacket.packet_type).where(
                    TranslationPacket.chapter_id == chapter_run.chapter_id
                )
            ).all()
            for packet_id, packet_type in packet_rows:
                desired_action = _packet_task_action_for_packet_type(packet_type)
                existing = self._runtime_repo.get_packet_task_by_identity(
                    chapter_run_id=chapter_run.id,
                    packet_id=packet_id,
                    packet_generation=1,
                )
                if existing is not None:
                    continue
                self._runtime_repo.ensure_packet_task(
                    chapter_run_id=chapter_run.id,
                    packet_id=packet_id,
                    packet_generation=1,
                    desired_action=desired_action,
                )
                created += 1
        return created

    def ensure_review_sessions(self, *, run_id: str) -> int:
        chapter_runs = self._session.scalars(select(ChapterRun).where(ChapterRun.run_id == run_id)).all()
        created = 0
        for chapter_run in chapter_runs:
            result = self._review_controller.reconcile_review_session(chapter_run_id=chapter_run.id)
            created += int(result.created)
        return created


def _packet_task_action_for_packet_type(packet_type: PacketType) -> PacketTaskAction:
    if packet_type == PacketType.RETRANSLATE:
        return PacketTaskAction.RETRANSLATE
    return PacketTaskAction.TRANSLATE
