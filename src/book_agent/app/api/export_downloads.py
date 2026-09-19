"""Export download responses: artifact path resolution, filenames and zip archives.

Export rows store the writer's file path; the bytes may since live in the
content-addressed blob tree, at a legacy name, or under the canonical
``<root>/[exports/]<document_id>/`` layout. Resolution only ever returns paths
inside the configured artifact roots.
"""

from __future__ import annotations

import mimetypes
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException, status
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask

from book_agent.domain.document_titles import document_display_title, safe_title_for_filename
from book_agent.domain.enums import ExportStatus, ExportType
from book_agent.infra.repositories.export import ExportRepository
from book_agent.infra.storage.blobs import UNRECOVERABLE_SCHEME

# Download packaging: one self-contained HTML file (images embedded) or the stored files zipped.
PACKAGE_SINGLE = "single"
PACKAGE_ZIP = "zip"
SINGLE_FILE_TYPES = frozenset({ExportType.MERGED_HTML, ExportType.BILINGUAL_HTML})


def _standalone_html(file_path: Path, canonical_path: Path) -> str | None:
    """The export with its local assets embedded, or None when an asset is missing (serve the zip then)."""
    from book_agent.export.standalone import drop_unused_katex, inline_local_assets, local_references

    document = file_path.read_text(encoding="utf-8")
    document = drop_unused_katex(inline_local_assets(document, canonical_path.parent))
    return None if local_references(document) else document


def _html_response(document: str, filename: str) -> Response:
    return Response(
        content=document.encode("utf-8"),
        media_type="text/html; charset=utf-8",
        headers={"content-disposition": content_disposition(filename)},
    )


@dataclass(frozen=True, slots=True)
class ArchiveInput:
    path: Path
    archive_name: str | None = None


def cleanup_path(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    finally:
        parent = path.parent
        try:
            parent.rmdir()
        except OSError:
            pass


def _artifact_fallback_candidates(path: Path) -> list[Path]:
    if path.exists():
        return [path]
    candidates: list[Path] = []
    if path.suffix.lower() == ".html" and path.stem == "merged-document":
        candidates.extend(
            sorted(
                path.parent.glob("merged-document*.html"),
                key=lambda candidate: (len(candidate.name), candidate.name),
            )
        )
    return [candidate.resolve() for candidate in candidates if candidate.exists()]


def _is_within_any_root(path: Path, roots: tuple[Path, ...]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
        except ValueError:
            continue
        return True
    return False


def _by_basename_under_document(
    basename: str,
    document_id: str,
    roots: tuple[Path, ...],
) -> Path | None:
    # Phase-2 self-heal: a stored file_path can point at a path that is
    # no longer reachable (e.g. a tempdir produced by a smoke run that
    # leaked into prod DB). The physical artifact is often still present
    # under the canonical layout <root>/[exports/]<document_id>/<basename>.
    # Try both layouts for each configured root.
    for root in roots:
        for candidate in (
            root / document_id / basename,
            root / "exports" / document_id / basename,
        ):
            resolved = candidate.resolve()
            if _is_within_any_root(resolved, roots) and resolved.exists():
                return resolved
    return None


def assert_record_serviceable(record: Any) -> None:
    # Short-circuit deterministic 410 Gone when the row is known-stale.
    # This is set either by the M1 legacy backfill migration (for old
    # tempdir-rooted rows) or by future verifier/reaper jobs. Doing this
    # check BEFORE filesystem resolution means we never accidentally
    # serve a half-healed artifact when the metadata says the row is
    # poisoned.
    stale = getattr(record, "stale_reason", None)
    file_path = getattr(record, "file_path", "") or ""
    if stale or file_path.startswith(UNRECOVERABLE_SCHEME):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=(
                f"Export artifact is permanently unavailable "
                f"(reason: {stale or 'unrecoverable_sentinel'}). "
                "Re-export the document to generate a fresh artifact."
            ),
        )


def _blob_path(content_sha256: str, roots: tuple[Path, ...]) -> Path | None:
    # M2.2b: the CAS layout populated by scripts/materialize_blob_store.py
    # lives at ``<artifact_root>/blobs/<aa>/<bb>/<sha>``. We probe every
    # configured root because tests and prod use different parents, and
    # only accept a hit that lies under one of them so the existing
    # _is_within_any_root safety net keeps working.
    if not content_sha256 or len(content_sha256) < 4:
        return None
    relative = Path("blobs") / content_sha256[:2] / content_sha256[2:4] / content_sha256
    for root in roots:
        candidate = (root / relative).resolve()
        if _is_within_any_root(candidate, roots) and candidate.exists():
            return candidate
    return None


def _canonical_artifact_path(
    candidate: str | Path,
    *,
    roots: tuple[Path, ...],
    document_id: str | None = None,
) -> Path | None:
    # Non-raising variant of ``resolve_artifact_path`` that intentionally
    # does NOT consult the CAS blob tree. We use it whenever we need the
    # writer-layout directory (for sidecar asset discovery) or the
    # original filename (which the blob tree has replaced with a sha).
    try:
        return resolve_artifact_path(
            candidate,
            roots=roots,
            document_id=document_id,
            content_sha256=None,
        )
    except HTTPException:
        return None


_ASCII_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._\-]+")


