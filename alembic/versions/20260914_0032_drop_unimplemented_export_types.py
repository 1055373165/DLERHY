"""Drop export types that no exporter implements.

Revision ID: 20260914_0032
Revises: 20260914_0031
Create Date: 2026-09-14

bilingual_markdown, zh_pdf and jsonl were allowed by the exports CHECK and
the ExportType enum but every attempt to produce them failed at the export
gate, so no rows can carry them. The upgrade refuses to run if any do.
"""

from alembic import op
from sqlalchemy import text

revision = "20260914_0032"
down_revision = "20260914_0031"
branch_labels = None
depends_on = None

_REMOVED = ("bilingual_markdown", "zh_pdf", "jsonl")


def upgrade() -> None:
    bind = op.get_bind()
    count = bind.execute(
        text("SELECT count(*) FROM exports WHERE export_type IN ('bilingual_markdown', 'zh_pdf', 'jsonl')")
    ).scalar_one()
    if count:
        raise RuntimeError(f"{count} exports rows use removed export types {_REMOVED}; resolve them before upgrading.")
    op.execute("ALTER TABLE exports DROP CONSTRAINT IF EXISTS exports_export_type_check;")
    op.execute(
        "ALTER TABLE exports ADD CONSTRAINT exports_export_type_check CHECK (export_type IN ("
        "'bilingual_html', 'merged_html', 'merged_markdown', 'rebuilt_epub', 'rebuilt_pdf', 'zh_epub', 'review_package'"
        "));"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE exports DROP CONSTRAINT IF EXISTS exports_export_type_check;")
    op.execute(
        "ALTER TABLE exports ADD CONSTRAINT exports_export_type_check CHECK (export_type IN ("
        "'bilingual_html', 'bilingual_markdown', 'merged_html', 'merged_markdown', 'rebuilt_epub', "
        "'rebuilt_pdf', 'zh_epub', 'zh_pdf', 'review_package', 'jsonl'"
        "));"
    )
