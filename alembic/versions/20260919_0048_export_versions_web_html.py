"""Export versions of translated web pages: the export_versions export_type CHECK allows 'web_html'.

Revision ID: 20260919_0048
Revises: 20260919_0047
Create Date: 2026-09-19

0046 extended exports.export_type but missed the copy of the constraint on
export_versions, so exporting a web page failed when recording its version.
"""

from alembic import op
from sqlalchemy import text

revision = "20260919_0048"
down_revision = "20260919_0047"
branch_labels = None
depends_on = None

_EXPORT_TYPES = "'bilingual_html', 'merged_html', 'merged_markdown', 'rebuilt_epub', 'rebuilt_pdf', 'zh_epub', 'review_package'"


def upgrade() -> None:
    op.execute("ALTER TABLE export_versions DROP CONSTRAINT IF EXISTS export_versions_export_type_check;")
    op.execute(
        "ALTER TABLE export_versions ADD CONSTRAINT export_versions_export_type_check "
        f"CHECK (export_type IN ({_EXPORT_TYPES}, 'web_html'));"
    )


def downgrade() -> None:
    count = op.get_bind().execute(text("SELECT count(*) FROM export_versions WHERE export_type = 'web_html'")).scalar_one()
    if count:
        raise RuntimeError(f"{count} web_html export versions exist; delete them before downgrading.")
    op.execute("ALTER TABLE export_versions DROP CONSTRAINT IF EXISTS export_versions_export_type_check;")
    op.execute(
        "ALTER TABLE export_versions ADD CONSTRAINT export_versions_export_type_check "
        f"CHECK (export_type IN ({_EXPORT_TYPES}));"
    )