def _ascii_fallback_name(filename: str) -> str:
    # RFC 6266 §4.3 asks for an ASCII ``filename=`` token alongside the
    # ``filename*=`` UTF-8 form. Browsers that parse the extended form
    # happily use the Chinese title; browsers that do not (some Safari
    # releases on macOS notoriously drop the extension when consuming
    # only ``filename*=``) need a clean ASCII fallback so the saved
    # file keeps its ``.html`` / ``.md`` / ``.pdf`` suffix.
    stem = Path(filename).stem
    ext = Path(filename).suffix
    ascii_stem = re.sub(r"-+", "-", _ASCII_FILENAME_UNSAFE.sub("-", stem)).strip("-")
    ascii_stem = ascii_stem or "export"
    return f"{ascii_stem}{ext}" if ext else ascii_stem


def content_disposition(filename: str) -> str:
    # Produce a dual-form Content-Disposition header per RFC 6266.
    # Starlette only emits one form; when the payload name is non-ASCII
    # it drops the ``filename=`` alternative entirely, which loses the
    # extension on at least macOS Safari downloads.
    ascii_name = _ascii_fallback_name(filename)
    encoded = quote(filename, safe="")
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded}'


def _canonical_basename(file_path: str | Path) -> str:
    # The record's stored ``file_path`` is authoritative for naming even
    # when we serve bytes from the CAS tree — the blob on disk is named
    # by its sha256, so ``resolved.suffix`` would be empty and user
    # downloads would land without an extension.
    return Path(file_path).name


def resolve_artifact_path(
    candidate: str | Path,
    *,
    roots: tuple[Path, ...],
    document_id: str | None = None,
    content_sha256: str | None = None,
) -> Path:
    # Belt-and-suspenders: even if a caller forgot to run
    # assert_record_serviceable, an `unrecoverable://…` path must never
    # be interpreted as a filesystem path.
    candidate_str = str(candidate)
    if candidate_str.startswith(UNRECOVERABLE_SCHEME):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Export artifact is permanently unavailable (unrecoverable sentinel).",
        )
    # M2.2b: prefer the content-addressable blob when we have its sha.
    # The blob tree is the authoritative location — its path IS the
    # hash, so there is no way for it to drift. If the blob is missing
    # (not yet materialized), we fall through to the canonical path
    # logic below and everything behaves as before.
    if content_sha256:
        blob = _blob_path(content_sha256, roots)
        if blob is not None:
            return blob
    resolved = Path(candidate).resolve()
    fallback_candidates = [resolved, *_artifact_fallback_candidates(resolved)]
    for fallback_path in fallback_candidates:
        if _is_within_any_root(fallback_path, roots) and fallback_path.exists():
            return fallback_path
    # Phase 2: canonical-layout fallback keyed by (document_id, basename).
    if document_id:
        healed = _by_basename_under_document(resolved.name, document_id, roots)
        if healed is not None:
            return healed
    allowed_root_label = ", ".join(str(root) for root in roots)
    if any(_is_within_any_root(fallback_path, roots) for fallback_path in fallback_candidates):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Export artifact not found under allowed roots: {allowed_root_label}",
        )
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Export artifact is no longer available.",
    )


def _export_sidecar_paths(file_path: Path) -> list[Path]:
    if file_path.suffix.lower() not in {".html", ".md"}:
        return []
    assets_dir = file_path.parent / "assets"
    if not assets_dir.is_dir():
        return []
    return sorted(path for path in assets_dir.rglob("*") if path.is_file())


def _sidecar_archive_name(sidecar_path: Path, canonical_path: Path) -> str | None:
    try:
        return sidecar_path.relative_to(canonical_path.parent).as_posix()
    except ValueError:
        return None


