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

MALFORMED_TABLE_HTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <table id="tbl-1">
      <colgroup>
        <col class="tcol1 align-left"/>
        <col class="tcol2 align-left"/>
      </colgroup>
      <tr><th>Feature</th><th>Description</th></tr>
      <tr><td>Multi-agent conversations</td><td>AutoGen allows multiple agents to converse and collaborate.</td></tr>
    </table>
    <p>AT&T keeps this XHTML from being XML-clean.</p>
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

NESTED_HEADING_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <div class="chapter">
      <h1>Chapter 6. Prompt Engineering</h1>
      <div id="using_text_generation_models">
        <h1>Using Text Generation Models</h1>
      </div>
      <div id="choosing_a_text_generation_model">
        <h2>Choosing a Text Generation Model</h2>
      </div>
    </div>
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

FRONTMATTER_FILTER_CONTENT_OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package version="3.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Build an AI Agent</dc:title>
    <dc:creator>Test Author</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav" />
    <item id="titlepage" href="titlepage.xhtml" media-type="application/xhtml+xml" />
    <item id="welcome" href="welcome.xhtml" media-type="application/xhtml+xml" />
    <item id="brief" href="brief-table-of-contents.html" media-type="application/xhtml+xml" />
    <item id="chap1" href="chapter1.xhtml" media-type="application/xhtml+xml" />
  </manifest>
  <spine>
    <itemref idref="titlepage" />
    <itemref idref="welcome" />
    <itemref idref="brief" />
    <itemref idref="chap1" />
  </spine>
</package>
"""

TITLEPAGE_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
  </body>
</html>
"""

BRIEF_CONTENTS_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1>Brief contents</h1>
    <p>PART 1: BUILDING YOUR FIRST LLM AGENT</p>
    <p>1 What is an AI agent?</p>
    <p>2 The brain of AI agents: LLMs</p>
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
        self.assertEqual([block.block_type for block in chapter.blocks], ["heading", "figure", "caption", "table", "code"])
        self.assertEqual(chapter.blocks[1].metadata["image_src"], "images/agent-loop.png")
        self.assertEqual(chapter.blocks[1].metadata["image_path"], "OEBPS/images/agent-loop.png")
        self.assertEqual(chapter.blocks[1].metadata["image_alt"], "Agent loop architecture")
        self.assertEqual(chapter.blocks[1].metadata["linked_caption_source_anchor"], "OEBPS/chapter1.xhtml#fig-1-caption")
        self.assertEqual(chapter.blocks[2].metadata["caption_for_source_anchor"], "OEBPS/chapter1.xhtml#fig-1-figure")
        self.assertEqual(chapter.blocks[3].metadata["tag"], "table")
        self.assertEqual(chapter.blocks[4].metadata["tag"], "math")

    def test_parse_epub_with_malformed_table_fallback_preserves_table_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            epub_path = Path(tmpdir) / "sample-malformed-table.epub"
            with zipfile.ZipFile(epub_path, "w") as archive:
                archive.writestr("mimetype", "application/epub+zip")
                archive.writestr("META-INF/container.xml", CONTAINER_XML)
                archive.writestr("OEBPS/content.opf", CONTENT_OPF)
                archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
                archive.writestr("OEBPS/chapter1.xhtml", MALFORMED_TABLE_HTML)

            parsed = EPUBParser().parse(epub_path)

        chapter = parsed.chapters[0]
        self.assertEqual([block.block_type for block in chapter.blocks], ["heading", "table", "paragraph"])
        self.assertEqual(
            chapter.blocks[1].text,
            "Feature | Description\nMulti-agent conversations | AutoGen allows multiple agents to converse and collaborate.",
        )
        self.assertEqual(chapter.blocks[1].metadata["tag"], "table")
        self.assertNotIn("<colgroup>", chapter.blocks[1].text)
        self.assertNotIn("<col", chapter.blocks[1].text)

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

    def test_parse_epub_normalizes_nested_heading_levels_within_chapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            epub_path = Path(tmpdir) / "sample-nested-headings.epub"
            with zipfile.ZipFile(epub_path, "w") as archive:
                archive.writestr("mimetype", "application/epub+zip")
                archive.writestr("META-INF/container.xml", CONTAINER_XML)
                archive.writestr("OEBPS/content.opf", CONTENT_OPF)
                archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
                archive.writestr("OEBPS/chapter1.xhtml", NESTED_HEADING_XHTML)

            parsed = EPUBParser().parse(epub_path)

        chapter = parsed.chapters[0]
        heading_levels = [block.metadata.get("heading_level") for block in chapter.blocks if block.block_type == "heading"]
        self.assertEqual(heading_levels, [1, 2, 3])
        heading_anchors = [block.anchor for block in chapter.blocks if block.block_type == "heading"]
        self.assertEqual(heading_anchors, [None, "using_text_generation_models", "choosing_a_text_generation_model"])

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

    def test_parse_epub_skips_empty_titlepage_and_toc_like_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            epub_path = Path(tmpdir) / "sample-frontmatter-filter.epub"
            with zipfile.ZipFile(epub_path, "w") as archive:
                archive.writestr("mimetype", "application/epub+zip")
                archive.writestr("META-INF/container.xml", CONTAINER_XML)
                archive.writestr("OEBPS/content.opf", FRONTMATTER_FILTER_CONTENT_OPF)
                archive.writestr(
                    "OEBPS/nav.xhtml",
                    NAV_XHTML.replace("chapter1.xhtml", "welcome.xhtml").replace("Chapter One", "Welcome"),
                )
                archive.writestr("OEBPS/titlepage.xhtml", TITLEPAGE_XHTML)
                archive.writestr("OEBPS/welcome.xhtml", WELCOME_XHTML)
                archive.writestr("OEBPS/brief-table-of-contents.html", BRIEF_CONTENTS_XHTML)
                archive.writestr("OEBPS/chapter1.xhtml", CHAPTER_XHTML)

            parsed = EPUBParser().parse(epub_path)

        self.assertEqual([chapter.href for chapter in parsed.chapters], ["OEBPS/welcome.xhtml", "OEBPS/chapter1.xhtml"])
        self.assertEqual([chapter.title for chapter in parsed.chapters], ["Welcome", "Chapter One"])

    def test_parse_epub_ignores_page_list_nav_pollution_and_preserves_subtitle_metadata(self) -> None:
        content_opf = """<?xml version="1.0" encoding="UTF-8"?>
<package version="3.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title id="pub-title">Agentic AI</dc:title>
    <meta refines="#pub-title" property="title-type">main</meta>
    <dc:title id="pub-subtitle">Theories and Practices</dc:title>
    <meta refines="#pub-subtitle" property="title-type">subtitle</meta>
    <dc:creator>Ken Huang</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="navigation.xhtml" media-type="application/xhtml+xml" properties="nav" />
    <item id="frontmatter" href="frontmatter.xhtml" media-type="application/xhtml+xml" />
    <item id="chap1" href="chapter1.xhtml" media-type="application/xhtml+xml" />
  </manifest>
  <spine>
    <itemref idref="frontmatter" />
    <itemref idref="chap1" />
  </spine>
</package>
"""
        navigation_xhtml = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <body>
    <nav epub:type="toc">
      <ol>
        <li><a href="frontmatter.xhtml">Front Matter</a></li>
        <li><a href="chapter1.xhtml">1. The Genesis and Evolution of AI Agents</a></li>
      </ol>
    </nav>
    <nav epub:type="page-list">
      <ol>
        <li><a href="frontmatter.xhtml#PBxxxviii">xxxviii</a></li>
        <li><a href="chapter1.xhtml#PB20">20</a></li>
      </ol>
    </nav>
  </body>
