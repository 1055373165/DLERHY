"""An Amazon-exported EPUB: pages that open with a bare ``<html>``, contents listing "CHAPTER 1" and its name
as two entries, and a title page, copyright page and printed contents the contents do not list.

How to Swing Trade imported with no chapter at all (no ``<body>`` in the XHTML namespace), and its
whole-book run sat with nothing to translate.
"""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from book_agent.domain.structure.epub import EPUBParser

CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>How to Swing Trade</dc:title><dc:language>en</dc:language></metadata>
  <manifest>
    <item href="Text/part0000.xhtml" id="h0" media-type="application/xhtml+xml"/>
    <item href="Text/part0001.xhtml" id="h1" media-type="application/xhtml+xml"/>
    <item href="Text/part0002.xhtml" id="h2" media-type="application/xhtml+xml"/>
    <item href="Text/part0004.xhtml" id="h4" media-type="application/xhtml+xml"/>
    <item href="toc.ncx" id="ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="ncx"><itemref idref="h0"/><itemref idref="h1"/><itemref idref="h2"/><itemref idref="h4"/></spine>
</package>"""

NCX = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><navMap>
  <navPoint id="a" playOrder="1"><navLabel><text>CHAPTER 1</text></navLabel><content src="Text/part0004.xhtml#_Toc1"/></navPoint>
  <navPoint id="b" playOrder="2"><navLabel><text>SWING TRADING BASICS</text></navLabel><content src="Text/part0004.xhtml#_Toc2"/></navPoint>
</navMap></ncx>"""


def page(body: str) -> str:
    return f"<?xml version='1.0' encoding='utf-8'?>\n<html><head><title>x</title></head><body>{body}</body></html>"


PAGES = {
    "OEBPS/Text/part0000.xhtml": page("<p>HOW TO</p><p>SWING TRADE</p><p>George Milton</p>"),
    "OEBPS/Text/part0001.xhtml": page("<p>Copyright &#169; 2022 George Milton. All rights are reserved by the author.</p>"),
    "OEBPS/Text/part0002.xhtml": page(
        "<p>TABLE OF CONTENTS</p><p><a href='part0004.xhtml#_Toc1'>CHAPTER 1</a></p><p><a href='part0004.xhtml#_Toc2'>SWING TRADING BASICS</a></p>"
    ),
    "OEBPS/Text/part0004.xhtml": page(
        "<h1 id='_Toc1'>CHAPTER 1</h1><h2 id='_Toc2'>SWING TRADING BASICS</h2><p>Swing trading holds a position for days.</p>"
    ),
}


class NamespaceLessEpubTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        path = Path(self.tempdir.name) / "book.epub"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", CONTAINER)
            archive.writestr("OEBPS/content.opf", OPF)
            archive.writestr("OEBPS/toc.ncx", NCX)
            for name, markup in PAGES.items():
                archive.writestr(name, markup)
        self.chapters = EPUBParser().parse(path).chapters

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_pages_without_a_namespace_are_read(self) -> None:
        chapter = self.chapters[-1]
        self.assertIn("Swing trading holds a position for days.", [block.text for block in chapter.blocks])

    def test_a_bare_chapter_label_takes_the_name_after_it(self) -> None:
        self.assertEqual(self.chapters[-1].title, "CHAPTER 1: SWING TRADING BASICS")

    def test_pages_ahead_of_the_contents_are_titled_and_the_printed_contents_dropped(self) -> None:
        self.assertEqual([chapter.title for chapter in self.chapters], ["HOW TO SWING TRADE", "Copyright", "CHAPTER 1: SWING TRADING BASICS"])


if __name__ == "__main__":
    unittest.main()
