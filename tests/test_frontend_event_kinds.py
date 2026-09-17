"""The frontend subscribes to named SSE events one by one; its list must match EVENT_KINDS."""

import re
import unittest
from pathlib import Path

from book_agent.domain.event_kinds import EVENT_KINDS

ROOT = Path(__file__).resolve().parents[1]


class FrontendEventKindsTests(unittest.TestCase):
    def test_run_event_kinds_match_the_backend_catalog(self) -> None:
        source = (ROOT / "frontend" / "src" / "lib" / "runEvents.ts").read_text(encoding="utf-8")
        block = source[source.index("export const RUN_EVENT_KINDS") : source.index("] as const")]
        frontend_kinds = set(re.findall(r'"([a-z_.]+)"', block))
        self.assertEqual(frontend_kinds, set(EVENT_KINDS))


if __name__ == "__main__":
    unittest.main()
