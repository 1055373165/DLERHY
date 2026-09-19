"""Parse-revision fork, sentence level: retired sentences and sentence_lineage.

Revision ID: 20260917_0037
Revises: 20260917_0036
Create Date: 2026-09-17
"""

from alembic import op

revision = "20260917_0037"
down_revision = "20260917_0036"
branch_labels = None
depends_on = None


UPGRADE_SQL = """
ALTER TABLE sentences ADD COLUMN retired_by_revision_id UUID REFERENCES document_parse_revisions(id) ON DELETE SET NULL;
ALTER TABLE sentences DROP CONSTRAINT IF EXISTS sentences_block_id_ordinal_in_block_key;
ALTER TABLE sentences DROP CONSTRAINT IF EXISTS uq_sentences_block_ordinal;
CREATE UNIQUE INDEX uq_sentences_block_ordinal_active ON sentences (block_id, ordinal_in_block)
    WHERE retired_by_revision_id IS NULL;

CREATE TABLE sentence_lineage (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    parse_revision_id UUID NOT NULL REFERENCES document_parse_revisions(id) ON DELETE CASCADE,
    from_sentence_id UUID NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
    to_sentence_id UUID REFERENCES sentences(id) ON DELETE CASCADE,
    relation TEXT NOT NULL CONSTRAINT ck_sentence_lineage_relation CHECK (relation IN ('same', 'split', 'merge', 'removed')),
    similarity NUMERIC(4,3),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_sentence_lineage_from ON sentence_lineage (from_sentence_id);
CREATE INDEX idx_sentence_lineage_to ON sentence_lineage (to_sentence_id);
"""

# Downgrade deletes retired sentences: the old unique constraint cannot hold them.
DOWNGRADE_SQL = """
DROP TABLE IF EXISTS sentence_lineage;
DELETE FROM sentences WHERE retired_by_revision_id IS NOT NULL;
DROP INDEX IF EXISTS uq_sentences_block_ordinal_active;
ALTER TABLE sentences ADD CONSTRAINT sentences_block_id_ordinal_in_block_key UNIQUE (block_id, ordinal_in_block);
ALTER TABLE sentences DROP COLUMN IF EXISTS retired_by_revision_id;
"""


def _run(sql: str) -> None:
    for statement in sql.strip().split(";\n"):
        if statement.strip():
            op.execute(statement)


def upgrade() -> None:
    _run(UPGRADE_SQL)


def downgrade() -> None:
    _run(DOWNGRADE_SQL)
