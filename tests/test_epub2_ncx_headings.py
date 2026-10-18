"""An EPUB 2 book: its contents in toc.ncx, its headings paragraphs the stylesheet sets large and bold.

Mastering Trading Psychology (an InDesign export through Calibre) has no nav document, and sets every
heading as ``<p class="heading1">`` / ``<p class="heading">``. Every chapter came out untitled and
without a heading: review blocked the sample on "missing_chapter_title", and the sample itself was
the copyright and disclaimer pages.
"""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from book_agent.domain.structure.epub import EPUBParser

CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Trading Book</dc:title><dc:language>en</dc:language></metadata>
  <manifest>
    <item href="text/part0001.html" id="c1" media-type="application/xhtml+xml"/>
    <item href="stylesheet.css" id="css" media-type="text/css"/>
    <item href="images/quote.jpeg" id="q" media-type="image/jpeg"/>
    <item href="toc.ncx" id="ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="ncx"><itemref idref="c1"/></spine>
</package>"""

NCX = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><navMap>
  <navPoint id="n1" playOrder="1"><navLabel><text>Chapter 1 - Trading, A Different Kind of Business</text></navLabel>
    <content src="text/part0001.html#Chapter_1"/></navPoint>
</navMap></ncx>"""

CSS = """
.heading1 { font-size: 1.83333em; font-weight: 800; }
.heading { font-size: 1.29167em; font-weight: bold; }
.heading2 { font-size: 0.75em; font-weight: bold; }
._idGenDropcap { font-size: 3em; font-weight: bold; }
.normal { font-size: 1em; }
"""

CHAPTER = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>c1</title></head>
<body id="Chapter_1">
  <p class="heading1">Trading: A Different Kind of Business</p>
  <p class="heading">Traders as Businesspeople</p>
  <p class="normal"><span class="_idGenDropcap">I</span>n my classes and books, I always emphasize that trading must be treated as a business.</p>
<div class="_idgenobjectlayout"><div class="icon"><img src="../images/quote.jpeg" alt="A screenshot of a cell phone  Description automatically generated"/></div></div>\n  <p class="quote">“Quoted forum post.”</p>\n<div class="_idgenobjectlayout"><div class="icon"><img src="../images/quote.jpeg" alt="A screenshot of a cell phone  Description automatically generated"/></div></div>\n  <p class="quote">“Quoted forum post.”</p>\n<div class="_idgenobjectlayout"><div class="icon"><img src="../images/quote.jpeg" alt="A screenshot of a cell phone  Description automatically generated"/></div></div>\n  <p class="quote">“Quoted forum post.”</p>\n<div class="_idgenobjectlayout"><div class="icon"><img src="../images/quote.jpeg" alt="A screenshot of a cell phone  Description automatically generated"/></div></div>\n  <p class="quote">“Quoted forum post.”</p>\n<div class="_idgenobjectlayout"><div class="icon"><img src="../images/quote.jpeg" alt="A screenshot of a cell phone  Description automatically generated"/></div></div>\n  <p class="quote">“Quoted forum post.”</p>\n  <p class="heading2">Figure 1.1: Hockey stick forecast for your new venture.</p>
  <p class="heading">Why Start Trading? A paragraph the stylesheet sets in the heading face but which runs on far longer than any heading would, so it is text, not a title for what follows it.</p>
</body></html>"""


class Epub2NcxAndStyledHeadingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "book.epub"
        with zipfile.ZipFile(self.path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", CONTAINER)
            archive.writestr("content.opf", OPF)
            archive.writestr("toc.ncx", NCX)
            archive.writestr("stylesheet.css", CSS)
            archive.writestr("text/part0001.html", CHAPTER)
        self.chapter = EPUBParser().parse(self.path).chapters[0]

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_the_ncx_titles_the_chapter(self) -> None:
        self.assertEqual(self.chapter.title, "Chapter 1 - Trading, A Different Kind of Business")

    def test_large_bold_paragraph_classes_are_headings_ranked_by_size(self) -> None:
        headings = [(block.text, block.metadata.get("heading_level")) for block in self.chapter.blocks if block.block_type == "heading"]
        self.assertEqual(headings, [("Trading: A Different Kind of Business", 1), ("Traders as Businesspeople", 2)])

    def test_an_image_repeated_without_a_caption_is_an_ornament(self) -> None:
        # The book's 197 quotation-mark icons came out as image boxes, their generated alt text translated.
        self.assertFalse([block for block in self.chapter.blocks if block.metadata.get("image_src")])
        self.assertEqual(sum(block.text == "“Quoted forum post.”" for block in self.chapter.blocks), 5)

    def test_small_captions_and_long_paragraphs_stay_text(self) -> None:
        texts = {block.text[:20]: block.block_type for block in self.chapter.blocks}
        self.assertNotEqual(texts["Figure 1.1: Hockey s"], "heading")
        self.assertNotEqual(texts["Why Start Trading? A"], "heading")


if __name__ == "__main__":
    unittest.main()