def build_export_archive(
    document_id: str,
    export_type: ExportType,
    files: list[ArchiveInput],
    *,
    folder_name: str | None = None,
) -> Path:
    temp_file = tempfile.NamedTemporaryFile(
        prefix=f"book-agent-{document_id}-{export_type.value}-",
        suffix=".zip",
        delete=False,
    )
    archive_path = Path(temp_file.name)
    temp_file.close()
    if not folder_name:
        folder_name = f"{document_id}-{export_type.value}"
    common_root = Path(os.path.commonpath([str(file.path) for file in files]))
    with zipfile.ZipFile(archive_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        seen_names: set[str] = set()
        for index, file in enumerate(files, start=1):
            file_path = file.path
            if file.archive_name:
                archive_name = file.archive_name
            else:
                try:
                    archive_name = file_path.relative_to(common_root).as_posix()
                except ValueError:
                    archive_name = file_path.name
            if archive_name in seen_names:
                archive_name = f"{file_path.stem}-{index}{file_path.suffix}"
            seen_names.add(archive_name)
            archive.write(file_path, arcname=f"{folder_name}/{archive_name}")
    return archive_path


def _preferred_archive_name(original_path: str | Path, resolved_path: Path) -> str | None:
    original_name = Path(original_path).name
    if not original_name or original_name == resolved_path.name:
        return None
    return original_name


def _chapter_export_download_filename(
    document,
    chapter,
    export_type: ExportType,
    *,
    file_suffix: str,
    archive: bool = False,
) -> str:
    book_title = safe_title_for_filename(document_display_title(document), wrap_book_quotes=True)
    chapter_ordinal = getattr(chapter, "ordinal", None)
    chapter_title = safe_title_for_filename(
        getattr(chapter, "title_tgt", None) or getattr(chapter, "title_src", None),
        fallback="未命名章节",
    )
    chapter_prefix = f"第{chapter_ordinal}章" if isinstance(chapter_ordinal, int) and chapter_ordinal > 0 else "章节导出"
    label_map = {
        ExportType.BILINGUAL_HTML: "双语章节包",
        ExportType.REVIEW_PACKAGE: "审校包",
    }
    label = label_map.get(export_type, export_type.value)
    suffix = ".zip" if archive else file_suffix
    return f"{book_title}-{chapter_prefix}-{chapter_title}-{label}{suffix}"


def _append_archive_input(
    archive_inputs: list[ArchiveInput],
    seen_paths: set[str],
    file_path: Path,
    *,
    preferred_archive_name: str | None = None,
) -> None:
    for index, candidate in enumerate([file_path, *_export_sidecar_paths(file_path)]):
        resolved = str(candidate.resolve())
        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)
        archive_inputs.append(
            ArchiveInput(
                path=candidate,
                archive_name=(preferred_archive_name if index == 0 else None),
            )
        )


def chapter_export_response(
    export_repository: ExportRepository,
    document_id: str,
    chapter_id: str,
    export_type: ExportType,
    *,
    artifact_roots: tuple[Path, ...],
    package: str = PACKAGE_SINGLE,
) -> Response:
    """Serve a chapter's latest successful export: one HTML file with its images embedded, or zipped with its assets."""
    chapter_bundle = export_repository.load_chapter_bundle(chapter_id)
    try:
        records = export_repository.list_document_exports_filtered(
            document_id,
            export_type=export_type,
            status=ExportStatus.SUCCEEDED,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    chapter_record = next(
        (
            record
            for record in records
            if str((record.input_version_bundle_json or {}).get("chapter_id") or "") == chapter_id
        ),
        None,
    )
    if chapter_record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No successful {export_type.value} export is available for chapter {chapter_id}.",
        )

    assert_record_serviceable(chapter_record)
    file_path = resolve_artifact_path(
        chapter_record.file_path,
        roots=artifact_roots,
        document_id=document_id,
        content_sha256=chapter_record.content_sha256,
    )
    canonical_basename = _canonical_basename(chapter_record.file_path)
    canonical_suffix = Path(canonical_basename).suffix or ""
    canonical_path = _canonical_artifact_path(
        chapter_record.file_path, roots=artifact_roots, document_id=document_id
    ) or file_path
    if package == PACKAGE_SINGLE and export_type in SINGLE_FILE_TYPES and canonical_suffix.lower() == ".html":
        document = _standalone_html(file_path, canonical_path)
        if document is not None:
            return _html_response(
                document,
                _chapter_export_download_filename(
                    chapter_bundle.document, chapter_bundle.chapter, export_type, file_suffix=".html"
                ),
            )
    archive_inputs = [
        ArchiveInput(path=file_path, archive_name=_preferred_archive_name(chapter_record.file_path, file_path)),
        *[
            ArchiveInput(
                path=sidecar_path,
                archive_name=_sidecar_archive_name(sidecar_path, canonical_path),
            )
            for sidecar_path in _export_sidecar_paths(canonical_path)
        ],
    ]
    if len(archive_inputs) == 1:
        dl_name = _chapter_export_download_filename(
            chapter_bundle.document,
            chapter_bundle.chapter,
            export_type,
            file_suffix=canonical_suffix,
        )
        return FileResponse(
            path=file_path,
            media_type=mimetypes.guess_type(canonical_basename)[0]
            or "application/octet-stream",
            headers={"content-disposition": content_disposition(dl_name)},
        )

    archive_path = build_export_archive(document_id, export_type, archive_inputs)
    dl_name = _chapter_export_download_filename(
        chapter_bundle.document,
        chapter_bundle.chapter,
        export_type,
        file_suffix=".zip",
        archive=True,
    )
    return FileResponse(
        path=archive_path,
        media_type="application/zip",
        headers={"content-disposition": content_disposition(dl_name)},
        background=BackgroundTask(cleanup_path, archive_path),
    )


