import unittest
from enum import StrEnum

from book_agent.domain.enums import (
    ChapterRunPhase,
    ChapterRunStatus,
    PacketTaskAction,
    PacketTaskStatus,
    ReviewSessionStatus,
    ReviewTerminalityState,
)


class RuntimeV2EnumsTests(unittest.TestCase):
    def test_runtime_enums_are_str_enum(self) -> None:
        self.assertTrue(issubclass(ChapterRunPhase, StrEnum))
        self.assertTrue(issubclass(ChapterRunStatus, StrEnum))
        self.assertTrue(issubclass(PacketTaskAction, StrEnum))
        self.assertTrue(issubclass(PacketTaskStatus, StrEnum))
        self.assertTrue(issubclass(ReviewSessionStatus, StrEnum))
        self.assertTrue(issubclass(ReviewTerminalityState, StrEnum))

    def test_runtime_enum_values_are_stable_and_string_comparable(self) -> None:
        self.assertEqual(ChapterRunPhase.PACKETIZE.value, "packetize")
        self.assertEqual(ChapterRunPhase.COMPLETE.value, "complete")
        self.assertEqual(ChapterRunStatus.ACTIVE.value, "active")
        self.assertEqual(PacketTaskAction.RETRANSLATE.value, "retranslate")
        self.assertEqual(ReviewSessionStatus.ACTIVE.value, "active")
        self.assertEqual(ReviewTerminalityState.APPROVED.value, "approved")
        self.assertTrue(ChapterRunStatus.ACTIVE == "active")

    def test_invalid_runtime_enum_value_raises(self) -> None:
        with self.assertRaises(ValueError):
            ChapterRunStatus("not-a-status")
