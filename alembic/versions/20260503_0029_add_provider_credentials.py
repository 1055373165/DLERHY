"""add provider_credentials

Revision ID: 20260503_0029
Revises: 20260422_0028
Create Date: 2026-05-03
"""

from alembic import op
import sqlalchemy as sa


revision = "20260503_0029"
down_revision = "20260422_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_credentials",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "provider_kind",
            sa.Enum(
                "openai_compatible",
                "echo",
                name="provider_kind",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("api_key_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("streaming", sa.Boolean(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("max_retries", sa.Integer(), nullable=False),
        sa.Column("retry_backoff_seconds_x10", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "last_test_status",
            sa.Enum(
                "unknown",
                "ok",
                "failed",
                name="provider_test_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_message", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_provider_credentials_one_active",
        "provider_credentials",
        ["is_active"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_provider_credentials_one_active",
        table_name="provider_credentials",
        postgresql_where=sa.text("is_active"),
    )
    op.drop_table("provider_credentials")
