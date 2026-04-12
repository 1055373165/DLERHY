"""Events bus table with Postgres NOTIFY trigger (Phase 4 CC-1).

The ``events`` table is the single append-only event bus for the system:
SSE, cost telemetry, glossary violations, patch lifecycle all publish here.
Every INSERT fires pg_notify('events_channel', <id>) so a long-lived
LISTEN connection in the app can fan events out to SSE subscribers.

Revision ID: 20260412_0020
Revises: 20260410_0019
Create Date: 2026-04-12 00:00:00
"""

from alembic import op


revision = "20260412_0020"
down_revision = "20260410_0019"
branch_labels = None
depends_on = None


UPGRADE_SQL = """
CREATE TABLE events (
    id              BIGSERIAL PRIMARY KEY,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    kind            TEXT NOT NULL,
    run_id          TEXT,
    chapter_id      TEXT,
    packet_id       TEXT,
    actor_kind      TEXT NOT NULL DEFAULT 'system'
                    CHECK (actor_kind IN ('user', 'agent', 'system')),
    actor_id        TEXT NOT NULL DEFAULT 'system',
    org_id          TEXT NOT NULL DEFAULT 'default',
    correlation_id  TEXT,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX events_run_id_idx      ON events (run_id, id DESC);
CREATE INDEX events_kind_idx        ON events (kind, id DESC);
CREATE INDEX events_occurred_at_idx ON events (occurred_at);
CREATE INDEX events_payload_gin_idx ON events USING GIN (payload);

CREATE OR REPLACE FUNCTION events_notify() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify('events_channel', NEW.id::text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER events_notify_trigger
AFTER INSERT ON events
FOR EACH ROW EXECUTE FUNCTION events_notify();
"""


DOWNGRADE_SQL = """
DROP TRIGGER IF EXISTS events_notify_trigger ON events;
DROP FUNCTION IF EXISTS events_notify();
DROP INDEX IF EXISTS events_payload_gin_idx;
DROP INDEX IF EXISTS events_occurred_at_idx;
DROP INDEX IF EXISTS events_kind_idx;
DROP INDEX IF EXISTS events_run_id_idx;
DROP TABLE IF EXISTS events;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
