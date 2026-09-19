"""Export history: exports.version and the append-only export_versions table.

Revision ID: 20260917_0038
Revises: 20260917_0037
Create Date: 2026-09-17
"""

from alembic import op

revision = "20260917_0038"
down_revision = "20260917_0037"
branch_labels = None
depends_on = None


UPGRADE_SQL = """
ALTER TABLE exports ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
CREATE TABLE export_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    export_id UUID NOT NULL REFERENCES exports(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    export_type TEXT NOT NULL CONSTRAINT export_versions_export_type_check CHECK (export_type IN ('bilingual_html', 'merged_html', 'merged_markdown', 'rebuilt_epub', 'rebuilt_pdf', 'zh_epub', 'review_package')),
    version INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    manifest_path TEXT,
    content_sha256 TEXT,
    byte_count BIGINT,
    input_version_bundle_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_export_versions_export_version UNIQUE (export_id, version)
);
CREATE INDEX idx_export_versions_document_created ON export_versions (document_id, created_at);
INSERT INTO export_versions (export_id, document_id, export_type, version, file_path, content_sha256, byte_count, input_version_bundle_json, created_at)
    SELECT id, document_id, export_type, 1, file_path, content_sha256, byte_count, input_version_bundle_json, updated_at
    FROM exports;
"""

DOWNGRADE_SQL = """
DROP TABLE IF EXISTS export_versions;
ALTER TABLE exports DROP COLUMN IF EXISTS version;
"""


def _run(sql: str) -> None:
    for statement in sql.strip().split(";\n"):
        if statement.strip():
            op.execute(statement)


def upgrade() -> None:
    _run(UPGRADE_SQL)


def downgrade() -> None:
    _run(DOWNGRADE_SQL)
