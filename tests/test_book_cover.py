"""A book's cover for the blog: a PDF's first page, or the cover image an EPUB declares; the same
bytes every time, so a republish knows it uploaded it before."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

import fitz

from book_agent.services.book_cover import book_cover

_CONTAINER = (
    '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
    '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'
)


def _png(color: tuple[int, int, int]) -> bytes:
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 4, 6), False)
    pixmap.set_rect(pixmap.irect, color)
    return pixmap.tobytes("png")


def _epub(path: Path, manifest: str, metadata: str, files: dict[str, bytes]) -> None:
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
        f"<metadata>{metadata}</metadata><manifest>{manifest}</manifest><spine/></package>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", _CONTAINER)
        archive.writestr("OEBPS/content.opf", opf)
        for name, data in files.items():
            archive.writestr(f"OEBPS/{name}", data)


class BookCoverTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def test_a_pdf_cover_is_its_first_page_drawn_the_same_every_time(self) -> None:
        path = self.tmp / "book.pdf"
        document = fitz.open()
        document.new_page(width=500, height=650).insert_text((72, 72), "THE COVER")
        document.new_page().insert_text((72, 72), "chapter one")
        document.save(path)
        cover = book_cover(str(path))
        self.assertEqual((cover.filename, cover.content_type), ("cover.jpg", "image/jpeg"))
        self.assertEqual(cover.data, book_cover(str(path)).data)
        width = fitz.Pixmap(cover.data).width
        self.assertEqual(width, 1200)

    def test_an_epub_cover_is_the_image_it_declares(self) -> None:
        red, blue = _png((255, 0, 0)), _png((0, 0, 255))
        epub3 = self.tmp / "three.epub"
        _epub(
            epub3,
            '<item id="a" href="images/figure.png" media-type="image/png"/>'
            '<item id="b" href="images/front.png" media-type="image/png" properties="cover-image"/>',
            "",
            {"images/figure.png": red, "images/front.png": blue},
        )
        self.assertEqual(book_cover(str(epub3)).data, blue)
        epub2 = self.tmp / "two.epub"
        _epub(
            epub2,
            '<item id="a" href="figure.png" media-type="image/png"/><item id="art" href="front.png" media-type="image/png"/>',
            '<meta name="cover" content="art"/>',
            {"figure.png": red, "front.png": blue},
        )
        self.assertEqual((book_cover(str(epub2)).data, book_cover(str(epub2)).filename), (blue, "cover.png"))

    def test_no_cover_no_file(self) -> None:
        bare = self.tmp / "bare.epub"
        _epub(bare, '<item id="a" href="figure.png" media-type="image/png"/>', "", {"figure.png": _png((0, 0, 0))})
        self.assertIsNone(book_cover(str(bare)))
        self.assertIsNone(book_cover(str(self.tmp / "missing.pdf")))
        self.assertIsNone(book_cover(None))


if __name__ == "__main__":
    unittest.main()
