"""Add partial UNIQUE index on work_items active rows.

Revision ID: 20260421_0026
Revises: 20260421_0025
Create Date: 2026-04-21 01:00:00

Backs the DECIDE/EXECUTE main-loop refactor (P0.1a): a run must never
hold two active work_items for the same (stage, scope_type, scope_id)
at the same time. The app-layer dedupe in RunExecutionService.seed_work_items
already filters out existing live rows, but two seeders racing can both
see "no existing" and insert duplicates — which is exactly the failure
mode that lets a packet get translated twice or a chapter's frontier
get inflated.

Partial scope (status IN live set) so the REPAIR stage can reseed after
a prior attempt terminates: once a work_item is succeeded / terminal_failed
/ cancelled it no longer holds the slot.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260421_0026"
down_revision = "20260421_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_work_items_active_scope",
        "work_items",
        ["run_id", "stage", "scope_type", "scope_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('pending','leased','running','retryable_failed')"
        ),
        sqlite_where=sa.text(
            "status IN ('pending','leased','running','retryable_failed')"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_work_items_active_scope", table_name="work_items")
