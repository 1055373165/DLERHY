"""Multi-instance execution: run ownership leases and provider credential revisions.

Revision ID: 20260918_0040
Revises: 20260917_0039
Create Date: 2026-09-18
"""

from alembic import op

revision = "20260918_0040"
down_revision = "20260917_0039"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
ALTER TABLE document_runs ADD COLUMN executor_owner TEXT;
ALTER TABLE document_runs ADD COLUMN executor_lease_expires_at TIMESTAMPTZ;
ALTER TABLE provider_credentials ADD COLUMN config_revision INTEGER NOT NULL DEFAULT 1
"""

DOWNGRADE_SQL = """
ALTER TABLE provider_credentials DROP COLUMN config_revision;
ALTER TABLE document_runs DROP COLUMN executor_lease_expires_at;
ALTER TABLE document_runs DROP COLUMN executor_owner
"""


def _execute(sql: str) -> None:
    for statement in sql.split(";"):
        if statement.strip():
            op.execute(statement)


def upgrade() -> None:
    _execute(UPGRADE_SQL)


def downgrade() -> None:
    _execute(DOWNGRADE_SQL)
