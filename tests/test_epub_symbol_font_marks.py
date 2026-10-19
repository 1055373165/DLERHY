"""Word sets ®, © and ™ in the Symbol font, and an EPUB exported from it keeps the font's private-use
code points. Trading: Technical Analysis Masterclass failed parse health on "Apple \\uf0e2" and
"Porsche\\uf0e2"."""

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
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Masterclass</dc:title><dc:language>en</dc:language></metadata>
  <manifest>
    <item href="text/part1.html" id="p1" media-type="application/xhtml+xml"/>
    <item href="toc.ncx" id="ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="ncx"><itemref idref="p1"/></spine>
</package>"""

NCX = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><navMap>
  <navPoint id="a" playOrder="1"><navLabel><text>1. What is trading?</text></navLabel><content src="text/part1.html"/></navPoint>
</navMap></ncx>"""

PAGE = """<?xml version='1.0' encoding='utf-8'?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>x</title></head><body>
<h1>1. What is trading?</h1>
<p><span>If the stock of Apple</span><span> </span><span> or the euro appreciates, buy it today.</span></p>
<p><span>Whether we follow gold or the Porsch</span><span>e</span><span> share price, the patterns repeat.</span></p>
<p>Charts  2018 Example Ltd. Trading View is a trademark.</p>
</body></html>"""


class SymbolFontMarkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        path = Path(self.tempdir.name) / "book.epub"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", CONTAINER)
            archive.writestr("content.opf", OPF)
            archive.writestr("toc.ncx", NCX)
            archive.writestr("text/part1.html", PAGE)
        self.texts = [block.text for chapter in EPUBParser().parse(path).chapters for block in chapter.blocks]

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_registered_marks_become_the_real_character_against_the_name(self) -> None:
        self.assertIn("If the stock of Apple® or the euro appreciates, buy it today.", self.texts)
        self.assertIn("Whether we follow gold or the Porsche® share price, the patterns repeat.", self.texts)

    def test_copyright_and_trademark_marks_are_decoded(self) -> None:
        self.assertIn("Charts © 2018 Example Ltd. Trading View™ is a trademark.", self.texts)

    def test_no_private_use_character_is_left(self) -> None:
        self.assertFalse([text for text in self.texts if any(0xE000 <= ord(char) <= 0xF8FF for char in text)])


if __name__ == "__main__":
    unittest.main()
