"""Parse revisions of web page documents: the parse revision source_type CHECK allows 'web'.

Revision ID: 20260919_0047
Revises: 20260919_0046
Create Date: 2026-09-19

0046 extended documents.source_type but missed the copy of the constraint on
document_parse_revisions, so bootstrapping a web page failed on insert.
"""

from alembic import op
from sqlalchemy import text

revision = "20260919_0047"
down_revision = "20260919_0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE document_parse_revisions DROP CONSTRAINT IF EXISTS document_parse_revisions_source_type_check;")
    op.execute(
        "ALTER TABLE document_parse_revisions ADD CONSTRAINT document_parse_revisions_source_type_check "
        "CHECK (source_type IN ('epub', 'pdf_text', 'pdf_scan', 'pdf_mixed', 'web'));"
    )


def downgrade() -> None:
    count = op.get_bind().execute(
        text("SELECT count(*) FROM document_parse_revisions WHERE source_type = 'web'")
    ).scalar_one()
    if count:
        raise RuntimeError(f"{count} web parse revisions exist; delete the web documents before downgrading.")
    op.execute("ALTER TABLE document_parse_revisions DROP CONSTRAINT IF EXISTS document_parse_revisions_source_type_check;")
    op.execute(
        "ALTER TABLE document_parse_revisions ADD CONSTRAINT document_parse_revisions_source_type_check "
        "CHECK (source_type IN ('epub', 'pdf_text', 'pdf_scan', 'pdf_mixed'));"
    )
