"""Web pages as a source type, and the translated web page as an export type.

Revision ID: 20260919_0046
Revises: 20260919_0045
Create Date: 2026-09-19
"""

from alembic import op
from sqlalchemy import text

revision = "20260919_0046"
down_revision = "20260919_0045"
branch_labels = None
depends_on = None

_EXPORT_TYPES = "'bilingual_html', 'merged_html', 'merged_markdown', 'rebuilt_epub', 'rebuilt_pdf', 'zh_epub', 'review_package'"


def upgrade() -> None:
    op.execute("ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_source_type_check;")
    op.execute(
        "ALTER TABLE documents ADD CONSTRAINT documents_source_type_check "
        "CHECK (source_type IN ('epub', 'pdf_text', 'pdf_scan', 'pdf_mixed', 'web'));"
    )
    op.execute("ALTER TABLE exports DROP CONSTRAINT IF EXISTS exports_export_type_check;")
    op.execute(
        f"ALTER TABLE exports ADD CONSTRAINT exports_export_type_check CHECK (export_type IN ({_EXPORT_TYPES}, 'web_html'));"
    )


def downgrade() -> None:
    bind = op.get_bind()
    web_documents = bind.execute(text("SELECT count(*) FROM documents WHERE source_type = 'web'")).scalar_one()
    web_exports = bind.execute(text("SELECT count(*) FROM exports WHERE export_type = 'web_html'")).scalar_one()
    if web_documents or web_exports:
        raise RuntimeError(
            f"{web_documents} web documents and {web_exports} web_html exports exist; delete them before downgrading."
        )
    op.execute("ALTER TABLE exports DROP CONSTRAINT IF EXISTS exports_export_type_check;")
    op.execute(f"ALTER TABLE exports ADD CONSTRAINT exports_export_type_check CHECK (export_type IN ({_EXPORT_TYPES}));")
    op.execute("ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_source_type_check;")
    op.execute(
        "ALTER TABLE documents ADD CONSTRAINT documents_source_type_check "
        "CHECK (source_type IN ('epub', 'pdf_text', 'pdf_scan', 'pdf_mixed'));"
    )
