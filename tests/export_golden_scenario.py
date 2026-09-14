"""Render every supported export for an EPUB and a PDF fixture, for golden snapshots.

Bootstraps each fixture, translates with the echo worker, reviews, and writes
all export types the service can render without optional tooling (rebuilt PDF
needs Playwright). The export gate is bypassed so the snapshot pins rendering
only. ``snapshot_exports`` reads back every written file, normalizing ids,
hashes, timestamps and temp paths; zip-based exports are expanded entry by
entry.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from pathlib import Path
from unittest.mock import patch

import tests.test_api_workflow as api_fixtures
from book_agent.domain.enums import ExportType
from book_agent.infra.db.session import session_scope
from book_agent.services.export import ExportService
from book_agent.services.workflows import DocumentWorkflowService
from tests.golden_pdfs.fixtures import make_code_block_book, make_figure_with_caption

FIXTURE_EXPORT_TYPES: dict[str, tuple[ExportType, ...]] = {
    "epub": (
        ExportType.BILINGUAL_HTML,
        ExportType.REVIEW_PACKAGE,
        ExportType.MERGED_HTML,
        ExportType.MERGED_MARKDOWN,
        ExportType.REBUILT_EPUB,
        ExportType.ZH_EPUB,
    ),
    "pdf": (
        ExportType.BILINGUAL_HTML,
        ExportType.REVIEW_PACKAGE,
        ExportType.MERGED_HTML,
        ExportType.MERGED_MARKDOWN,
    ),
}

_TEXT_SUFFIXES = {".html", ".xhtml", ".md", ".json", ".css", ".opf", ".ncx", ".xml", ".txt"}
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\b[0-9a-f]{32}\b")
_SHA256 = re.compile(r"\b[0-9a-f]{64}\b")
_ISO_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")


def write_epub(root: Path) -> Path:
    chapters = [("Chapter One", "chapter1.xhtml"), ("Chapter Two", "chapter2.xhtml")]
    epub_path = root / "golden-export.epub"
    with zipfile.ZipFile(epub_path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", api_fixtures.CONTAINER_XML)
        archive.writestr("OEBPS/content.opf", api_fixtures._content_opf_with_chapters(chapters))
        archive.writestr("OEBPS/nav.xhtml", api_fixtures._nav_xhtml_with_chapters(chapters))
        archive.writestr("OEBPS/chapter1.xhtml", api_fixtures.STRUCTURED_ARTIFACT_XHTML)
        archive.writestr("OEBPS/chapter2.xhtml", api_fixtures.CODE_CHAPTER_XHTML)
        archive.writestr("OEBPS/images/agent-loop.png", b"fake-png-binary")
    return epub_path


def write_pdf(root: Path) -> Path:
    import fitz  # PyMuPDF

    pdf_path = root / "golden-export.pdf"
    merged = fitz.open()
    for pdf_bytes in (make_figure_with_caption(), make_code_block_book()):
        part = fitz.open(stream=pdf_bytes, filetype="pdf")
        merged.insert_pdf(part)
        part.close()
    merged.save(pdf_path, garbage=4, deflate=True, no_new_id=True)
    merged.close()
    return pdf_path


def render_exports(session_factory, root: Path, fixture: str) -> Path:
    source = write_epub(root) if fixture == "epub" else write_pdf(root)
    export_root = root / "exports"
    with session_scope(session_factory) as session:
        document_id = DocumentWorkflowService(session, export_root=str(export_root)).bootstrap_document(source).document_id
    with session_scope(session_factory) as session:
        workflow = DocumentWorkflowService(session, export_root=str(export_root))
        workflow.translate_document(document_id)
        workflow.review_document(document_id)
    for export_type in FIXTURE_EXPORT_TYPES[fixture]:
        with session_scope(session_factory) as session:
            workflow = DocumentWorkflowService(session, export_root=str(export_root))
            with patch.object(ExportService, "_enforce_gate", autospec=True, return_value=None):
                workflow.export_document(document_id, export_type)
    return export_root


def snapshot_exports(export_root: Path, root: Path) -> dict[str, object]:
    ids: dict[str, str] = {}

    def normalize_text(text: str) -> str:
        text = text.replace(str(root.resolve()), "<root>").replace(str(root), "<root>")
        text = _SHA256.sub("<sha256>", text)
        text = _ISO_TIMESTAMP.sub("<ts>", text)
        return _UUID.sub(lambda match: ids.setdefault(match.group(0), f"<id{len(ids) + 1}>"), text)

    def entry(name: str, data: bytes) -> object:
        if Path(name).suffix.lower() in _TEXT_SUFFIXES or name.endswith("mimetype"):
            return normalize_text(data.decode("utf-8"))
        return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}

    def order_key(path: Path) -> tuple[str, str]:
        # Ids differ between runs, so order files by their id-free name and
        # content; placeholders are then numbered in that stable order.
        generic_name = _UUID.sub("<id>", path.relative_to(export_root).as_posix())
        generic_content = _UUID.sub("<id>", path.read_bytes().decode("utf-8", errors="replace"))
        return generic_name, generic_content

    snapshot: dict[str, object] = {}
    files = sorted(
        (path for path in export_root.rglob("*") if path.is_file() and "blobs" not in path.parts),
        key=order_key,
    )
    for path in files:
        normalize_text(path.relative_to(export_root).as_posix())
    for path in files:
        name = normalize_text(path.relative_to(export_root).as_posix())
        data = path.read_bytes()
        if path.suffix.lower() == ".epub":
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                snapshot[name] = {
                    normalize_text(info.filename): entry(info.filename, archive.read(info.filename))
                    for info in sorted(archive.infolist(), key=lambda info: info.filename)
                }
        else:
            snapshot[name] = entry(path.name, data)
    return snapshot
