"""Add runtime incidents, patch proposals, and bundle revisions.

Revision ID: 20260326_0012
Revises: 20260326_0011
Create Date: 2026-03-26 23:35:00
"""

from alembic import op


revision = "20260326_0012"
down_revision = "20260326_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE runtime_bundle_revisions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            bundle_type TEXT NOT NULL,
            revision_name TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('draft', 'published', 'rolled_back')),
            parent_bundle_revision_id UUID REFERENCES runtime_bundle_revisions(id) ON DELETE SET NULL,
            manifest_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            rollout_scope_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            published_at TIMESTAMPTZ,
            active_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE INDEX idx_runtime_bundle_revisions_status ON runtime_bundle_revisions(status);"
    )

    op.execute(
        """
        CREATE TABLE runtime_incidents (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id UUID NOT NULL REFERENCES document_runs(id) ON DELETE CASCADE,
            scope_type TEXT NOT NULL CHECK (scope_type IN ('document', 'chapter', 'packet', 'sentence')),
            scope_id UUID NOT NULL,
            incident_kind TEXT NOT NULL CHECK (incident_kind IN ('export_misrouting', 'runtime_defect')),
            fingerprint TEXT NOT NULL,
            source_type TEXT,
            selected_route TEXT,
            runtime_bundle_revision_id UUID,
            status TEXT NOT NULL CHECK (status IN ('open', 'diagnosing', 'patch_proposed', 'validating', 'published', 'resolved', 'failed', 'frozen')),
            failure_count INTEGER NOT NULL DEFAULT 1,
            latest_work_item_id UUID REFERENCES work_items(id) ON DELETE SET NULL,
            route_evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            latest_error_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            bundle_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            status_detail_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            resolved_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_runtime_incidents_scope_fingerprint UNIQUE (scope_type, scope_id, fingerprint)
        );
        """
    )
    op.execute("CREATE INDEX idx_runtime_incidents_run_id ON runtime_incidents(run_id);")
    op.execute("CREATE INDEX idx_runtime_incidents_fingerprint ON runtime_incidents(fingerprint);")

    op.execute(
        """
        CREATE TABLE runtime_patch_proposals (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            incident_id UUID NOT NULL REFERENCES runtime_incidents(id) ON DELETE CASCADE,
            status TEXT NOT NULL CHECK (status IN ('proposed', 'validating', 'validated', 'published', 'rejected', 'rolled_back')),
            proposed_by TEXT,
            approved_by TEXT,
            patch_surface TEXT,
            diff_manifest_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            validation_report_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            published_bundle_revision_id UUID REFERENCES runtime_bundle_revisions(id) ON DELETE SET NULL,
            status_detail_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE INDEX idx_runtime_patch_proposals_incident_id ON runtime_patch_proposals(incident_id);")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_runtime_patch_proposals_incident_id;")
    op.execute("DROP TABLE IF EXISTS runtime_patch_proposals;")
    op.execute("DROP INDEX IF EXISTS idx_runtime_incidents_fingerprint;")
    op.execute("DROP INDEX IF EXISTS idx_runtime_incidents_run_id;")
    op.execute("DROP TABLE IF EXISTS runtime_incidents;")
    op.execute("DROP INDEX IF EXISTS idx_runtime_bundle_revisions_status;")
    op.execute("DROP TABLE IF EXISTS runtime_bundle_revisions;")
