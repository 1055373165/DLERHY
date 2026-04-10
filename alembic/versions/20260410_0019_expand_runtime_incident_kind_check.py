"""Expand runtime_incidents incident_kind check to match RuntimeIncidentKind enum.

Revision ID: 20260410_0019
Revises: 20260410_0018
Create Date: 2026-04-10 11:10:00
"""

from alembic import op


revision = "20260410_0019"
down_revision = "20260410_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE runtime_incidents DROP CONSTRAINT IF EXISTS runtime_incidents_incident_kind_check;")
    op.execute(
        """
        ALTER TABLE runtime_incidents
        ADD CONSTRAINT runtime_incidents_incident_kind_check
        CHECK (incident_kind IN (
            'export_misrouting',
            'runtime_defect',
            'review_deadlock',
            'packet_runtime_defect'
        ));
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE runtime_incidents DROP CONSTRAINT IF EXISTS runtime_incidents_incident_kind_check;")
    op.execute(
        """
        ALTER TABLE runtime_incidents
        ADD CONSTRAINT runtime_incidents_incident_kind_check
        CHECK (incident_kind IN (
            'export_misrouting',
            'runtime_defect'
        ));
        """
    )
