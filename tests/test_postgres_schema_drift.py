"""Alembic migrations must produce the schema the ORM declares.

The app tests build their schema with ``Base.metadata.create_all`` on SQLite,
so nothing else notices when a model and the raw-SQL migrations drift apart.
This test migrates a throwaway database on the configured PostgreSQL server
from scratch and compares it with ``Base.metadata``.

Opt-in like the other PostgreSQL tests: ``BOOK_AGENT_RUN_PG_TESTS=1``.
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

import book_agent.domain.models  # noqa: F401  (register all tables on Base.metadata)
from book_agent.core.config import get_settings
from book_agent.infra.db.base import Base

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(
    os.getenv("BOOK_AGENT_RUN_PG_TESTS") == "1",
    "Set BOOK_AGENT_RUN_PG_TESTS=1 to run PostgreSQL integration tests.",
)
class PostgresSchemaDriftTests(unittest.TestCase):
    def setUp(self) -> None:
        server_url = make_url(get_settings().database_url)
        if not server_url.drivername.startswith("postgresql"):
            self.skipTest("Schema drift check requires a PostgreSQL database URL.")
        database_name = f"book_agent_drift_{uuid4().hex[:12]}"
        admin_engine = create_engine(server_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
        self.addCleanup(admin_engine.dispose)
        with admin_engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{database_name}"'))

        def _drop_database() -> None:
            with admin_engine.connect() as conn:
                conn.execute(text(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'))

        self.addCleanup(_drop_database)
        self.database_url = server_url.set(database=database_name)

    def test_migrated_schema_matches_orm_metadata(self) -> None:
        env = dict(os.environ)
        env["BOOK_AGENT_DATABASE_URL"] = self.database_url.render_as_string(hide_password=False)
        migration = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(migration.returncode, 0, migration.stderr[-4000:])

        engine = create_engine(self.database_url)
        self.addCleanup(engine.dispose)
        inspector = inspect(engine)

        database_tables = set(inspector.get_table_names()) - {"alembic_version"}
        orm_tables = set(Base.metadata.tables)
        self.assertEqual(sorted(database_tables - orm_tables), [], "tables created by migrations but not modeled")
        self.assertEqual(sorted(orm_tables - database_tables), [], "modeled tables missing from migrations")

        problems: list[str] = []
        for table_name, table in sorted(Base.metadata.tables.items()):
            database_columns = {column["name"]: column for column in inspector.get_columns(table_name)}
            orm_column_names = {column.name for column in table.columns}
            for name in sorted(set(database_columns) - orm_column_names):
                problems.append(f"{table_name}.{name}: column only in database")
            for column in table.columns:
                database_column = database_columns.get(column.name)
                if database_column is None:
                    problems.append(f"{table_name}.{column.name}: column only in ORM")
                elif bool(database_column["nullable"]) != bool(column.nullable):
                    problems.append(
                        f"{table_name}.{column.name}: nullable db={database_column['nullable']} "
                        f"orm={column.nullable}"
                    )
            database_indexes = {index["name"] for index in inspector.get_indexes(table_name)}
            for index in table.indexes:
                if index.name not in database_indexes:
                    problems.append(f"{table_name}: index {index.name} missing from migrations")
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
