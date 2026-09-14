"""frontend/src/lib/api-types.gen.ts must match the backend OpenAPI schema."""

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FrontendApiTypesTests(unittest.TestCase):
    def test_generated_api_types_are_up_to_date(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "generate_frontend_api_types.py"), "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
