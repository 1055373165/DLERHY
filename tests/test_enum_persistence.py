import re
import unittest
from pathlib import Path
import sys

from sqlalchemy.dialects import postgresql

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.enums import ActionType, BlockType, ExportType, SnapshotType, SourceType, WorkItemStage
from book_agent.domain.models import Block, IssueAction


def _latest_in_list_checks() -> dict[str, tuple[str, str, set[str]]]:
    """Each named `col IN (...)` CHECK as its last upgrade() in alembic/versions leaves it."""
    latest: dict[str, tuple[str, str, set[str]]] = {}
    for path in sorted((ROOT / "alembic" / "versions").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        upgrade = source.split("def downgrade")[0]
        for name, value in re.findall(r'^(_[A-Z_]+) = "(.*)"$', source, re.M):
            upgrade = upgrade.replace("{" + name + "}", value)
        upgrade = re.sub(r'"\s*\n\s*f?"', "", upgrade)  # join implicitly concatenated SQL strings
        pattern = r"CONSTRAINT\s+(\w+)\s+CHECK\s*\(\s*\(?\s*(\w+)\s+IN\s*\(([^)]*)\)"
        for name, column, values in re.findall(pattern, upgrade):
            latest[name] = (column, path.name, set(re.findall(r"'([^']+)'", values)))
    return latest


class EnumPersistenceTests(unittest.TestCase):
    def test_lowercase_enum_persists_value_not_member_name(self) -> None:
        processor = Block.__table__.c.block_type.type.bind_processor(postgresql.dialect())
        self.assertIsNotNone(processor)
        assert processor is not None

        self.assertEqual(processor(BlockType.HEADING), "heading")

    def test_uppercase_enum_preserves_declared_value(self) -> None:
        processor = IssueAction.__table__.c.action_type.type.bind_processor(postgresql.dialect())
        self.assertIsNotNone(processor)
        assert processor is not None

        self.assertEqual(processor(ActionType.RERUN_PACKET), "RERUN_PACKET")

    def test_every_export_and_source_type_check_allows_every_enum_value(self) -> None:
        # Several tables carry their own copy of these CHECKs; a migration that
        # extends one copy and misses another breaks inserts on Postgres only.
        latest = _latest_in_list_checks()
        expected = {"export_type": ExportType, "source_type": SourceType}
        checked = []
        for name, (column, migration, values) in latest.items():
            if column not in expected:
                continue
            checked.append(name)
            missing = {member.value for member in expected[column]} - values
            self.assertFalse(missing, f"{name} (last set in {migration}) does not allow {sorted(missing)}")
        self.assertGreaterEqual(len(checked), 4)

    def test_latest_snapshot_constraint_migration_covers_all_snapshot_types(self) -> None:
        migration_path = (
            ROOT / "alembic" / "versions" / "20260410_0017_memory_snapshot_type_chapter_translation_memory.py"
        )
        migration_text = migration_path.read_text(encoding="utf-8")

        for snapshot_type in SnapshotType:
            self.assertIn(f"'{snapshot_type.value}'", migration_text)

    def test_latest_work_item_stage_constraint_migration_matches_work_item_stages(self) -> None:
        migration_path = ROOT / "alembic" / "versions" / "20260917_0035_agent_harness_ledger.py"
        migration_text = migration_path.read_text(encoding="utf-8")

        for stage in WorkItemStage:
            self.assertIn(f"'{stage.value}'", migration_text)
        self.assertNotIn("'repair',", migration_text)

    def test_review_issue_event_constraints_match_enums(self) -> None:
        from book_agent.domain.enums import IssueEventKind, IssueStatus

        migration_path = ROOT / "alembic" / "versions" / "20260917_0036_review_issue_versioning.py"
        migration_text = migration_path.read_text(encoding="utf-8")

        for kind in IssueEventKind:
            self.assertIn(f"'{kind.value}'", migration_text)
        for status in IssueStatus:
            self.assertIn(f"'{status.value}'", migration_text)


if __name__ == "__main__":
    unittest.main()
