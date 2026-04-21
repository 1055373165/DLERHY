"""Add max_no_progress_seconds column to run_budgets.

Revision ID: 20260421_0025
Revises: 20260421_0024
Create Date: 2026-04-21 00:10:00

Introduces a no-progress-based guardrail distinct from
``max_wall_clock_seconds`` (spec Phase 2 / P0.2b).

Rationale: a slow but progressing run should not be paused just because
wall-clock elapsed seconds hit an arbitrary cap — that pattern punishes
large books with many packets. The no-progress cap instead measures time
since the *last successful* work-item completion and only fires when
meaningful forward progress has stopped. ``enforce_budget_guardrails``
reads ``status_detail_json.last_progress.completed_at`` (already written
by :meth:`_bump_success_progress`) to compute the window, so no new
per-run column is needed — only the budget cap itself lives here.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260421_0025"
down_revision = "20260421_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "run_budgets",
        sa.Column("max_no_progress_seconds", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("run_budgets", "max_no_progress_seconds")
