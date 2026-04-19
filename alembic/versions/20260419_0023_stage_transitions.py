"""Add ``stage_transitions`` append-only audit table (Phase 1 of state-consistency refactor).

Every pipeline stage / work_item status flip the executor performs must
insert one row in the same transaction as the change itself. No audit row
means the state change is illegal and the future Reconciler raises a P0
event on next scan. The table is the runtime source of truth for
postmortem forensics: ``caused_by_code`` pins the file:lineno of the
caller, ``triggered_by`` tags the operator kind (``main_loop``,
``lease_reaper``, ``reconciler``, ``api:<op>``).

Revision ID: 20260419_0023
Revises: 20260413_0022
Create Date: 2026-04-19 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260419_0023"
down_revision = "20260413_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stage_transitions",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("run_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("work_item_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("from_status", sa.Text(), nullable=False),
        sa.Column("to_status", sa.Text(), nullable=False),
        sa.Column("triggered_by", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("caused_by_code", sa.Text(), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(
            ["run_id"], ["document_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"], ["work_items.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_stage_transitions_run_id",
        "stage_transitions",
        ["run_id"],
    )
    op.create_index(
        "ix_stage_transitions_run_stage_created",
        "stage_transitions",
        ["run_id", "stage", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_stage_transitions_run_stage_created", table_name="stage_transitions")
    op.drop_index("ix_stage_transitions_run_id", table_name="stage_transitions")
    op.drop_table("stage_transitions")
