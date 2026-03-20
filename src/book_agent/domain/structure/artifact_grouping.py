from __future__ import annotations

import re
from typing import Any

from book_agent.domain.enums import BlockType

_HEADING_LEAD_PATTERN = re.compile(
    r"^(?:chapter|part|appendix|abstract|introduction|references|conclusion|\d+(?:\.\d+)*\s+[A-Z])\b",
    re.IGNORECASE,
)
_CAPTION_LEAD_PATTERN = re.compile(
    r"^(?:(?:figure|fig\.|image|diagram|chart|table)\s+"
    r"(?:\(?\d+(?:\.\d+)*[A-Za-z]?\)?|[A-Z])"
    r"(?:(?:[.:\-\u2013\u2014]\s+)\S+|\s+(?-i:[A-Z])[^\n]{2,})"
    r"|(?:eq(?:uation)?\.?)\s*(?:\(\s*\d+(?:\.\d+)*[A-Za-z]?\s*\)|\d+(?:\.\d+)*[A-Za-z]?)(?:[.:-]\s+)\S+)",
    re.IGNORECASE,
)
_CODEISH_LINE_PATTERN = re.compile(
    r"^(?:"
    r"async\s+def\b.+:"
    r"|def\b.+:"
    r"|class\b.+:"
    r"|import\b.+"
    r"|from\s+\S+\s+import\b.+"
    r"|return\b.+"
    r"|yield\b.+"
    r"|raise\b.+"
    r"|pass\b.*"
    r"|break\b.*"
    r"|continue\b.*"
    r"|try:"
    r"|finally:"
    r"|except\b.*:"
    r"|if\b.+:"
    r"|elif\b.+:"
    r"|else:"
    r"|for\b.+\bin\b.+:"
    r"|while\b.+:"
    r"|with\b.+:"
    r")$",
    re.IGNORECASE,
)
_GENERIC_PROSE_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "our",
    "that",
    "the",
    "their",
    "them",
    "these",
    "this",
    "those",
    "through",
    "to",
    "was",
    "we",
    "what",
    "when",
    "which",
    "while",
    "with",
    "you",
    "your",
}
_FIGURE_CONTEXT_CUE_PATTERN = re.compile(
    r"\b(?:figure|fig\.|image|diagram|chart|overview|pipeline|workflow|architecture|visuali[sz]e|shows?|illustrates?|depicts?)\b",
    re.IGNORECASE,
)
_TABLE_CONTEXT_CUE_PATTERN = re.compile(
    r"\b(?:table|result(?:s)?|metric(?:s)?|performance|comparison|compare[sd]?|summari[sz]es?|lists?|reports?|rows?|columns?|ablation|accuracy|bleu|score(?:s)?)\b",
    re.IGNORECASE,
)
_EQUATION_CONTEXT_CUE_PATTERN = re.compile(
    r"\b(?:where|denotes?|represents?|corresponds?\s+to|distribution|probability|objective|constraint|formula|equation|loss|defined\s+as)\b",
    re.IGNORECASE,
)


def normalize_artifact_role(role: str | None, block_type: BlockType | str | None) -> str | None:
    normalized_role = str(role or "").strip().casefold()
    if normalized_role in {"image", "figure"}:
        return "image"
    if normalized_role in {"table", "table_like"}:
        return "table"
    if normalized_role == "equation":
        return "equation"

    if block_type is None:
        return None
    try:
        effective_block_type = block_type if isinstance(block_type, BlockType) else BlockType(str(block_type))
    except ValueError:
        return None
    if effective_block_type in {BlockType.IMAGE, BlockType.FIGURE}:
        return "image"
    if effective_block_type == BlockType.TABLE:
        return "table"
    if effective_block_type == BlockType.EQUATION:
        return "equation"
    return None


def is_academic_paper_document(document: object | None) -> bool:
    metadata = getattr(document, "metadata_json", None)
    if not isinstance(metadata, dict):
        return False
    profile = metadata.get("pdf_profile")
    if not isinstance(profile, dict):
        return False
    return str(profile.get("recovery_lane") or "").strip() == "academic_paper"


