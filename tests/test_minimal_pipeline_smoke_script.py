import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_minimal_pipeline_smoke.py"


class MinimalPipelineSmokeScriptTests(unittest.TestCase):
    def test_script_runs_all_cases_and_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "smoke"
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--case", "all", "--output-dir", str(output_dir)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            report_path = Path(payload["report_path"])
            self.assertTrue(report_path.exists())
            report_payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(len(report_payload), 2)
            self.assertEqual({entry["case"] for entry in report_payload}, {"epub", "pdf"})
            self.assertTrue(all(entry["document_status"] == "exported" for entry in report_payload))
            self.assertTrue(all(Path(entry["chapter_export_path"]).exists() for entry in report_payload))