def document_export_response(
    export_repository: ExportRepository,
    document_id: str,
    export_type: ExportType,
    *,
    artifact_roots: tuple[Path, ...],
    package: str = PACKAGE_SINGLE,
) -> Response:
    """Serve a document's latest successful export.

    HTML exports come as one self-contained file by default (images embedded;
    the bilingual version assembled from the chapter exports into one book).
    ``package=zip`` returns the stored files and their assets folder instead.
    """
    try:
        document = export_repository.get_document(document_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if export_type == ExportType.BILINGUAL_HTML:
        return _bilingual_document_response(export_repository, document, artifact_roots=artifact_roots, package=package)
    lookup_type = export_type

    try:
        primary_records = export_repository.list_document_exports_filtered(
            document_id,
            export_type=lookup_type,
            status=ExportStatus.SUCCEEDED,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    if not primary_records:
        # Downloads only serve existing exports; POST /export enqueues one.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No successful {lookup_type.value} exports are available for download.",
        )

    book_title = safe_title_for_filename(document_display_title(document), wrap_book_quotes=True)
    label_map = {
        ExportType.MERGED_HTML: "中文阅读稿",
        ExportType.MERGED_MARKDOWN: "中文阅读稿-Markdown",
        ExportType.BILINGUAL_HTML: "中英文对照",
        ExportType.REBUILT_EPUB: "重建EPUB",
        ExportType.REBUILT_PDF: "重建PDF",
        ExportType.REVIEW_PACKAGE: "审校包",
    }
    label = label_map.get(export_type, export_type.value)

    # If every matching record is marked stale, fail fast with a
    # consistent 410 rather than letting resolve_artifact_path bubble
    # a 404 and confuse operators.
    for record in primary_records:
        assert_record_serviceable(record)
    files = [
        resolve_artifact_path(
            record.file_path,
            roots=artifact_roots,
            document_id=document_id,
            content_sha256=record.content_sha256,
        )
        for record in primary_records
    ]

    # Records are newest first; deliver the latest one.
    file_path = files[0]
    primary_record = primary_records[0]
    canonical_basename = _canonical_basename(primary_record.file_path)
    ext = Path(canonical_basename).suffix or ""
    main_filename = f"{book_title}-{label}{ext}"
    # Sidecar assets (images, CSS) live beside the canonical writer
    # output, not the blob. Probe the canonical layout; fall back to
    # the served path (still correct for rows with no blob preference).
    canonical_path = _canonical_artifact_path(
        primary_record.file_path, roots=artifact_roots, document_id=document_id
    ) or file_path

    if package == PACKAGE_SINGLE and export_type in SINGLE_FILE_TYPES and ext.lower() == ".html":
        document_html = _standalone_html(file_path, canonical_path)
        if document_html is not None:
            return _html_response(document_html, main_filename)

    archive_inputs: list[ArchiveInput] = []
    seen_paths: set[str] = set()
    _append_archive_input(
        archive_inputs,
        seen_paths,
        file_path,
        preferred_archive_name=main_filename,
    )
    for sidecar_path in _export_sidecar_paths(canonical_path):
        _append_archive_input(
            archive_inputs,
            seen_paths,
            sidecar_path,
            preferred_archive_name=_sidecar_archive_name(sidecar_path, canonical_path),
        )

    # Single file with no sidecar assets → return directly
    if len(archive_inputs) == 1:
        return FileResponse(
            path=file_path,
            media_type=mimetypes.guess_type(canonical_basename)[0]
            or "application/octet-stream",
            headers={"content-disposition": content_disposition(main_filename)},
        )

    # Multiple files (main + assets/) → zip with book-title folder
    archive_folder = f"{book_title}-{label}"
    archive_path = build_export_archive(
        document_id,
        export_type,
        archive_inputs,
        folder_name=archive_folder,
    )
    zip_name = f"{archive_folder}.zip"
    return FileResponse(
        path=archive_path,
        media_type="application/zip",
        headers={"content-disposition": content_disposition(zip_name)},
        background=BackgroundTask(cleanup_path, archive_path),
    )


def _bilingual_document_response(
    export_repository: ExportRepository,
    document: Any,
    *,
    artifact_roots: tuple[Path, ...],
    package: str = PACKAGE_SINGLE,
) -> Response:
    """The bilingual book: one HTML assembled from every chapter's latest export, or the chapter files zipped."""
    records = export_repository.list_document_exports_filtered(
        document.id,
        export_type=ExportType.BILINGUAL_HTML,
        status=ExportStatus.SUCCEEDED,
    )
    latest_by_chapter: dict[str, Any] = {}
    for record in records:  # newest first
        chapter_id = str((record.input_version_bundle_json or {}).get("chapter_id") or "")
        if chapter_id and chapter_id not in latest_by_chapter:
            latest_by_chapter[chapter_id] = record
    chapters = [
        chapter for chapter in export_repository.list_document_chapters(document.id) if chapter.id in latest_by_chapter
    ]
    if not chapters:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No successful bilingual_html chapter exports are available for download.",
        )

    book_title = safe_title_for_filename(document_display_title(document), wrap_book_quotes=True)
    if package == PACKAGE_SINGLE:
        book = _bilingual_book_html(document, chapters, latest_by_chapter, artifact_roots=artifact_roots)
        if book is not None:
            return _html_response(book, f"{book_title}-中英文对照.html")

    archive_inputs: list[ArchiveInput] = []
    seen_paths: set[str] = set()
    for chapter in chapters:
        record = latest_by_chapter[chapter.id]
        assert_record_serviceable(record)
        file_path = resolve_artifact_path(
            record.file_path,
            roots=artifact_roots,
            document_id=document.id,
            content_sha256=record.content_sha256,
        )
        canonical_path = _canonical_artifact_path(record.file_path, roots=artifact_roots, document_id=document.id) or file_path
        _append_archive_input(
            archive_inputs,
            seen_paths,
            file_path,
            preferred_archive_name=_chapter_export_download_filename(
                document,
                chapter,
                ExportType.BILINGUAL_HTML,
                file_suffix=Path(_canonical_basename(record.file_path)).suffix or ".html",
            ),
        )
        for sidecar_path in _export_sidecar_paths(canonical_path):
            _append_archive_input(
                archive_inputs,
                seen_paths,
                sidecar_path,
                preferred_archive_name=_sidecar_archive_name(sidecar_path, canonical_path),
            )

    archive_folder = f"{book_title}-中英文对照"
    archive_path = build_export_archive(
        document.id,
        ExportType.BILINGUAL_HTML,
        archive_inputs,
        folder_name=archive_folder,
    )
    return FileResponse(
        path=archive_path,
        media_type="application/zip",
        headers={"content-disposition": content_disposition(f"{archive_folder}.zip")},
        background=BackgroundTask(cleanup_path, archive_path),
    )


