"""Turn-level tracing onto the events bus (SSE consumers see agent activity live)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from book_agent.domain.event_kinds import (
    AGENT_APPROVAL_DECIDED,
    AGENT_APPROVAL_REQUESTED,
    AGENT_TOOL_CALLED,
    AGENT_TOOL_RETURNED,
    AGENT_TURN_FINISHED,
    AGENT_TURN_STARTED,
)
from book_agent.infra.repositories.events import emit_event


def _emit(session: Session, kind: str, *, turn_id: str, agent_kind: str, run_id: str | None, chapter_id: str | None, payload: dict[str, Any]) -> None:
    emit_event(
        session,
        kind=kind,
        run_id=run_id,
        chapter_id=chapter_id,
        actor_kind="agent",
        actor_id=f"agent.{agent_kind}",
        correlation_id=f"turn:{turn_id}",
        payload={"turn_id": turn_id, "agent_kind": agent_kind, **payload},
    )


def turn_started(session: Session, **kw: Any) -> None:
    _emit(session, AGENT_TURN_STARTED, **kw)


def turn_finished(session: Session, **kw: Any) -> None:
    _emit(session, AGENT_TURN_FINISHED, **kw)


def tool_called(session: Session, **kw: Any) -> None:
    _emit(session, AGENT_TOOL_CALLED, **kw)


def tool_returned(session: Session, **kw: Any) -> None:
    _emit(session, AGENT_TOOL_RETURNED, **kw)


def approval_requested(session: Session, **kw: Any) -> None:
    _emit(session, AGENT_APPROVAL_REQUESTED, **kw)


def approval_decided(session: Session, **kw: Any) -> None:
    _emit(session, AGENT_APPROVAL_DECIDED, **kw)
