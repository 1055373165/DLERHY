"""Add runtime bundle rollback lineage and canary governance fields.

Revision ID: 20260327_0014
Revises: 20260327_0013
Create Date: 2026-03-27 14:25:00
"""

from alembic import op


revision = "20260327_0014"
down_revision = "20260327_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE runtime_bundle_revisions
        ADD COLUMN rollback_target_revision_id UUID REFERENCES runtime_bundle_revisions(id) ON DELETE SET NULL;
        """
    )
    op.execute(
        """
        ALTER TABLE runtime_bundle_revisions
        ADD COLUMN canary_verdict TEXT;
        """
    )
    op.execute(
        """
        ALTER TABLE runtime_bundle_revisions
        ADD COLUMN canary_report_json JSONB NOT NULL DEFAULT '{}'::jsonb;
        """
    )
    op.execute(
        """
        ALTER TABLE runtime_bundle_revisions
        ADD COLUMN freeze_reason TEXT;
        """
    )
    op.execute(
        """
        ALTER TABLE runtime_bundle_revisions
        ADD COLUMN frozen_at TIMESTAMPTZ;
        """
    )
    op.execute(
        """
        ALTER TABLE runtime_bundle_revisions
        ADD COLUMN rolled_back_at TIMESTAMPTZ;
        """
    )
    op.execute(
        "CREATE INDEX idx_runtime_bundle_revisions_canary_verdict ON runtime_bundle_revisions(canary_verdict);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_runtime_bundle_revisions_canary_verdict;")
    op.execute("ALTER TABLE runtime_bundle_revisions DROP COLUMN IF EXISTS rolled_back_at;")
    op.execute("ALTER TABLE runtime_bundle_revisions DROP COLUMN IF EXISTS frozen_at;")
    op.execute("ALTER TABLE runtime_bundle_revisions DROP COLUMN IF EXISTS freeze_reason;")
    op.execute("ALTER TABLE runtime_bundle_revisions DROP COLUMN IF EXISTS canary_report_json;")
    op.execute("ALTER TABLE runtime_bundle_revisions DROP COLUMN IF EXISTS canary_verdict;")
    op.execute("ALTER TABLE runtime_bundle_revisions DROP COLUMN IF EXISTS rollback_target_revision_id;")
