"""Per-organisation provider credentials and monthly budgets.

provider_credentials.org_id NULL means shared (instance-wide); an organisation
with its own active credential uses it, others fall back to the shared one.
One active credential per scope: the old single partial index becomes one for
the shared scope and one per organisation.

Revision ID: 20260918_0041
Revises: 20260918_0040
Create Date: 2026-09-18
"""

from alembic import op

revision = "20260918_0041"
down_revision = "20260918_0040"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
ALTER TABLE provider_credentials ADD COLUMN org_id UUID REFERENCES orgs(id) ON DELETE CASCADE;
DROP INDEX IF EXISTS uq_provider_credentials_one_active;
CREATE UNIQUE INDEX uq_provider_credentials_one_active_shared ON provider_credentials (is_active) WHERE is_active AND org_id IS NULL;
CREATE UNIQUE INDEX uq_provider_credentials_one_active_per_org ON provider_credentials (org_id) WHERE is_active AND org_id IS NOT NULL;
ALTER TABLE orgs ADD COLUMN monthly_budget_usd NUMERIC(12, 2) CONSTRAINT ck_orgs_monthly_budget_non_negative CHECK (monthly_budget_usd IS NULL OR monthly_budget_usd >= 0)
"""

DOWNGRADE_SQL = """
ALTER TABLE orgs DROP COLUMN monthly_budget_usd;
DROP INDEX IF EXISTS uq_provider_credentials_one_active_per_org;
DROP INDEX IF EXISTS uq_provider_credentials_one_active_shared;
DELETE FROM provider_credentials WHERE org_id IS NOT NULL;
ALTER TABLE provider_credentials DROP COLUMN org_id;
CREATE UNIQUE INDEX uq_provider_credentials_one_active ON provider_credentials (is_active) WHERE is_active
"""


def _run(sql: str) -> None:
    for statement in sql.strip().split(";\n"):
        if statement.strip():
            op.execute(statement)


def upgrade() -> None:
    _run(UPGRADE_SQL)


def downgrade() -> None:
    _run(DOWNGRADE_SQL)
