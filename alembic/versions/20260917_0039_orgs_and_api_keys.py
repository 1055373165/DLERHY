"""Authentication and tenancy: orgs, api_keys, documents.org_id.

Revision ID: 20260917_0039
Revises: 20260917_0038
Create Date: 2026-09-17
"""

from alembic import op

revision = "20260917_0039"
down_revision = "20260917_0038"
branch_labels = None
depends_on = None

DEFAULT_ORG_ID = "00000000-0000-4000-8000-000000000001"

UPGRADE_SQL = f"""
CREATE TABLE orgs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO orgs (id, name) VALUES ('{DEFAULT_ORG_ID}', 'default');
CREATE TABLE api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    key_prefix TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL CONSTRAINT ck_api_keys_role CHECK (role IN ('viewer', 'editor', 'admin')),
    last_used_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_api_keys_org_id ON api_keys (org_id);
ALTER TABLE documents ADD COLUMN org_id UUID NOT NULL DEFAULT '{DEFAULT_ORG_ID}' REFERENCES orgs(id) ON DELETE RESTRICT;
ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_file_fingerprint_key;
ALTER TABLE documents ADD CONSTRAINT uq_documents_org_fingerprint UNIQUE (org_id, file_fingerprint)
"""

DOWNGRADE_SQL = """
ALTER TABLE documents DROP CONSTRAINT IF EXISTS uq_documents_org_fingerprint;
DELETE FROM documents WHERE org_id <> '00000000-0000-4000-8000-000000000001';
ALTER TABLE documents ADD CONSTRAINT documents_file_fingerprint_key UNIQUE (file_fingerprint);
ALTER TABLE documents DROP COLUMN IF EXISTS org_id;
DROP TABLE IF EXISTS api_keys;
DROP TABLE IF EXISTS orgs
"""


def _run(sql: str) -> None:
    for statement in sql.strip().split(";\n"):
        if statement.strip():
            op.execute(statement)


def upgrade() -> None:
    _run(UPGRADE_SQL)


def downgrade() -> None:
    _run(DOWNGRADE_SQL)
