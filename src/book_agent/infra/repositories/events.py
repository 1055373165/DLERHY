"""Unified event publishing helper (Phase 4 CC-3).

All event writes in the system go through :func:`emit_event`. Centralising the
insert gives us three guarantees:

1. ``kind`` is validated against :data:`EVENT_KINDS` — unknown kinds raise at
   runtime, keeping the catalog truthful.
2. ``actor_kind`` is validated against :data:`VALID_ACTOR_KINDS` (matches the
   DB CHECK constraint).
3. The caller-owned ``Session`` is flushed so the Postgres trigger fires
   ``pg_notify('events_channel', id)`` before control returns. SSE consumers
   can act on the id immediately.

Transaction ownership stays with the caller — ``emit_event`` never commits.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from book_agent.domain.event_kinds import EVENT_KINDS, VALID_ACTOR_KINDS
from book_agent.domain.models.ops import Event


def emit_event(
    session: Session,
    *,
    kind: str,
    run_id: str | None = None,
    chapter_id: str | None = None,
    packet_id: str | None = None,
    actor_kind: str = "system",
    actor_id: str = "system",
    org_id: str = "default",
    correlation_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> Event:
    if kind not in EVENT_KINDS:
        raise ValueError(f"Unknown event kind: {kind!r}. Add it to event_kinds.py first.")
    if actor_kind not in VALID_ACTOR_KINDS:
        raise ValueError(f"Invalid actor_kind: {actor_kind!r}. Must be one of {sorted(VALID_ACTOR_KINDS)}.")

    event = Event(
        kind=kind,
        run_id=run_id,
        chapter_id=chapter_id,
        packet_id=packet_id,
        actor_kind=actor_kind,
        actor_id=actor_id,
        org_id=org_id,
        correlation_id=correlation_id,
        payload=payload or {},
    )
    session.add(event)
    session.flush()
    return event
