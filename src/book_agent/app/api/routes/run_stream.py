"""Server-Sent Events stream of the event bus, scoped to a single run.

Replaces 2.5s polling of ``/v1/runs/{id}`` with push updates backed by
Postgres ``LISTEN events_channel``. Backfill ensures clients never miss
events during reconnect: ``Last-Event-ID`` (or ``last_event_id`` query)
says "I've seen up to N, send me everything after."

Each subscriber holds a dedicated raw psycopg connection. Sync generator
runs in FastAPI's threadpool; the connection is closed when the client
disconnects or when the keepalive loop detects it.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.engine import Engine

from book_agent.app.api.deps import get_session_factory


router = APIRouter()

HEARTBEAT_INTERVAL_SECONDS = 20.0
NOTIFY_POLL_SECONDS = 1.0
BACKFILL_LIMIT = 500


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _row_to_sse(row: dict[str, Any]) -> str:
    data = json.dumps(
        {
            "id": row["id"],
            "kind": row["kind"],
            "occurred_at": _iso(row["occurred_at"]),
            "run_id": row["run_id"],
            "chapter_id": row["chapter_id"],
            "packet_id": row["packet_id"],
            "actor_kind": row["actor_kind"],
            "actor_id": row["actor_id"],
            "correlation_id": row["correlation_id"],
            "payload": row["payload"] or {},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"id: {row['id']}\nevent: {row['kind']}\ndata: {data}\n\n"


def _parse_kinds(raw: str | None) -> set[str] | None:
    if not raw:
        return None
    return {k.strip() for k in raw.split(",") if k.strip()} or None


@router.get("/{run_id}/stream")
def stream_run_events(
    request: Request,
    run_id: str,
    last_event_id: int | None = Query(default=None, ge=0),
    kinds: str | None = Query(default=None, description="Comma-separated event kinds filter"),
) -> StreamingResponse:
    """Stream events for ``run_id`` as Server-Sent Events.

    Clients should set ``EventSource``'s standard ``Last-Event-ID`` header on
    reconnect. The ``last_event_id`` query param is accepted as a fallback
    for curl-driven debugging.
    """
    session_factory = get_session_factory(request)
    engine: Engine = session_factory.kw["bind"]
    if engine.dialect.name != "postgresql":
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="SSE streaming requires PostgreSQL (LISTEN/NOTIFY).",
        )

    header_cursor = request.headers.get("last-event-id")
    if header_cursor is not None:
        try:
            last_event_id = max(int(header_cursor), last_event_id or 0)
        except ValueError:
            pass

    kind_filter = _parse_kinds(kinds)
    start_cursor = last_event_id or 0

    def generator() -> Iterator[bytes]:
        raw = engine.raw_connection()
        try:
            raw.set_autocommit(True)
            driver_conn = raw.driver_connection
            with raw.cursor() as cur:
                cur.execute("LISTEN events_channel")

            cursor = start_cursor
            backfill_rows = _fetch_events(
                engine, run_id=run_id, after_id=cursor, kind_filter=kind_filter, limit=BACKFILL_LIMIT
            )
            for row in backfill_rows:
                yield _row_to_sse(row).encode("utf-8")
                cursor = max(cursor, int(row["id"]))

            yield b": ready\n\n"

            last_heartbeat = time.monotonic()
            while True:
                if _client_gone(request):
                    return

                new_ids: list[int] = []
                for note in driver_conn.notifies(timeout=NOTIFY_POLL_SECONDS):
                    try:
                        new_ids.append(int(note.payload))
                    except (TypeError, ValueError):
                        continue

                if new_ids:
                    next_cursor = max(max(new_ids), cursor)
                    rows = _fetch_events(
                        engine,
                        run_id=run_id,
                        after_id=cursor,
                        kind_filter=kind_filter,
                        limit=BACKFILL_LIMIT,
                        up_to_id=next_cursor,
                    )
                    for row in rows:
                        yield _row_to_sse(row).encode("utf-8")
                        cursor = max(cursor, int(row["id"]))

                now = time.monotonic()
                if now - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
                    yield b": heartbeat\n\n"
                    last_heartbeat = now
        finally:
            try:
                raw.close()
            except Exception:
                pass

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _fetch_events(
    engine: Engine,
    *,
    run_id: str,
    after_id: int,
    kind_filter: set[str] | None,
    limit: int,
    up_to_id: int | None = None,
) -> list[dict[str, Any]]:
    clauses = ["run_id = :run_id", "id > :after_id"]
    params: dict[str, Any] = {"run_id": run_id, "after_id": after_id, "limit": limit}
    if up_to_id is not None:
        clauses.append("id <= :up_to_id")
        params["up_to_id"] = up_to_id
    if kind_filter:
        clauses.append("kind = ANY(:kinds)")
        params["kinds"] = list(kind_filter)
    where = " AND ".join(clauses)
    stmt = text(
        f"""
        SELECT id, occurred_at, kind, run_id, chapter_id, packet_id,
               actor_kind, actor_id, org_id, correlation_id, payload
        FROM events
        WHERE {where}
        ORDER BY id ASC
        LIMIT :limit
        """
    )
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(stmt, params).mappings()]


def _client_gone(request: Request) -> bool:
    """Best-effort disconnect check usable from a sync generator."""
    state = getattr(request, "_is_disconnected", None)
    if callable(state):
        try:
            return bool(state())
        except Exception:
            return False
    return False
