# ruff: noqa: E402

import tempfile
import unittest
import zipfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.structure.epub import EPUBParser


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
    <dc:title>Test Book</dc:title>
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

ENTITY_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>Resolution is 2 &times; 4.</p>
  </body>
</html>
"""

MALFORMED_HTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>Tokens starting with <think>. Keep the token literal.</p>
    <pre>while value < limit:</pre>
  </body>
</html>
"""

STRUCTURED_ARTIFACT_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:m="http://www.w3.org/1998/Math/MathML">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <div id="fig-1" class="browsable-container figure-container">
      <img src="images/agent-loop.png" alt="Agent loop architecture" />
      <h5>Figure 1.1 Agent loop architecture</h5>
    </div>
    <table id="tbl-1">
      <tr><th>Tier</th><th>Latency</th></tr>
      <tr><td>Basic</td><td>Slow</td></tr>
    </table>
    <m:math id="eq-1"><m:mi>x</m:mi><m:mo>=</m:mo><m:mn>1</m:mn></m:math>
  </body>
</html>
"""

PREFORMATTED_CODE_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <pre class="code-area">def run_agent():
    return "ok"

print(run_agent())</pre>
  </body>
</html>
"""

IMAGE_ONLY_FIGURE_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <div id="cover" class="browsable-container figure-container">
      <img src="images/cover.png" alt="Cover illustration" />
    </div>
  </body>
</html>
"""

DEDUP_CONTENT_OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package version="3.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Dedup Book</dc:title>
    <dc:creator>welcome.html</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav" />
    <item id="cover" href="cover.xhtml" media-type="application/xhtml+xml" />
    <item id="welcome_a" href="welcome.xhtml" media-type="application/xhtml+xml" />
    <item id="welcome_b" href="welcome.xhtml" media-type="application/xhtml+xml" />
    <item id="chap1" href="chapter1.xhtml" media-type="application/xhtml+xml" />
  </manifest>
  <spine>
    <itemref idref="cover" linear="no" />
    <itemref idref="welcome_a" />
    <itemref idref="welcome_b" />
    <itemref idref="chap1" />
  </spine>
</package>
"""

COVER_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <img src="cover.png" alt="Cover" />
  </body>
</html>
"""

WELCOME_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="welcome">Welcome</h1>
    <p>Thank you for reading.</p>
  </body>
</html>
"""


