"""Per-provider token prices and request overrides (configured in the UI instead of .env).

Revision ID: 20260919_0044
Revises: 20260918_0043
Create Date: 2026-09-19
"""

from alembic import op

revision = "20260919_0044"
down_revision = "20260918_0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE provider_credentials ADD COLUMN input_cost_per_1m_tokens NUMERIC(12, 4)")
    op.execute("ALTER TABLE provider_credentials ADD COLUMN input_cache_hit_cost_per_1m_tokens NUMERIC(12, 4)")
    op.execute("ALTER TABLE provider_credentials ADD COLUMN output_cost_per_1m_tokens NUMERIC(12, 4)")
    op.execute("ALTER TABLE provider_credentials ADD COLUMN request_overrides_json JSONB NOT NULL DEFAULT '{}'::jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE provider_credentials DROP COLUMN request_overrides_json")
    op.execute("ALTER TABLE provider_credentials DROP COLUMN output_cost_per_1m_tokens")
    op.execute("ALTER TABLE provider_credentials DROP COLUMN input_cache_hit_cost_per_1m_tokens")
    op.execute("ALTER TABLE provider_credentials DROP COLUMN input_cost_per_1m_tokens")
