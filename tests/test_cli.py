import io
import json
import os
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
os.environ.setdefault("BOOK_AGENT_TRANSLATION_BACKEND", "echo")
os.environ.setdefault("BOOK_AGENT_TRANSLATION_MODEL", "echo-worker")
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.cli import main
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine


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
    <dc:title>Business Strategy Handbook</dc:title>
    <dc:creator>Test Author</dc:creator>
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
    <nav epub:type="toc">
      <ol>
        <li><a href="chapter1.xhtml">Chapter One</a></li>
      </ol>
    </nav>
  </body>
</html>
"""

CHAPTER_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>Pricing power matters. Strategy compounds.</p>
    <blockquote>A quoted paragraph.</blockquote>
  </body>
</html>
"""


class CliWorkflowTests(unittest.TestCase):
    def test_cli_happy_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            epub_path = Path(tmpdir) / "sample.epub"
            export_root = Path(tmpdir) / "exports"
            database_path = Path(tmpdir) / "book-agent.db"
            database_url = f"sqlite+pysqlite:///{database_path}"

            with zipfile.ZipFile(epub_path, "w") as archive:
                archive.writestr("mimetype", "application/epub+zip")
                archive.writestr("META-INF/container.xml", CONTAINER_XML)
                archive.writestr("OEBPS/content.opf", CONTENT_OPF)
                archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
                archive.writestr("OEBPS/chapter1.xhtml", CHAPTER_XHTML)

            engine = build_engine(database_url)
            Base.metadata.create_all(engine)
            self.addCleanup(engine.dispose)

            bootstrap_data = self._run_cli(
                "--database-url",
                database_url,
                "--export-root",
                str(export_root),
                "bootstrap",
                "--source-path",
                str(epub_path),
            )
            document_id = bootstrap_data["document_id"]

            translate_data = self._run_cli(
                "--database-url",
                database_url,
                "--export-root",
                str(export_root),
                "translate",
                "--document-id",
                document_id,
            )
            self.assertEqual(translate_data["translated_packet_count"], 3)

            review_data = self._run_cli(
                "--database-url",
                database_url,
                "--export-root",
                str(export_root),
                "review",
                "--document-id",
                document_id,
            )
            self.assertEqual(review_data["total_issue_count"], 0)

            export_data = self._run_cli(
                "--database-url",
                database_url,
                "--export-root",
                str(export_root),
                "export",
                "--document-id",
                document_id,
                "--export-type",
                "bilingual_html",
            )
            self.assertEqual(export_data["document_status"], "exported")
            self.assertTrue(Path(export_data["chapter_results"][0]["file_path"]).exists())

    def _run_cli(self, *argv: str) -> dict:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = main(list(argv))
        self.assertEqual(exit_code, 0)
        return json.loads(stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
