"""Add exports integrity columns + file_path CHECK + legacy backfill.

Revision ID: 20260422_0028
Revises: 20260421_0027
Create Date: 2026-04-22 00:40:00

M1.2 production guardrail. Three coordinated changes, in strict order:

1. ADD COLUMNs content_sha256/byte_count/last_verified_at/stale_reason
   — payload for the verifier and the 410-Gone download path.

2. Legacy backfill: any existing row whose file_path points at an OS
   tempdir (the M0 incident pattern) is rewritten to a sentinel
   `unrecoverable://<basename>` and has stale_reason set. Keeping the
   rows preserves audit trail; the sentinel path is deliberately
   non-filesystem so a naive reader can't be tricked into serving
   something bogus.

3. ADD CONSTRAINT blocking NEW tempdir-rooted INSERTs. Ordering is
   load-bearing: the backfill must run BEFORE the CHECK, otherwise
   the constraint rejects the pre-existing rows and the migration
   fails.

Why LIKE rather than regex: LIKE is ANSI SQL and works in sqlite
(smoke harness). Postgres `!~` would work in prod but fail under the
sqlite path we rely on for smoke schema parity.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260422_0028"
down_revision = "20260421_0027"
branch_labels = None
depends_on = None


_TEMPDIR_CLAUSE = (
    "file_path LIKE '/var/folders/%' "
    "OR file_path LIKE '/private/var/folders/%' "
    "OR file_path LIKE '/tmp/%' "
    "OR file_path LIKE '/private/tmp/%'"
)


def upgrade() -> None:
    op.add_column(
        "exports",
        sa.Column("content_sha256", sa.Text(), nullable=True),
    )
    op.add_column(
        "exports",
        sa.Column("byte_count", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "exports",
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "exports",
        sa.Column("stale_reason", sa.Text(), nullable=True),
    )

    # Mark legacy tempdir rows stale.
    op.execute(
        sa.text(
            f"UPDATE exports SET "
            f"  stale_reason = COALESCE(stale_reason, 'tempdir_pollution_m0') "
            f"WHERE {_TEMPDIR_CLAUSE}"
        )
    )

    # Rewrite file_path to the `unrecoverable://<basename>` sentinel.
    # Dialect-split because sqlite doesn't have regex.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                f"UPDATE exports SET "
                f"  file_path = 'unrecoverable://' || regexp_replace(file_path, '^.*/', '') "
                f"WHERE {_TEMPDIR_CLAUSE}"
            )
        )
    else:
        rows = bind.execute(
            sa.text(f"SELECT id, file_path FROM exports WHERE {_TEMPDIR_CLAUSE}")
        ).all()
        for row_id, fp in rows:
            basename = fp.rsplit("/", 1)[-1]
            bind.execute(
                sa.text("UPDATE exports SET file_path = :new WHERE id = :id"),
                {"new": f"unrecoverable://{basename}", "id": row_id},
            )

    # Constraint last. Every row now has a canonical path or the
    # sentinel — neither matches the tempdir patterns.
    op.create_check_constraint(
        "exports_file_path_no_tempdir_check",
        "exports",
        "file_path NOT LIKE '/var/folders/%' "
        "AND file_path NOT LIKE '/private/var/folders/%' "
        "AND file_path NOT LIKE '/tmp/%' "
        "AND file_path NOT LIKE '/private/tmp/%'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "exports_file_path_no_tempdir_check",
        "exports",
        type_="check",
    )
    op.drop_column("exports", "stale_reason")
    op.drop_column("exports", "last_verified_at")
    op.drop_column("exports", "byte_count")
    op.drop_column("exports", "content_sha256")
