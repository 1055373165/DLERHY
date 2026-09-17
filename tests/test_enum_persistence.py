import unittest
from pathlib import Path
import sys

from sqlalchemy.dialects import postgresql

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.enums import ActionType, BlockType, ExportType, SnapshotType, WorkItemStage
from book_agent.domain.models import Block, IssueAction


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

    def test_latest_export_constraint_migration_covers_all_export_types(self) -> None:
        migration_path = ROOT / "alembic" / "versions" / "20260410_0018_expand_exports_export_type_check.py"
        migration_text = migration_path.read_text(encoding="utf-8")

        for export_type in ExportType:
            self.assertIn(f"'{export_type.value}'", migration_text)

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


if __name__ == "__main__":
    unittest.main()
