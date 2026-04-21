"""Add partial UNIQUE index on active document_runs per document.

Revision ID: 20260421_0027
Revises: 20260421_0026
Create Date: 2026-04-21 02:00:00

Backs P0.3a: the main pipeline assumes "at most one active run per
document". retry_run and resume_run both cold-start new DocumentRun
rows, and a second concurrent create_run / resume on the same
document could silently produce two runs competing for the same
frontier — both seeding work_items, both calling reconcile, both
writing terminal_* events. The partial UNIQUE catches it at the DB
layer regardless of which call path tried it.

Scope excludes PAUSED (retry-on-pause must remain possible) and every
terminal state (runs live in lineage chains forever).
"""

from alembic import op
import sqlalchemy as sa


revision = "20260421_0027"
down_revision = "20260421_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_document_runs_active_per_document",
        "document_runs",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running','draining')"),
        sqlite_where=sa.text("status IN ('queued','running','draining')"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_document_runs_active_per_document",
        table_name="document_runs",
    )
