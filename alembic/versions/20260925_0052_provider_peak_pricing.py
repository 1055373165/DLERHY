"""Provider peak pricing: a multiplier on the stored prices outside an off-peak window (UTC).

Revision ID: 20260925_0052
Revises: 20260925_0051
Create Date: 2026-09-25

DeepSeek charges twice its off-peak price at peak. A credential keeps its off-peak prices in the
existing price columns; these two columns say how much more a call costs outside the window.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260925_0052"
down_revision = "20260925_0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("provider_credentials", sa.Column("peak_price_multiplier", sa.Numeric(6, 3), nullable=True))
    op.add_column("provider_credentials", sa.Column("off_peak_window_utc", sa.String(11), nullable=True))


def downgrade() -> None:
    op.drop_column("provider_credentials", "off_peak_window_utc")
    op.drop_column("provider_credentials", "peak_price_multiplier")
