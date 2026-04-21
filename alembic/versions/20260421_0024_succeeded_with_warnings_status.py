"""Expand document_runs.status CHECK to include succeeded_with_warnings.

Revision ID: 20260421_0024
Revises: 20260419_0023
Create Date: 2026-04-21 00:00:00

Adds the ``succeeded_with_warnings`` terminal state used by
:class:`classify_run_outcome` when every *required* pipeline stage
reached SUCCEEDED but at least one *optional* stage ended in FAILED
(spec Phase 2 / P0.2c). The base schema was defined in migration 0004
with an inline, anonymous CHECK; Postgres auto-names it
``document_runs_status_check``. We drop-and-recreate it by that name.
"""

from alembic import op


revision = "20260421_0024"
down_revision = "20260419_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE document_runs DROP CONSTRAINT IF EXISTS document_runs_status_check;"
    )
    op.execute(
        """
        ALTER TABLE document_runs
        ADD CONSTRAINT document_runs_status_check
        CHECK (status IN (
            'queued',
            'running',
            'paused',
            'draining',
            'succeeded',
            'succeeded_with_warnings',
            'failed',
            'cancelled'
        ));
        """
    )


def downgrade() -> None:
    # Any row that reached the new terminal must be coerced back to the
    # closest legacy state — ``succeeded`` — before the old CHECK is
    # reinstated. Losing the "has_warnings" signal is acceptable here:
    # ``status_detail_json.last_control.detail_json`` still carries
    # ``failed_optional_stages`` from the original reconcile write.
    op.execute(
        """
        UPDATE document_runs
        SET status = 'succeeded'
        WHERE status = 'succeeded_with_warnings';
        """
    )
    op.execute(
        "ALTER TABLE document_runs DROP CONSTRAINT IF EXISTS document_runs_status_check;"
    )
    op.execute(
        """
        ALTER TABLE document_runs
        ADD CONSTRAINT document_runs_status_check
        CHECK (status IN (
            'queued',
            'running',
            'paused',
            'draining',
            'succeeded',
            'failed',
            'cancelled'
        ));
        """
    )
