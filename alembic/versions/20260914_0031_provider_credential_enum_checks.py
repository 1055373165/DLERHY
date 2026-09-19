"""Add enum CHECKs to provider_credentials.

Revision ID: 20260914_0031
Revises: 20260914_0030
Create Date: 2026-09-14

20260503_0029 created provider_kind and last_test_status as non-native enums
without the CHECK constraints every other enum column has. The ORM now
generates ``<table>_<column>_check`` constraints from the Python enums and the
PostgreSQL drift test compares them with the migrated schema.
"""

from alembic import op


revision = "20260914_0031"
down_revision = "20260914_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE provider_credentials ADD CONSTRAINT provider_credentials_provider_kind_check "
        "CHECK (provider_kind IN ('openai_compatible', 'echo'));"
    )
    op.execute(
        "ALTER TABLE provider_credentials ADD CONSTRAINT provider_credentials_last_test_status_check "
        "CHECK (last_test_status IN ('unknown', 'ok', 'failed'));"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE provider_credentials DROP CONSTRAINT IF EXISTS provider_credentials_last_test_status_check;")
    op.execute("ALTER TABLE provider_credentials DROP CONSTRAINT IF EXISTS provider_credentials_provider_kind_check;")
