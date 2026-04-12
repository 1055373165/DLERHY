"""Add ``requires_human_review`` gate + review-metadata columns to runtime_patch_proposals.

Self-hosted PatchProposal review workflow (Phase 5 CC-3).

``requires_human_review`` defaults FALSE so existing auto-publish incidents
keep their current behavior. When set TRUE, the incident controller refuses
to publish the patch until ``approved_by`` is populated via the reviewer
API (``POST /v1/patches/{id}/approve``).

Revision ID: 20260413_0022
Revises: 20260412_0021
Create Date: 2026-04-13 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260413_0022"
down_revision = "20260412_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runtime_patch_proposals",
        sa.Column(
            "requires_human_review",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
    )
    op.add_column(
        "runtime_patch_proposals",
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "runtime_patch_proposals",
        sa.Column("rejected_by", sa.Text(), nullable=True),
    )
    op.add_column(
        "runtime_patch_proposals",
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "runtime_patch_proposals",
        sa.Column("review_notes", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("runtime_patch_proposals", "review_notes")
    op.drop_column("runtime_patch_proposals", "rejected_at")
    op.drop_column("runtime_patch_proposals", "rejected_by")
    op.drop_column("runtime_patch_proposals", "approved_at")
    op.drop_column("runtime_patch_proposals", "requires_human_review")
