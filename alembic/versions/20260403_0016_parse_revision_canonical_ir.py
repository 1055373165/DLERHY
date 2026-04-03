"""Add parse revisions and canonical IR sidecars.

Revision ID: 20260403_0016
Revises: 20260327_0015
Create Date: 2026-04-03 10:00:00
"""

from alembic import op


revision = "20260403_0016"
down_revision = "20260327_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE document_parse_revisions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            version INTEGER NOT NULL,
            parser_version INTEGER NOT NULL,
            parse_ir_version INTEGER NOT NULL DEFAULT 1,
            source_type TEXT NOT NULL CHECK (source_type IN ('epub', 'pdf_text', 'pdf_scan', 'pdf_mixed')),
            source_path TEXT,
            source_fingerprint TEXT,
            status TEXT NOT NULL CHECK (status IN ('active', 'superseded', 'invalidated')),
            canonical_ir_path TEXT,
            canonical_ir_checksum TEXT,
            projection_hints_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_document_parse_revisions_document_version UNIQUE (document_id, version)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE document_parse_revision_artifacts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            document_parse_revision_id UUID NOT NULL REFERENCES document_parse_revisions(id) ON DELETE CASCADE,
            artifact_type TEXT NOT NULL,
            storage_path TEXT NOT NULL,
            content_type TEXT,
            checksum TEXT,
            status TEXT NOT NULL CHECK (status IN ('active', 'invalidated')),
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_document_parse_revision_artifacts_revision_type UNIQUE (document_parse_revision_id, artifact_type)
        );
        """
    )
    op.execute("CREATE INDEX idx_document_parse_revisions_document_version ON document_parse_revisions(document_id, version DESC);")
    op.execute(
        "CREATE INDEX idx_document_parse_revision_artifacts_revision_id ON document_parse_revision_artifacts(document_parse_revision_id);"
    )
    op.execute("ALTER TABLE blocks ADD COLUMN parse_revision_id UUID REFERENCES document_parse_revisions(id) ON DELETE SET NULL;")
    op.execute("ALTER TABLE blocks ADD COLUMN canonical_node_id TEXT;")
    op.execute("ALTER TABLE sentences ADD COLUMN parse_revision_id UUID REFERENCES document_parse_revisions(id) ON DELETE SET NULL;")
    op.execute("ALTER TABLE sentences ADD COLUMN canonical_node_id TEXT;")
    op.execute("CREATE INDEX idx_blocks_parse_revision_id ON blocks(parse_revision_id);")
    op.execute("CREATE INDEX idx_sentences_parse_revision_id ON sentences(parse_revision_id);")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_sentences_parse_revision_id;")
    op.execute("DROP INDEX IF EXISTS idx_blocks_parse_revision_id;")
    op.execute("ALTER TABLE sentences DROP COLUMN IF EXISTS canonical_node_id;")
    op.execute("ALTER TABLE sentences DROP COLUMN IF EXISTS parse_revision_id;")
    op.execute("ALTER TABLE blocks DROP COLUMN IF EXISTS canonical_node_id;")
    op.execute("ALTER TABLE blocks DROP COLUMN IF EXISTS parse_revision_id;")
    op.execute("DROP INDEX IF EXISTS idx_document_parse_revision_artifacts_revision_id;")
    op.execute("DROP INDEX IF EXISTS idx_document_parse_revisions_document_version;")
    op.execute("DROP TABLE IF EXISTS document_parse_revision_artifacts;")
    op.execute("DROP TABLE IF EXISTS document_parse_revisions;")
