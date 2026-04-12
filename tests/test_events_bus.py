"""End-to-end test: emit_event -> pg_notify -> LISTEN roundtrip (Phase 4 CC-1).

Requires a live Postgres at ``BOOK_AGENT_DATABASE_URL`` (default: dev compose).
Skipped automatically when unreachable so the rest of the suite stays green on
SQLite-only environments.
"""

from __future__ import annotations

import os
import time

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from book_agent.domain.event_kinds import EVENT_KINDS
from book_agent.domain.models.ops import Event
from book_agent.infra.repositories.events import emit_event


DEFAULT_DEV_DSN = "postgresql+psycopg://postgres:postgres@localhost:55432/book_agent"


def _pg_dsn() -> str | None:
    dsn = os.environ.get("BOOK_AGENT_DATABASE_URL", DEFAULT_DEV_DSN)
    return dsn if dsn.startswith("postgresql") else None


@pytest.fixture(scope="module")
def pg_engine():
    dsn = _pg_dsn()
    if dsn is None:
        pytest.skip("Postgres DSN not configured; events bus requires PG (NOTIFY/JSONB/BIGSERIAL).")
    engine = create_engine(dsn, future=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1 FROM events LIMIT 0"))
    except OperationalError as exc:
        pytest.skip(f"Postgres unreachable or events table missing: {exc}")
    yield engine
    engine.dispose()


class TestEventsBus:
    def test_emit_event_inserts_row_and_returns_id(self, pg_engine) -> None:
        with Session(pg_engine) as session:
            evt = emit_event(
                session,
                kind="run.created",
                run_id="test-run-insert",
                payload={"hello": "world"},
            )
            event_id = evt.id
            session.commit()

        assert event_id is not None

        with Session(pg_engine) as session:
            reread = session.get(Event, event_id)
            assert reread is not None
            assert reread.kind == "run.created"
            assert reread.payload == {"hello": "world"}
            assert reread.actor_kind == "system"
            assert reread.org_id == "default"

    def test_unknown_kind_is_rejected_before_insert(self, pg_engine) -> None:
        with Session(pg_engine) as session:
            with pytest.raises(ValueError, match="Unknown event kind"):
                emit_event(session, kind="definitely.not.registered")
            session.rollback()

    def test_invalid_actor_kind_is_rejected(self, pg_engine) -> None:
        with Session(pg_engine) as session:
            with pytest.raises(ValueError, match="Invalid actor_kind"):
                emit_event(session, kind="run.created", actor_kind="root")
            session.rollback()

    def test_all_catalog_kinds_insertable(self, pg_engine) -> None:
        """Every kind in EVENT_KINDS must survive the actor_kind CHECK and JSONB default."""
        with Session(pg_engine) as session:
            ids: list[int] = []
            for kind in sorted(EVENT_KINDS):
                evt = emit_event(session, kind=kind, run_id="catalog-sweep")
                ids.append(evt.id)
            session.commit()
        assert len(ids) == len(EVENT_KINDS)
        assert len(set(ids)) == len(ids)

    def test_notify_trigger_delivers_new_event_id(self, pg_engine) -> None:
        """Insert in one connection, receive pg_notify payload in another.

        This is the SSE substrate: a long-lived LISTEN connection should wake
        up within ~1s of an INSERT and receive the new row id as the payload.
        """
        raw = pg_engine.raw_connection()
        try:
            raw.set_autocommit(True)
            with raw.cursor() as cur:
                cur.execute("LISTEN events_channel")

            with Session(pg_engine) as session:
                evt = emit_event(
                    session,
                    kind="packet.built",
                    run_id="notify-probe",
                    packet_id="pkt-notify-1",
                )
                event_id = evt.id
                session.commit()

            deadline = time.monotonic() + 5.0
            seen_payloads: list[str] = []
            while time.monotonic() < deadline:
                notes = list(raw.driver_connection.notifies(timeout=0.5))
                seen_payloads.extend(n.payload for n in notes)
                if str(event_id) in seen_payloads:
                    break

            assert str(event_id) in seen_payloads, (
                f"Expected NOTIFY payload {event_id!r} in {seen_payloads!r}"
            )
        finally:
            raw.close()
