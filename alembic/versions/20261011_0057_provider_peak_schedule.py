"""Provider peak schedule: peak hours by weekday in UTC, replacing the single off-peak window.

Revision ID: 20261011_0057
Revises: 20261008_0056
Create Date: 2026-10-11

DeepSeek's pricing page (checked 2026-10-11) sets the peak at Monday to Friday 01:00-04:00 and
06:00-10:00 UTC, every other hour, weekends included, being off-peak. One off-peak window cannot
say that: the stored "16:30-00:30" priced 10:00-16:30 UTC and weekends as peak, which overstated
the recorded cost by about 23% over two weeks (docs/specs/cost-governance/SPEC.md).
"""

import sqlalchemy as sa
from alembic import op

revision = "20261011_0057"
down_revision = "20261008_0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("provider_credentials", sa.Column("peak_schedule_utc", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("provider_credentials", "peak_schedule_utc")
