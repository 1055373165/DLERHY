"""Add run control plane models.

Revision ID: 20260315_0004
Revises: 20260315_0003
Create Date: 2026-03-15 05:00:00
"""

from alembic import op


revision = "20260315_0004"
down_revision = "20260315_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE document_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            run_type TEXT NOT NULL CHECK (run_type IN (
                'bootstrap', 'translate_full', 'translate_targeted', 'review_full', 'export_full', 'repair_targeted'
            )),
            status TEXT NOT NULL CHECK (status IN (
                'queued', 'running', 'paused', 'draining', 'succeeded', 'failed', 'cancelled'
            )),
            backend TEXT,
            model_name TEXT,
            requested_by TEXT,
            priority INTEGER NOT NULL DEFAULT 100,
            resume_from_run_id UUID REFERENCES document_runs(id) ON DELETE SET NULL,
            stop_reason TEXT,
            status_detail_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            started_at TIMESTAMPTZ,
            finished_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        """
        CREATE TABLE work_items (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id UUID NOT NULL REFERENCES document_runs(id) ON DELETE CASCADE,
            stage TEXT NOT NULL CHECK (stage IN ('bootstrap', 'translate', 'review', 'repair', 'export')),
            scope_type TEXT NOT NULL CHECK (scope_type IN ('document', 'chapter', 'packet', 'issue_action', 'export')),
            scope_id UUID NOT NULL,
            attempt INTEGER NOT NULL DEFAULT 1,
            priority INTEGER NOT NULL DEFAULT 100,
            status TEXT NOT NULL CHECK (status IN (
                'pending', 'leased', 'running', 'succeeded', 'retryable_failed', 'terminal_failed', 'cancelled'
            )),
            lease_owner TEXT,
            lease_expires_at TIMESTAMPTZ,
            last_heartbeat_at TIMESTAMPTZ,
            started_at TIMESTAMPTZ,
            finished_at TIMESTAMPTZ,
            input_version_bundle_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            output_artifact_refs_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            error_class TEXT,
            error_detail_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        """
        CREATE TABLE worker_leases (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id UUID NOT NULL REFERENCES document_runs(id) ON DELETE CASCADE,
            work_item_id UUID NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
            worker_name TEXT NOT NULL,
            worker_instance_id TEXT NOT NULL,
            lease_token TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL CHECK (status IN ('active', 'released', 'expired')),
            lease_expires_at TIMESTAMPTZ NOT NULL,
            last_heartbeat_at TIMESTAMPTZ,
            released_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        """
        CREATE TABLE run_budgets (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id UUID NOT NULL UNIQUE REFERENCES document_runs(id) ON DELETE CASCADE,
            max_wall_clock_seconds INTEGER,
            max_total_cost_usd NUMERIC(12, 6),
            max_total_token_in INTEGER,
            max_total_token_out INTEGER,
            max_retry_count_per_work_item INTEGER,
            max_consecutive_failures INTEGER,
            max_parallel_workers INTEGER,
            max_parallel_requests_per_provider INTEGER,
            max_auto_followup_attempts INTEGER,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        """
        CREATE TABLE run_audit_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id UUID NOT NULL REFERENCES document_runs(id) ON DELETE CASCADE,
            work_item_id UUID REFERENCES work_items(id) ON DELETE SET NULL,
            event_type TEXT NOT NULL,
            actor_type TEXT NOT NULL CHECK (actor_type IN ('system', 'model', 'human')),
            actor_id TEXT,
            payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE INDEX idx_document_runs_document_status "
        "ON document_runs(document_id, status, run_type);"
    )
    op.execute(
        "CREATE INDEX idx_work_items_run_stage_status "
        "ON work_items(run_id, stage, status);"
    )
    op.execute(
        "CREATE INDEX idx_work_items_scope_status "
        "ON work_items(scope_type, scope_id, status);"
    )
    op.execute(
        "CREATE INDEX idx_worker_leases_work_item_status "
        "ON worker_leases(work_item_id, status);"
    )
    op.execute(
        "CREATE INDEX idx_worker_leases_expiry "
        "ON worker_leases(status, lease_expires_at);"
    )
    op.execute(
        "CREATE INDEX idx_run_audit_events_run "
        "ON run_audit_events(run_id, created_at DESC);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_run_audit_events_run;")
    op.execute("DROP INDEX IF EXISTS idx_worker_leases_expiry;")
    op.execute("DROP INDEX IF EXISTS idx_worker_leases_work_item_status;")
    op.execute("DROP INDEX IF EXISTS idx_work_items_scope_status;")
    op.execute("DROP INDEX IF EXISTS idx_work_items_run_stage_status;")
    op.execute("DROP INDEX IF EXISTS idx_document_runs_document_status;")
    op.execute("DROP TABLE IF EXISTS run_audit_events;")
    op.execute("DROP TABLE IF EXISTS run_budgets;")
    op.execute("DROP TABLE IF EXISTS worker_leases;")
    op.execute("DROP TABLE IF EXISTS work_items;")
    op.execute("DROP TABLE IF EXISTS document_runs;")