def looks_like_artifact_group_context_text(
    text: str,
    artifact_role: str,
    *,
    academic_paper: bool,
) -> bool:
    normalized = normalize_text(text)
    if len(normalized) < 48 or len(normalized) > 900:
        return False
    if _CAPTION_LEAD_PATTERN.match(normalized) or _HEADING_LEAD_PATTERN.match(normalized):
        return False
    if looks_like_codeish_text(normalized):
        return False

    tokens = re.findall(r"[A-Za-z][A-Za-z0-9'-]*", normalized.casefold())
    if len(tokens) < 8:
        return False

    cue_pattern = _cue_pattern_for_role(artifact_role)
    if cue_pattern is not None and cue_pattern.search(normalized):
        return True

    stopword_hits = sum(1 for token in tokens if token in _GENERIC_PROSE_STOPWORDS)
    sentence_punctuation = len(re.findall(r"[.!?](?:[\"'\)\]\u201d\u2019])?(?:\s|$)", normalized))
    if academic_paper and stopword_hits >= max(4, len(tokens) // 8) and (sentence_punctuation >= 1 or len(tokens) >= 18):
        return True
    return False


def resolve_artifact_group_context_ids(
    blocks: list[object],
    *,
    academic_paper: bool,
) -> dict[str, list[str]]:
    blocks_by_id = {str(getattr(block, "id", "")): block for block in blocks if getattr(block, "id", None)}
    sorted_blocks = sorted(blocks, key=_block_sort_key)
    explicit: dict[str, list[str]] = {}
    claimed_context_ids: set[str] = set()
    for artifact in sorted_blocks:
        metadata = _block_metadata(artifact)
        block_id = str(getattr(artifact, "id", "")).strip()
        if not block_id:
            continue
        candidate_ids = [
            str(candidate).strip()
            for candidate in list(metadata.get("artifact_group_context_block_ids") or [])
            if isinstance(candidate, str) and str(candidate).strip() in blocks_by_id
        ]
        if candidate_ids:
            explicit[block_id] = candidate_ids
            claimed_context_ids.update(candidate_ids)

    result = dict(explicit)
    for artifact in sorted_blocks:
        block_id = str(getattr(artifact, "id", "")).strip()
        if not block_id or block_id in result:
            continue
        role = normalize_artifact_role(
            _block_metadata(artifact).get("pdf_block_role"),
            getattr(artifact, "block_type", None),
        )
        if role not in {"image", "table", "equation"}:
            continue
        caption_block = _linked_caption_block_for_artifact(artifact, sorted_blocks, blocks_by_id)
        if caption_block is None:
            continue
        inferred_ids = _infer_adjacent_group_context_ids(
            sorted_blocks,
            artifact,
            caption_block,
            claimed_context_ids,
            artifact_role=role,
            academic_paper=academic_paper,
        )
        if inferred_ids:
            result[block_id] = inferred_ids
            claimed_context_ids.update(inferred_ids)
    return result


def normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def persisted_block_page_bbox(block: object, page_number: int) -> list[float] | None:
    source_bbox_json = _block_metadata(block).get("source_bbox_json")
    if not isinstance(source_bbox_json, dict):
        return None
    regions = source_bbox_json.get("regions")
    if not isinstance(regions, list):
        return None
    for region in regions:
        if not isinstance(region, dict) or int(region.get("page_number", -1)) != page_number:
            continue
        bbox = region.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        try:
            return [float(value) for value in bbox]
        except (TypeError, ValueError):
            return None
    return None


def horizontal_overlap_ratio(left: list[float], right: list[float]) -> float:
    overlap = min(left[2], right[2]) - max(left[0], right[0])
    if overlap <= 0:
        return 0.0
    left_width = max(left[2] - left[0], 1.0)
    right_width = max(right[2] - right[0], 1.0)
    return overlap / min(left_width, right_width)


def looks_like_codeish_text(text: str) -> bool:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    keyword_hits = sum(1 for line in lines[:12] if _CODEISH_LINE_PATTERN.match(line))
    delimiter_hits = sum(
        1
        for line in lines[:12]
        if any(token in line for token in ("(", ")", "[", "]", "{", "}"))
        and any(token in line for token in ("=", ":", ","))
    )
    return keyword_hits >= 1 or delimiter_hits >= 3


def _cue_pattern_for_role(artifact_role: str) -> re.Pattern[str] | None:
    role = str(artifact_role or "").strip().casefold()
    if role == "image":
        return _FIGURE_CONTEXT_CUE_PATTERN
    if role == "table":
        return _TABLE_CONTEXT_CUE_PATTERN
    if role == "equation":
        return _EQUATION_CONTEXT_CUE_PATTERN
    return None


def _block_metadata(block: object) -> dict[str, Any]:
    metadata = getattr(block, "source_span_json", None)
    if isinstance(metadata, dict):
        return metadata
    metadata = getattr(block, "metadata", None)
    return metadata if isinstance(metadata, dict) else {}


def _block_sort_key(block: object) -> tuple[int, int, int]:
    metadata = _block_metadata(block)
    page_start = int(metadata.get("source_page_start", 0) or 0)
    reading_order_index = int(metadata.get("reading_order_index", 0) or 0)
    ordinal = int(getattr(block, "ordinal", 0) or 0)
    return (page_start, reading_order_index, ordinal)


def _linked_caption_block_for_artifact(
    artifact: object,
    blocks: list[object],
    blocks_by_id: dict[str, object],
) -> object | None:
    metadata = _block_metadata(artifact)
    linked_caption_block_id = metadata.get("linked_caption_block_id")
    if isinstance(linked_caption_block_id, str):
        linked_caption_block = blocks_by_id.get(linked_caption_block_id)
        if linked_caption_block is not None:
            return linked_caption_block
    artifact_id = str(getattr(artifact, "id", "")).strip()
    if not artifact_id:
        return None
    for candidate in blocks:
        candidate_metadata = _block_metadata(candidate)
        if str(candidate_metadata.get("caption_for_block_id") or "").strip() == artifact_id:
            return candidate
    return None


def _infer_adjacent_group_context_ids(
    sorted_blocks: list[object],
    artifact: object,
    caption_block: object,
    claimed_context_ids: set[str],
    *,
    artifact_role: str,
    academic_paper: bool,
) -> list[str]:
    metadata = _block_metadata(artifact)
    page_number = int(metadata.get("source_page_start", 0) or 0)
    if page_number <= 0:
        return []
    artifact_bbox = persisted_block_page_bbox(artifact, page_number)
    caption_bbox = persisted_block_page_bbox(caption_block, page_number)
    if artifact_bbox is None and caption_bbox is None:
        return []
    cluster_bbox = _union_bbox(artifact_bbox, caption_bbox)
    if cluster_bbox is None:
        return []

    block_ids_in_order = {
        str(getattr(block, "id", "")).strip(): index
        for index, block in enumerate(sorted_blocks)
        if getattr(block, "id", None)
    }
    artifact_id = str(getattr(artifact, "id", "")).strip()
    caption_id = str(getattr(caption_block, "id", "")).strip()
    if not artifact_id or not caption_id:
        return []
    start_index = max(block_ids_in_order.get(artifact_id, -1), block_ids_in_order.get(caption_id, -1))
    if start_index < 0:
        return []

    cluster_bottom = cluster_bbox[3]
    cluster_center = (cluster_bbox[0] + cluster_bbox[2]) / 2.0
    cluster_width = max(cluster_bbox[2] - cluster_bbox[0], 1.0)
    artifact_reading_order = int(metadata.get("reading_order_index", 0) or 0)
    caption_reading_order = int(_block_metadata(caption_block).get("reading_order_index", 0) or 0)
    cluster_reading_order = max(artifact_reading_order, caption_reading_order)

    for candidate in sorted_blocks[start_index + 1:]:
        candidate_id = str(getattr(candidate, "id", "")).strip()
        if not candidate_id or candidate_id in claimed_context_ids:
            continue
        candidate_metadata = _block_metadata(candidate)
        candidate_page_start = int(candidate_metadata.get("source_page_start", 0) or 0)
        candidate_page_end = int(candidate_metadata.get("source_page_end", candidate_page_start) or candidate_page_start)
        if candidate_page_start != page_number or candidate_page_end != page_number:
            if candidate_page_start > page_number:
                break
            continue

        candidate_role = str(candidate_metadata.get("pdf_block_role") or "").strip().casefold()
        if candidate_role in {"header", "footer", "toc_entry", "footnote"}:
            continue
        if candidate_role in {"caption", "image", "figure", "table_like", "equation", "heading"}:
            break
        if getattr(candidate, "block_type", None) not in {BlockType.PARAGRAPH, BlockType.QUOTE, BlockType.LIST_ITEM}:
            break

        candidate_bbox = persisted_block_page_bbox(candidate, page_number)
        if candidate_bbox is None:
            break
        gap = candidate_bbox[1] - cluster_bottom
        candidate_reading_order = int(candidate_metadata.get("reading_order_index", 0) or 0)
        if candidate_reading_order <= cluster_reading_order:
            continue
        if candidate_reading_order - cluster_reading_order > 4:
            break
        if gap < -12.0:
            continue
        if gap > 96.0:
            break

        overlap_ratio = horizontal_overlap_ratio(cluster_bbox, candidate_bbox)
        center_distance = abs(((candidate_bbox[0] + candidate_bbox[2]) / 2.0) - cluster_center)
        if overlap_ratio < 0.12 and center_distance > cluster_width * 0.9:
            break
        if not looks_like_artifact_group_context_text(
            str(getattr(candidate, "source_text", "") or ""),
            artifact_role,
            academic_paper=academic_paper,
        ):
            break
        return [candidate_id]
    return []


def _union_bbox(left: list[float] | None, right: list[float] | None) -> list[float] | None:
    if left is None:
        return right
    if right is None:
        return left
    return [
        min(left[0], right[0]),
        min(left[1], right[1]),
        max(left[2], right[2]),
        max(left[3], right[3]),
    ]