def _bilingual_book_html(
    document: Any,
    chapters: list[Any],
    latest_by_chapter: dict[str, Any],
    *,
    artifact_roots: tuple[Path, ...],
) -> str | None:
    from book_agent.export.standalone import BookChapter, assemble_bilingual_book, drop_unused_katex, local_references

    parts: list[BookChapter] = []
    for chapter in chapters:
        record = latest_by_chapter[chapter.id]
        assert_record_serviceable(record)
        file_path = resolve_artifact_path(
            record.file_path, roots=artifact_roots, document_id=document.id, content_sha256=record.content_sha256
        )
        canonical_path = _canonical_artifact_path(record.file_path, roots=artifact_roots, document_id=document.id) or file_path
        chapter_html = _standalone_html(file_path, canonical_path)
        if chapter_html is None:
            return None
        title = getattr(chapter, "title_tgt", None) or getattr(chapter, "title_src", None) or f"第{chapter.ordinal}章"
        parts.append(BookChapter(title=str(title), document=chapter_html))
    display_title = document_display_title(document)
    source_title = getattr(document, "title_src", None) or getattr(document, "title", None)
    book = assemble_bilingual_book(
        title=display_title,
        subtitle=source_title if source_title and source_title != display_title else None,
        chapters=parts,
    )
    book = drop_unused_katex(book)
    return None if local_references(book) else book
