"""Drop the cost rollup materialized views.

Revision ID: 20260917_0034
Revises: 20260915_0033
Create Date: 2026-09-17

Run spend is now aggregated straight from ``events`` (see
``RunControlRepository.usage_from_events``) by the run summary, the budget
guardrails and the cost endpoint. The views were refreshed only on demand,
their first CONCURRENTLY refresh failed on an unpopulated view, and they
were a second source of truth next to the run's own usage counters.
"""

from alembic import op

revision = "20260917_0034"
down_revision = "20260915_0033"
branch_labels = None
depends_on = None


DROP_SQL = """
DROP FUNCTION IF EXISTS refresh_cost_rollup();
DROP MATERIALIZED VIEW IF EXISTS cost_rollup_by_chapter;
DROP MATERIALIZED VIEW IF EXISTS cost_rollup_by_run;
"""

CREATE_SQL = """
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
    REFRESH MATERIALIZED VIEW cost_rollup_by_run;
    REFRESH MATERIALIZED VIEW cost_rollup_by_chapter;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for statement in DROP_SQL.strip().split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(CREATE_SQL)
