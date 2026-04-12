"""Cost rollup materialized view over llm.call.completed events (Phase 4 CC-2).

Aggregates token spend and USD cost per ``run_id`` by reading the events bus.
Refreshed on demand via ``refresh_cost_rollup()``; no trigger (events arrive
at high cadence — batch refresh keeps write path cheap).

Revision ID: 20260412_0021
Revises: 20260412_0020
Create Date: 2026-04-12 00:00:00
"""

from alembic import op


revision = "20260412_0021"
down_revision = "20260412_0020"
branch_labels = None
depends_on = None


UPGRADE_SQL = """
CREATE MATERIALIZED VIEW cost_rollup_by_run AS
SELECT
    run_id,
    COUNT(*)                                                 AS call_count,
    COALESCE(SUM((payload->>'token_in')::BIGINT),     0)     AS token_in,
    COALESCE(SUM((payload->>'token_out')::BIGINT),    0)     AS token_out,
    COALESCE(SUM((payload->>'total_tokens')::BIGINT), 0)     AS total_tokens,
    COALESCE(SUM((payload->>'cost_usd')::NUMERIC),    0)     AS cost_usd,
    MIN(occurred_at)                                         AS first_call_at,
    MAX(occurred_at)                                         AS last_call_at
FROM events
WHERE kind = 'llm.call.completed'
  AND run_id IS NOT NULL
GROUP BY run_id
WITH NO DATA;

CREATE UNIQUE INDEX cost_rollup_by_run_run_id_idx ON cost_rollup_by_run (run_id);

CREATE MATERIALIZED VIEW cost_rollup_by_chapter AS
SELECT
    run_id,
    chapter_id,
    COUNT(*)                                                 AS call_count,
    COALESCE(SUM((payload->>'token_in')::BIGINT),     0)     AS token_in,
    COALESCE(SUM((payload->>'token_out')::BIGINT),    0)     AS token_out,
    COALESCE(SUM((payload->>'total_tokens')::BIGINT), 0)     AS total_tokens,
    COALESCE(SUM((payload->>'cost_usd')::NUMERIC),    0)     AS cost_usd
FROM events
WHERE kind = 'llm.call.completed'
  AND run_id IS NOT NULL
  AND chapter_id IS NOT NULL
GROUP BY run_id, chapter_id
WITH NO DATA;

CREATE UNIQUE INDEX cost_rollup_by_chapter_run_chapter_idx
    ON cost_rollup_by_chapter (run_id, chapter_id);

CREATE OR REPLACE FUNCTION refresh_cost_rollup() RETURNS void AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY cost_rollup_by_run;
    REFRESH MATERIALIZED VIEW CONCURRENTLY cost_rollup_by_chapter;
EXCEPTION WHEN feature_not_supported THEN
    -- CONCURRENTLY requires populated view; fall back on first refresh.
    REFRESH MATERIALIZED VIEW cost_rollup_by_run;
    REFRESH MATERIALIZED VIEW cost_rollup_by_chapter;
END;
$$ LANGUAGE plpgsql;
"""


DOWNGRADE_SQL = """
DROP FUNCTION IF EXISTS refresh_cost_rollup();
DROP MATERIALIZED VIEW IF EXISTS cost_rollup_by_chapter;
DROP MATERIALIZED VIEW IF EXISTS cost_rollup_by_run;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