class EPUBParserTests(unittest.TestCase):
    def test_parse_minimal_epub(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            epub_path = Path(tmpdir) / "sample.epub"
            with zipfile.ZipFile(epub_path, "w") as archive:
                archive.writestr("mimetype", "application/epub+zip")
                archive.writestr("META-INF/container.xml", CONTAINER_XML)
                archive.writestr("OEBPS/content.opf", CONTENT_OPF)
                archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
                archive.writestr("OEBPS/chapter1.xhtml", CHAPTER_XHTML)

            parsed = EPUBParser().parse(epub_path)

        self.assertEqual(parsed.title, "Test Book")
        self.assertEqual(parsed.author, "Test Author")
        self.assertEqual(len(parsed.chapters), 1)
        chapter = parsed.chapters[0]
        self.assertEqual(chapter.title, "Chapter One")
        self.assertEqual([block.block_type for block in chapter.blocks], ["heading", "paragraph", "quote"])
        self.assertEqual(chapter.blocks[0].anchor, "ch1")

    def test_parse_epub_with_named_html_entity(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            epub_path = Path(tmpdir) / "sample-entity.epub"
            with zipfile.ZipFile(epub_path, "w") as archive:
                archive.writestr("mimetype", "application/epub+zip")
                archive.writestr("META-INF/container.xml", CONTAINER_XML)
                archive.writestr("OEBPS/content.opf", CONTENT_OPF)
                archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
                archive.writestr("OEBPS/chapter1.xhtml", ENTITY_XHTML)

            parsed = EPUBParser().parse(epub_path)

        self.assertEqual(parsed.chapters[0].blocks[1].text, "Resolution is 2 × 4.")

    def test_parse_epub_with_malformed_html_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            epub_path = Path(tmpdir) / "sample-malformed.epub"
            with zipfile.ZipFile(epub_path, "w") as archive:
                archive.writestr("mimetype", "application/epub+zip")
                archive.writestr("META-INF/container.xml", CONTAINER_XML)
                archive.writestr("OEBPS/content.opf", CONTENT_OPF)
                archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
                archive.writestr("OEBPS/chapter1.xhtml", MALFORMED_HTML)

            parsed = EPUBParser().parse(epub_path)

        chapter = parsed.chapters[0]
        self.assertEqual([block.block_type for block in chapter.blocks], ["heading", "paragraph", "code"])
        self.assertIn("<think>", chapter.blocks[1].text)
        self.assertEqual(chapter.blocks[2].text, "while value < limit:")

    def test_parse_epub_with_structured_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            epub_path = Path(tmpdir) / "sample-structured.epub"
            with zipfile.ZipFile(epub_path, "w") as archive:
                archive.writestr("mimetype", "application/epub+zip")
                archive.writestr("META-INF/container.xml", CONTAINER_XML)
                archive.writestr("OEBPS/content.opf", CONTENT_OPF)
                archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
                archive.writestr("OEBPS/chapter1.xhtml", STRUCTURED_ARTIFACT_XHTML)

            parsed = EPUBParser().parse(epub_path)

        chapter = parsed.chapters[0]
        self.assertEqual([block.block_type for block in chapter.blocks], ["heading", "caption", "table", "code"])
        self.assertEqual(chapter.blocks[1].metadata["image_src"], "images/agent-loop.png")
        self.assertEqual(chapter.blocks[1].metadata["image_path"], "OEBPS/images/agent-loop.png")
        self.assertEqual(chapter.blocks[1].metadata["image_alt"], "Agent loop architecture")
        self.assertEqual(chapter.blocks[2].metadata["tag"], "table")
        self.assertEqual(chapter.blocks[3].metadata["tag"], "math")

    def test_parse_epub_preserves_preformatted_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            epub_path = Path(tmpdir) / "sample-code.epub"
            with zipfile.ZipFile(epub_path, "w") as archive:
                archive.writestr("mimetype", "application/epub+zip")
                archive.writestr("META-INF/container.xml", CONTAINER_XML)
                archive.writestr("OEBPS/content.opf", CONTENT_OPF)
                archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
                archive.writestr("OEBPS/chapter1.xhtml", PREFORMATTED_CODE_XHTML)

            parsed = EPUBParser().parse(epub_path)

        chapter = parsed.chapters[0]
        self.assertEqual([block.block_type for block in chapter.blocks], ["heading", "code"])
        self.assertEqual(
            chapter.blocks[1].text,
            'def run_agent():\n    return "ok"\n\nprint(run_agent())',
        )

    def test_parse_epub_marks_image_only_figure_as_nontranslatable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            epub_path = Path(tmpdir) / "sample-image-only.epub"
            with zipfile.ZipFile(epub_path, "w") as archive:
                archive.writestr("mimetype", "application/epub+zip")
                archive.writestr("META-INF/container.xml", CONTAINER_XML)
                archive.writestr("OEBPS/content.opf", CONTENT_OPF)
                archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
                archive.writestr("OEBPS/chapter1.xhtml", IMAGE_ONLY_FIGURE_XHTML)

            parsed = EPUBParser().parse(epub_path)

        chapter = parsed.chapters[0]
        self.assertEqual([block.block_type for block in chapter.blocks], ["caption"])
        self.assertEqual(chapter.blocks[0].text, "Cover illustration")
        self.assertEqual(chapter.blocks[0].metadata["image_caption_generated"], "alt")
        self.assertFalse(chapter.blocks[0].metadata["translatable"])
        self.assertEqual(chapter.blocks[0].metadata["nontranslatable_reason"], "image_only_artifact")

    def test_parse_epub_skips_non_linear_and_duplicate_spine_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            epub_path = Path(tmpdir) / "sample-dedup.epub"
            with zipfile.ZipFile(epub_path, "w") as archive:
                archive.writestr("mimetype", "application/epub+zip")
                archive.writestr("META-INF/container.xml", CONTAINER_XML)
                archive.writestr("OEBPS/content.opf", DEDUP_CONTENT_OPF)
                archive.writestr("OEBPS/nav.xhtml", NAV_XHTML.replace("chapter1.xhtml", "welcome.xhtml").replace("Chapter One", "Welcome"))
                archive.writestr("OEBPS/cover.xhtml", COVER_XHTML)
                archive.writestr("OEBPS/welcome.xhtml", WELCOME_XHTML)
                archive.writestr("OEBPS/chapter1.xhtml", CHAPTER_XHTML)

            parsed = EPUBParser().parse(epub_path)

        self.assertIsNone(parsed.author)
        self.assertEqual(len(parsed.chapters), 2)
        self.assertEqual(parsed.chapters[0].href, "OEBPS/welcome.xhtml")
        self.assertEqual(parsed.chapters[0].title, "Welcome")
        self.assertEqual(parsed.chapters[1].href, "OEBPS/chapter1.xhtml")
        self.assertEqual(parsed.chapters[1].title, "Chapter One")


if __name__ == "__main__":
    unittest.main()
