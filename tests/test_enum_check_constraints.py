"""Enum-valued columns carry CHECK constraints generated from their enums."""

import unittest
from uuid import uuid4

from sqlalchemy import CheckConstraint, create_engine, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.exc import IntegrityError

import book_agent.domain.models  # noqa: F401  (register all tables on Base.metadata)
from book_agent.domain.enums import JobType, WorkItemStage
from book_agent.infra.db.base import Base, enum_check_constraint_name


class EnumCheckConstraintTests(unittest.TestCase):
    def test_every_enum_column_has_a_check_listing_the_enum_values(self) -> None:
        for table in Base.metadata.tables.values():
            checks = {c.name: str(c.sqltext) for c in table.constraints if isinstance(c, CheckConstraint)}
            for column in table.columns:
                if not isinstance(column.type, SAEnum):
                    continue
                name = enum_check_constraint_name(table.name, column.name)
                self.assertIn(name, checks, f"{table.name}.{column.name}")
                for value in column.type.enums:
                    self.assertIn(f"'{value}'", checks[name])

    def test_database_rejects_values_outside_the_enum(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine, tables=[Base.metadata.tables["job_runs"]])
        insert = text(
            "INSERT INTO job_runs (id, job_type, scope_type, scope_id, status, retry_count, error_json, created_at) "
            "VALUES (:id, :job_type, 'document', :scope_id, :status, 0, '{}', CURRENT_TIMESTAMP)"
        )
        with engine.begin() as conn:
            conn.execute(insert, {"id": uuid4().hex, "job_type": JobType.INGEST.value, "scope_id": uuid4().hex, "status": "queued"})
        with self.assertRaises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(insert, {"id": uuid4().hex, "job_type": JobType.INGEST.value, "scope_id": uuid4().hex, "status": "exploded"})

    def test_generated_check_follows_the_enum(self) -> None:
        stage_check = next(
            c
            for c in Base.metadata.tables["work_items"].constraints
            if isinstance(c, CheckConstraint) and c.name == "work_items_stage_check"
        )
        self.assertEqual(
            str(stage_check.sqltext),
            "stage IN (" + ", ".join(f"'{stage.value}'" for stage in WorkItemStage) + ")",
        )


if __name__ == "__main__":
    unittest.main()