</html>
"""
        frontmatter_xhtml = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1>Agentic AI</h1>
    <h2>Theories and Practices</h2>
  </body>
</html>
"""
        chapter_xhtml = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">1. The Genesis and Evolution of AI Agents</h1>
    <p>Origins matter.</p>
  </body>
</html>
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            epub_path = Path(tmpdir) / "sample-page-list.epub"
            with zipfile.ZipFile(epub_path, "w") as archive:
                archive.writestr("mimetype", "application/epub+zip")
                archive.writestr("META-INF/container.xml", CONTAINER_XML)
                archive.writestr("OEBPS/content.opf", content_opf)
                archive.writestr("OEBPS/navigation.xhtml", navigation_xhtml)
                archive.writestr("OEBPS/frontmatter.xhtml", frontmatter_xhtml)
                archive.writestr("OEBPS/chapter1.xhtml", chapter_xhtml)

            parsed = EPUBParser().parse(epub_path)

        self.assertEqual(parsed.title, "Agentic AI")
        self.assertEqual(parsed.metadata["subtitle"], "Theories and Practices")
        self.assertEqual(parsed.metadata["document_title_src"], "Agentic AI: Theories and Practices")
        self.assertEqual([chapter.title for chapter in parsed.chapters], ["Agentic AI", "1. The Genesis and Evolution of AI Agents"])


    def test_extract_rich_text_preserves_inline_formatting(self) -> None:
        """Verify bold, italic, and code inline elements are preserved as markdown."""
        from xml.etree.ElementTree import fromstring
        from book_agent.domain.structure.epub import _extract_rich_text

        # Bold
        el = fromstring("<p>This is <b>bold</b> text</p>")
        self.assertEqual(_extract_rich_text(el), "This is **bold** text")

        # Strong (alias for bold)
        el = fromstring("<p>This is <strong>strong</strong> text</p>")
        self.assertEqual(_extract_rich_text(el), "This is **strong** text")

        # Italic
        el = fromstring("<p>This is <i>italic</i> text</p>")
        self.assertEqual(_extract_rich_text(el), "This is *italic* text")

        # Em (alias for italic)
        el = fromstring("<p>This is <em>emphasized</em> text</p>")
        self.assertEqual(_extract_rich_text(el), "This is *emphasized* text")

        # Code (unchanged behavior)
        el = fromstring("<p>Use the <code>function()</code> method</p>")
        self.assertEqual(_extract_rich_text(el), "Use the `function()` method")

        # Mixed
        el = fromstring("<p>A <b>bold</b> and <code>code</code> and <i>italic</i> mix</p>")
        self.assertEqual(_extract_rich_text(el), "A **bold** and `code` and *italic* mix")


if __name__ == "__main__":
    unittest.main()
