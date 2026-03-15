import unittest
from pathlib import Path
import sys

from sqlalchemy.dialects import postgresql

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.enums import ActionType, BlockType
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


if __name__ == "__main__":
    unittest.main()
