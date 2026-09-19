"""The files a book leaves on disk, and removing them once the book is deleted.

Deleting a document drops its rows by FK cascade; its files are separate:
the uploaded source, the exports under ``<export_root>/<document_id>``, the
extracted images under ``<artifact_root>/document-images/<document_id>`` and
the content-addressed copies of its exports under ``<artifact_root>/blobs``.
The plan is taken before the rows go (it needs the export hashes) and
carried out after the delete commits, so a rolled-back delete never loses
files.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.domain.models import Document
from book_agent.domain.models.review import Export
from book_agent.infra.storage.blobs import blob_root_for_export_root, blob_target

_logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DocumentFiles:
    document_id: str
    source_path: Path | None
    directories: list[Path] = field(default_factory=list)
    export_hashes: set[str] = field(default_factory=set)
    blob_root: Path | None = None


def plan_document_files(session: Session, document: Document, export_root: str | Path) -> DocumentFiles:
    export_root = Path(export_root).resolve()
    document_id = str(document.id)
    hashes = {
        sha
        for sha in session.scalars(select(Export.content_sha256).where(Export.document_id == document.id))
        if sha
    }
    return DocumentFiles(
        document_id=document_id,
        source_path=Path(document.source_path) if document.source_path else None,
        directories=[export_root / document_id, export_root.parent / "document-images" / document_id],
        export_hashes=hashes,
        blob_root=blob_root_for_export_root(export_root),
    )


def remove_document_files(session: Session, files: DocumentFiles, *, upload_root: str | Path) -> list[Path]:
    """Remove what ``files`` names; returns what was removed. Best effort: failures are logged, not raised."""
    removed: list[Path] = []
    for directory in files.directories:
        if directory.is_dir():
            shutil.rmtree(directory, ignore_errors=True)
            removed.append(directory)
    upload_root = Path(upload_root).resolve()
    source = files.source_path.resolve() if files.source_path else None
    # Only files this app stored: a document bootstrapped from a path elsewhere keeps its source.
    if source is not None and source.is_file() and upload_root in source.parents:
        try:
            source.unlink()
            removed.append(source)
            if source.parent != upload_root and not any(source.parent.iterdir()):
                source.parent.rmdir()
        except OSError:
            _logger.warning("Could not remove uploaded source %s", source, exc_info=True)
    if files.blob_root is not None and files.export_hashes:
        still_used = set(
            session.scalars(
                select(Export.content_sha256).where(
                    Export.content_sha256.in_(files.export_hashes), Export.document_id != files.document_id
                )
            )
        )
        for sha in files.export_hashes - still_used:
            blob = blob_target(files.blob_root, sha)
            try:
                blob.unlink(missing_ok=True)
                removed.append(blob)
            except OSError:
                _logger.warning("Could not remove blob %s", blob, exc_info=True)
    return removed
