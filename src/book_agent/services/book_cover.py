"""A book's cover picture, from its source file: a PDF's first page, or the cover image an EPUB declares.

Made the same way every time (same bytes for the same file), so a publish can tell by content hash
that the cover was uploaded before.
"""

from __future__ import annotations

import logging
import posixpath
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

# What the blog's cover upload takes (handlers.UploadCoverImage): these types, up to 10 MB.
COVER_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp"}
COVER_MAX_BYTES = 10 * 1024 * 1024
# A PDF page is drawn this many pixels wide: sharp on a 16:9 cover card, a few hundred KB as JPEG.
_PDF_COVER_WIDTH = 1200

_CONTAINER_NS = {"container": "urn:oasis:names:tc:opendocument:xmlns:container"}
_OPF_NS = {"opf": "http://www.idpf.org/2007/opf"}


@dataclass(frozen=True, slots=True)
class BookCover:
    data: bytes
    filename: str
    content_type: str


def book_cover(source_path: str | None) -> BookCover | None:
    """The cover of the book at ``source_path``, or None (no file, no cover, unreadable)."""
    if not source_path:
        return None
    path = Path(source_path)
    if not path.is_file():
        return None
    try:
        if path.suffix.lower() == ".pdf":
            return _pdf_cover(path)
        if path.suffix.lower() == ".epub":
            return _epub_cover(path)
    except Exception:  # noqa: BLE001 - a book without a readable cover is published without one
        logger.warning("book cover: cannot read %s", path, exc_info=True)
    return None


def _pdf_cover(path: Path) -> BookCover | None:
    import fitz

    with fitz.open(path) as document:
        if document.page_count == 0:
            return None
        page = document[0]
        zoom = _PDF_COVER_WIDTH / max(page.rect.width, 1.0)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return BookCover(pixmap.tobytes("jpeg", jpg_quality=88), "cover.jpg", "image/jpeg")


def _epub_cover(path: Path) -> BookCover | None:
    with zipfile.ZipFile(path) as archive:
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = container.find(".//container:rootfile", _CONTAINER_NS)
        opf_path = rootfile.attrib.get("full-path", "") if rootfile is not None else ""
        if not opf_path:
            return None
        opf = ET.fromstring(archive.read(opf_path))
        items = [
            (
                item.attrib.get("id", ""),
                posixpath.normpath(posixpath.join(posixpath.dirname(opf_path), item.attrib.get("href", ""))),
                item.attrib.get("media-type", ""),
                item.attrib.get("properties", ""),
            )
            for item in opf.findall(".//opf:manifest/opf:item", _OPF_NS)
        ]
        images = [item for item in items if item[2].startswith("image/")]
        # EPUB 3 marks it (properties="cover-image"); EPUB 2 names it (<meta name="cover" content="id">);
        # otherwise an image called cover.
        cover_id = next(
            (meta.attrib.get("content", "") for meta in opf.findall(".//opf:metadata/opf:meta", _OPF_NS) if meta.attrib.get("name") == "cover"),
            "",
        )
        chosen = (
            next((item for item in images if "cover-image" in item[3].split()), None)
            or next((item for item in images if cover_id and item[0] == cover_id), None)
            or next((item for item in images if "cover" in (item[0] + posixpath.basename(item[1])).casefold()), None)
        )
        if chosen is None:
            return None
        data = archive.read(chosen[1])
    content_type = chosen[2]
    if content_type in COVER_TYPES and len(data) <= COVER_MAX_BYTES:
        return BookCover(data, f"cover{COVER_TYPES[content_type]}", content_type)
    # SVG or too big: redrawn as a JPEG.
    import fitz

    pixmap = fitz.Pixmap(data)
    if pixmap.alpha or pixmap.n > 3:
        pixmap = fitz.Pixmap(fitz.csRGB, pixmap)
    return BookCover(pixmap.tobytes("jpeg", jpg_quality=88), "cover.jpg", "image/jpeg")
