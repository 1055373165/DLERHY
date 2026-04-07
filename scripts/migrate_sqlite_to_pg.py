"""Migrate all data from SQLite to PostgreSQL.

Usage:
    python scripts/migrate_sqlite_to_pg.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import create_engine, text, MetaData
from sqlalchemy.orm import Session

SQLITE_URL = "sqlite+pysqlite:///./artifacts/book-agent.db"
PG_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/book_agent"

BATCH_SIZE = 500


def _get_check_constraints(conn):
    """Get all user-defined CHECK constraints (not NOT NULL)."""
    rows = conn.execute(text("""
        SELECT con.conname, con.conrelid::regclass::text AS table_name,
               pg_get_constraintdef(con.oid) AS definition
        FROM pg_constraint con
        WHERE con.contype = 'c'
          AND con.connamespace = 'public'::regnamespace
          AND con.conname NOT LIKE '%%_not_null'
    """)).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def migrate():
    sqlite_engine = create_engine(SQLITE_URL)
    pg_engine = create_engine(PG_URL)

    # First: truncate all tables (in case of partial previous run)
    pg_meta = MetaData()
    pg_meta.reflect(bind=pg_engine)

    tables_to_migrate = [
        t for t in pg_meta.sorted_tables if t.name != "alembic_version"
    ]

    print(f"Migrating {len(tables_to_migrate)} tables from SQLite → PostgreSQL\n")

    with pg_engine.connect() as conn:
        # Truncate all tables in reverse FK order
        conn.execute(text("SET session_replication_role = 'replica'"))
        for t in reversed(tables_to_migrate):
            conn.execute(text(f'TRUNCATE TABLE "{t.name}" CASCADE'))
        conn.commit()

        # Drop all CHECK constraints temporarily
        checks = _get_check_constraints(conn)
        print(f"  Dropping {len(checks)} CHECK constraints for bulk load...")
        for cname, tname, _ in checks:
            conn.execute(text(f'ALTER TABLE {tname} DROP CONSTRAINT "{cname}"'))
        conn.commit()

    # Now load data
    with Session(pg_engine) as pg_session:
        pg_session.execute(text("SET session_replication_role = 'replica'"))

        for pg_table in tables_to_migrate:
            name = pg_table.name

            # Read from SQLite
            with sqlite_engine.connect() as sqlite_conn:
                result = sqlite_conn.execute(text(f'SELECT * FROM "{name}"'))
                col_names = list(result.keys())
                rows = result.fetchall()

            if not rows:
                print(f"  SKIP {name} (empty)")
                continue

            # Only use columns that exist in PG schema
            pg_col_names = {c.name for c in pg_table.columns}
            shared_cols = [c for c in col_names if c in pg_col_names]

            total = len(rows)
            for i in range(0, total, BATCH_SIZE):
                batch = rows[i : i + BATCH_SIZE]
                row_dicts = []
                for row in batch:
                    d = dict(zip(col_names, row))
                    row_dicts.append({k: d[k] for k in shared_cols})
                pg_session.execute(pg_table.insert(), row_dicts)

            pg_session.flush()
            print(f"  OK   {name}: {total} rows")

        pg_session.execute(text("SET session_replication_role = 'origin'"))
        pg_session.commit()

    # Re-add CHECK constraints
    with pg_engine.connect() as conn:
        print(f"\n  Restoring {len(checks)} CHECK constraints...")
        for cname, tname, definition in checks:
            conn.execute(
                text(f'ALTER TABLE {tname} ADD CONSTRAINT "{cname}" {definition} NOT VALID')
            )
        conn.commit()

    print("\nMigration complete!")


if __name__ == "__main__":
    migrate()
