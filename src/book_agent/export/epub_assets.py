"""Locating figure images inside source EPUB archives."""


from __future__ import annotations

import posixpath
import zipfile
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET

from book_agent.domain.structure.epub import (
    _element_class_tokens,
    _figure_caption_text,
    _figure_like_container,
    _first_descendant,
    _join_path,
    _local_name,
    _parse_xml_document,
)
from book_agent.export.common import (
    _FallbackEpubFigureIndexParser,
    _normalize_figure_caption_signature,
)
from book_agent.export.models import (
    MergedRenderBlock,
)


def find_epub_element_by_id(root: ET.Element, element_id: str) -> ET.Element | None:
    for element in root.iter():
        if str(element.attrib.get("id") or "").strip() == element_id:
            return element
    return None


def recover_legacy_epub_figure_archive_path(
    block: MergedRenderBlock,
    archive: zipfile.ZipFile,
    *,
    cache: dict[str, dict[str, str]],
) -> str | None:
    if block.artifact_kind not in {"image", "figure"}:
        return None
    chapter_path = safe_epub_archive_path(
        block.source_metadata.get("source_path")
        or block.source_metadata.get("href")
    )
    if chapter_path is None:
        return None
    caption_signature = _normalize_figure_caption_signature(block.source_text or "")
    if not caption_signature:
        return None
    figure_index = cache.get(chapter_path)
    if figure_index is None:
        figure_index = index_epub_figure_archive_paths(archive, chapter_path)
        cache[chapter_path] = figure_index
    return figure_index.get(caption_signature)


def index_epub_figure_archive_paths(
    archive: zipfile.ZipFile,
    chapter_path: str,
) -> dict[str, str]:
    try:
        raw = archive.read(chapter_path)
        root = _parse_xml_document(raw)
    except (KeyError, ET.ParseError, UnicodeDecodeError):
        return index_epub_figure_archive_paths_from_html(
            archive,
            chapter_path,
            raw if "raw" in locals() else None,
        )

    base_dir = posixpath.dirname(chapter_path)
    archive_path_by_caption_signature: dict[str, str] = {}
    for element in root.iter():
        local_name = _local_name(element.tag)
        class_tokens = _element_class_tokens(element)
        if not _figure_like_container(local_name, class_tokens, element):
            continue
        image = _first_descendant(element, {"img"})
        if image is None:
            continue
        image_src = str(image.attrib.get("src") or "").strip()
        if not image_src:
            continue
        archive_path = safe_epub_archive_path(_join_path(base_dir, image_src))
        if archive_path is None:
            continue
        caption_text = _figure_caption_text(element)
        caption_signature = _normalize_figure_caption_signature(caption_text)
        if not caption_signature:
            continue
        archive_path_by_caption_signature.setdefault(caption_signature, archive_path)
    return archive_path_by_caption_signature


def index_epub_figure_archive_paths_from_html(
    archive: zipfile.ZipFile,
    chapter_path: str,
    raw: bytes | None = None,
) -> dict[str, str]:
    try:
        html_text = (raw if raw is not None else archive.read(chapter_path)).decode("utf-8", errors="replace")
    except KeyError:
        return {}
    parser = _FallbackEpubFigureIndexParser(
        base_dir=posixpath.dirname(chapter_path),
        path_normalizer=safe_epub_archive_path,
    )
    parser.feed(html_text)
    parser.close()
    return {
        caption_signature: archive_path
        for caption_signature, archive_path in parser.archive_path_by_caption_signature.items()
        if archive_path in archive.namelist()
    }


def safe_epub_archive_path(candidate: object) -> str | None:
    if not isinstance(candidate, str):
        return None
    normalized = posixpath.normpath(candidate.replace("\\", "/").strip())
    if not normalized or normalized == "." or normalized.startswith("/"):
        return None
    normalized_path = PurePosixPath(normalized)
    if any(part == ".." for part in normalized_path.parts):
        return None
    return normalized_path.as_posix()
