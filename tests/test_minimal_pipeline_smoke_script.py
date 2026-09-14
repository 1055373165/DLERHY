import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from tests.golden_pdfs.fixtures import make_clean_book


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_minimal_pipeline_smoke.py"

CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml" />
  </rootfiles>
</container>
"""

CONTENT_OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package version="3.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Minimal Pipeline Book</dc:title>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav" />
    <item id="chap1" href="chapter1.xhtml" media-type="application/xhtml+xml" />
  </manifest>
  <spine>
    <itemref idref="chap1" />
  </spine>
</package>
"""

NAV_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <body>
    <nav epub:type="toc"><ol><li><a href="chapter1.xhtml">Chapter One</a></li></ol></nav>
  </body>
</html>
"""

CHAPTER_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1>Chapter One</h1>
    <p>Agents need memory to stay coherent. Context windows are finite.</p>
    <p>Retrieval keeps the working set small.</p>
  </body>
</html>
"""


def _write_samples(sample_dir: Path) -> None:
    sample_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(sample_dir / "minimal_pipeline.epub", "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", CONTAINER_XML)
        archive.writestr("OEBPS/content.opf", CONTENT_OPF)
        archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
        archive.writestr("OEBPS/chapter1.xhtml", CHAPTER_XHTML)
    (sample_dir / "minimal_pipeline.pdf").write_bytes(make_clean_book())


class MinimalPipelineSmokeScriptTests(unittest.TestCase):
    def test_script_runs_all_cases_and_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_dir = Path(tmpdir) / "samples"
            _write_samples(sample_dir)
            output_dir = Path(tmpdir) / "smoke"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--case",
                    "all",
                    "--sample-dir",
                    str(sample_dir),
                    "--output-dir",
                    str(output_dir),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            report_path = Path(payload["report_path"])
            self.assertTrue(report_path.exists())
            report_payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual({entry["case"] for entry in report_payload}, {"epub", "pdf"})
            for entry in report_payload:
                self.assertEqual(entry["bilingual_status"], "exported", entry)
                self.assertEqual(entry["merged_status"], "exported", entry)
                self.assertTrue(Path(entry["chapter_export_path"]).exists())

    def test_script_fails_when_samples_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--case",
                    "epub",
                    "--sample-dir",
                    str(Path(tmpdir) / "missing"),
                    "--output-dir",
                    str(Path(tmpdir) / "smoke"),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )

            self.assertEqual(result.returncode, 1, msg=result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertIn("sample not found", payload["results"][0]["error"])


if __name__ == "__main__":
    unittest.main()
