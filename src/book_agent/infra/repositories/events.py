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

import logging
from typing import Any

from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session

from book_agent.domain.event_kinds import LLM_CALL_COMPLETED, LLM_CALL_FAILED
from book_agent.infra.metrics import record_llm_event
from book_agent.domain.event_kinds import EVENT_KINDS, VALID_ACTOR_KINDS
from book_agent.domain.models.ops import Event


_ORG_CACHE_LIMIT = 8192
_org_cache: dict[tuple[str, str], str] = {}


def resolve_event_org(
    session: Session,
    *,
    run_id: str | None = None,
    packet_id: str | None = None,
    chapter_id: str | None = None,
    document_id: str | None = None,
) -> str:
    """The organisation that owns the run, packet, chapter or document an event is about (default org otherwise)."""
    from sqlalchemy import select

    from book_agent.domain.models import Chapter, Document
    from book_agent.domain.models.auth import DEFAULT_ORG_ID
    from book_agent.domain.models.ops import DocumentRun
    from book_agent.domain.models.translation import TranslationPacket

    lookups = (
        ("run", run_id, lambda value: select(Document.org_id).join(DocumentRun, DocumentRun.document_id == Document.id).where(DocumentRun.id == value)),
        (
            "packet",
            packet_id,
            lambda value: select(Document.org_id)
            .join(Chapter, Chapter.document_id == Document.id)
            .join(TranslationPacket, TranslationPacket.chapter_id == Chapter.id)
            .where(TranslationPacket.id == value),
        ),
        ("chapter", chapter_id, lambda value: select(Document.org_id).join(Chapter, Chapter.document_id == Document.id).where(Chapter.id == value)),
        ("document", document_id, lambda value: select(Document.org_id).where(Document.id == value)),
    )
    for scope, value, statement in lookups:
        if not value:
            continue
        key = (scope, str(value))
        cached = _org_cache.get(key)
        if cached is not None:
            return cached
        try:
            org_id = session.scalar(statement(str(value)))
        except (ValueError, TypeError):
            org_id = None
        if org_id is not None:
            if len(_org_cache) >= _ORG_CACHE_LIMIT:
                _org_cache.clear()
            # Owners never change for these ids, so the cache needs no invalidation.
            _org_cache[key] = str(org_id)
            return str(org_id)
    return DEFAULT_ORG_ID


def emit_event(
    session: Session,
    *,
    kind: str,
    run_id: str | None = None,
    chapter_id: str | None = None,
    packet_id: str | None = None,
    actor_kind: str = "system",
    actor_id: str = "system",
    org_id: str | None = None,
    document_id: str | None = None,
    correlation_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> Event:
    """``org_id`` defaults to the owner of the run, packet, chapter or ``document_id`` the event is about."""
    if kind not in EVENT_KINDS:
        raise ValueError(f"Unknown event kind: {kind!r}. Add it to event_kinds.py first.")
    if actor_kind not in VALID_ACTOR_KINDS:
        raise ValueError(f"Invalid actor_kind: {actor_kind!r}. Must be one of {sorted(VALID_ACTOR_KINDS)}.")

    if org_id is None:
        org_id = resolve_event_org(
            session,
            run_id=run_id,
            packet_id=packet_id,
            chapter_id=chapter_id,
            document_id=document_id or (payload or {}).get("document_id"),
        )
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
    if kind in (LLM_CALL_COMPLETED, LLM_CALL_FAILED):
        record_llm_event(kind, event.payload or {})
        # Spend is real even when the caller's transaction later rolls back (an agent turn
        # that fails after its model calls, a work item that loses its lease): keep a copy
        # and write it on its own if that happens.
        session.info.setdefault(_PENDING_LLM_EVENTS, []).append(
            {
                "kind": kind,
                "run_id": run_id,
                "chapter_id": chapter_id,
                "packet_id": packet_id,
                "actor_kind": actor_kind,
                "actor_id": actor_id,
                "org_id": org_id,
                "correlation_id": correlation_id,
                "payload": dict(event.payload or {}),
            }
        )
    return event


logger = logging.getLogger(__name__)
_PENDING_LLM_EVENTS = "book_agent.pending_llm_events"


@sa_event.listens_for(Session, "after_commit")
def _forget_committed_llm_events(session: Session) -> None:
    session.info.pop(_PENDING_LLM_EVENTS, None)


@sa_event.listens_for(Session, "after_rollback")
def _rewrite_rolled_back_llm_events(session: Session) -> None:
    pending = session.info.pop(_PENDING_LLM_EVENTS, None)
    if not pending:
        return
    try:
        bind = session.get_bind()
    except Exception:  # an unbound session cannot have written anything
        return
    try:
        with Session(bind=bind) as ledger:
            for item in pending:
                ledger.add(Event(**{**item, "payload": {**item["payload"], "recorded_after_rollback": True}}))
            ledger.commit()
    except Exception:
        logger.warning("Could not keep %d model-call event(s) after a rollback", len(pending), exc_info=True)
