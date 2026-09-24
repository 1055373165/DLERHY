"""Document runs say who pays for their model calls: the customer, or us (the free pilot).

Revision ID: 20260925_0050
Revises: 20260925_0049
Create Date: 2026-09-25

The free two-chapter pilot (docs/agent-upgrade/08-pilot-quality-gate.md §5) runs through the same
pipeline as a paid run; its model calls must not be charged to the organisation's prepaid credit or
count against its monthly budget, but we still want to see what pilots cost us. A column on the run
rather than a flag in its JSON detail: the billing queries filter model-call events by it.
"""

from alembic import op

revision = "20260925_0050"
down_revision = "20260925_0049"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
ALTER TABLE document_runs ADD COLUMN billing_category TEXT NOT NULL DEFAULT 'customer'
    CONSTRAINT ck_document_runs_billing_category CHECK (billing_category IN ('customer', 'pilot'))
"""

DOWNGRADE_SQL = """
ALTER TABLE document_runs DROP COLUMN IF EXISTS billing_category
"""


def _run(sql: str) -> None:
    for statement in sql.strip().split(";\n"):
        if statement.strip():
            op.execute(statement)


def upgrade() -> None:
    _run(UPGRADE_SQL)


def downgrade() -> None:
    _run(DOWNGRADE_SQL)
