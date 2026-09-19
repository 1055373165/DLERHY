"""Drop the runtime self-repair control plane.

Revision ID: 20260914_0030
Revises: 20260503_0029
Create Date: 2026-09-14

Removes the incident / patch-proposal / bundle-revision pipeline and the
Runtime V2 projection tables (chapter_runs, packet_tasks, review_sessions,
runtime_checkpoints). None of these were read by the scheduling path; the
repair payload was a JSON manifest with a hard-coded passing validation.

Also drops the REPAIR work-item stage and the runtime_bundle_revision_id
binding columns on document_runs / work_items / run_budgets.

Irreversible: the dropped rows carry no information the remaining runtime can
use, so downgrade refuses instead of recreating empty tables.
"""

from alembic import op


revision = "20260914_0030"
down_revision = "20260503_0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # worker_leases cascade on work_item delete.
    op.execute("DELETE FROM work_items WHERE stage = 'repair';")
    op.execute("ALTER TABLE work_items DROP CONSTRAINT IF EXISTS work_items_stage_check;")
    op.execute(
        "ALTER TABLE work_items ADD CONSTRAINT work_items_stage_check "
        "CHECK (stage IN ('bootstrap', 'translate', 'review', 'export'));"
    )

    for table in (
        "runtime_patch_proposals",
        "runtime_incidents",
        "review_sessions",
        "packet_tasks",
        "runtime_checkpoints",
        "chapter_runs",
        "runtime_bundle_revisions",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE;")

    for table in ("document_runs", "work_items", "run_budgets"):
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS runtime_bundle_revision_id;")


def downgrade() -> None:
    raise NotImplementedError(
        "20260914_0030 drops the runtime self-repair tables and data; restore from a backup "
        "taken before upgrading instead of downgrading."
    )
