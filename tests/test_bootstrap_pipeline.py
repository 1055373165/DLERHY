import tempfile
import unittest
import zipfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.enums import ChapterStatus, DocumentStatus, PacketSentenceRole, SnapshotType
from book_agent.orchestrator.bootstrap import BootstrapOrchestrator


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
    <p>First paragraph. Second sentence.</p>
    <blockquote>A quoted paragraph.</blockquote>
  </body>
</html>
"""


class BootstrapPipelineTests(unittest.TestCase):
    def test_bootstrap_pipeline_builds_packets_and_briefs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            epub_path = Path(tmpdir) / "sample.epub"
            with zipfile.ZipFile(epub_path, "w") as archive:
                archive.writestr("mimetype", "application/epub+zip")
                archive.writestr("META-INF/container.xml", CONTAINER_XML)
                archive.writestr("OEBPS/content.opf", CONTENT_OPF)
                archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
                archive.writestr("OEBPS/chapter1.xhtml", CHAPTER_XHTML)

            result = BootstrapOrchestrator().bootstrap_epub(epub_path)

        self.assertEqual(result.document.status, DocumentStatus.ACTIVE)
        self.assertEqual(len(result.chapters), 1)
        self.assertEqual(result.chapters[0].status, ChapterStatus.PACKET_BUILT)
        self.assertEqual(len(result.blocks), 3)
        self.assertEqual(len(result.sentences), 4)
        self.assertEqual(result.book_profile.book_type.value, "business")
        self.assertEqual(len(result.translation_packets), 3)
        self.assertTrue(any(snapshot.snapshot_type == SnapshotType.CHAPTER_BRIEF for snapshot in result.memory_snapshots))
        self.assertTrue(any(mapping.role == PacketSentenceRole.CURRENT for mapping in result.packet_sentence_maps))
        self.assertGreaterEqual(len(result.job_runs), 6)


if __name__ == "__main__":
    unittest.main()

