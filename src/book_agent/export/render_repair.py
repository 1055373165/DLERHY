"""Pure repairs of merged render blocks before rendering.

Code/prose splits, refresh-split restoration, list and reference listing
layout, and merges of adjacent heading, code and prose fragments.
"""


from __future__ import annotations

import re
from dataclasses import replace

from book_agent.domain.enums import (
    BlockType,
    SentenceStatus,
)
from book_agent.domain.structure.geometry import horizontal_overlap_ratio
from book_agent.export import code_text
from book_agent.export.common import (
    _APPENDIX_TITLE_PATTERN,
    _BOOK_ALLOWED_REFERENCE_HEADINGS,
    _BOOK_PROSE_HEADING_VERB_PATTERN,
    _CHAPTER_LOWERCASE_TAIL_PATTERN,
    _CJK_CHAR_PATTERN,
    _MAIN_CHAPTER_TITLE_PATTERN,
    _ORDERED_LIST_LINE_PATTERN,
    _PURE_CHAPTER_LABEL_PATTERN,
    _REFERENCE_ENTRY_MARKER_PATTERN,
    _REFERENCE_LOCATOR_PATTERN,
    _UNORDERED_LIST_LINE_PATTERN,
    _URL_ONLY_PATTERN,
    _is_academic_paper_document,
    _is_pdf_document,
    _leading_whitespace_width,
    _looks_like_heading_continuation_fragment,
    _normalize_render_text,
    _normalize_signature_text,
)
from book_agent.export.models import (
    MergedRenderBlock,
)
from book_agent.infra.repositories.export import ChapterExportBundle
from book_agent.ingestion.text import (
    _TERMINAL_PUNCTUATION,
    _expanded_code_candidate_lines,
    _looks_like_code,
    _looks_like_code_continuation_line,
    _looks_like_code_docstring_line,
    _looks_like_embedded_code_line,
    _looks_like_labeled_prose_line,
    _looks_like_prose_line_group,
    _looks_like_sentence_prose_line,
    _looks_like_shell_command_line,
    _looks_like_splitworthy_single_line_code_fragment,
)


def normalize_pdf_body_render_texts(
    *,
    document,
    block_type: BlockType | None,
    render_mode: str,
    source_text: str,
    block_sentences: list[object],
    sentence_targets: dict[str, list[str]],
    target_ids: list[str],
    target_map: dict[str, object],
    source_metadata: dict[str, object],
) -> tuple[str, list[str], str | None]:
    if not _is_pdf_document(document) or render_mode != "zh_primary_with_optional_source":
        return source_text, target_ids, None
    if block_type == BlockType.HEADING:
        normalized_source_text = join_sentence_source_texts(block_sentences) or source_text
        if normalized_source_text != source_text:
            source_metadata["recovery_flags"] = list(
                dict.fromkeys(
                    [
                        *list(source_metadata.get("recovery_flags") or []),
                        "export_pdf_source_soft_wrap_normalized",
                    ]
                )
            )
        return normalized_source_text, target_ids, None
    if block_type not in {BlockType.PARAGRAPH, BlockType.QUOTE} or not block_sentences:
        return source_text, target_ids, None

    grouped_sentences = group_block_sentences_by_target_id(
        block_sentences,
        sentence_targets=sentence_targets,
        target_map=target_map,
    )
    normalized_source_text = join_sentence_source_texts(block_sentences) or source_text
    normalized_target_text: str | None = None
    normalized_target_ids = target_ids

    if should_restore_pdf_paragraph_breaks(grouped_sentences):
        source_paragraphs = [
            join_sentence_source_texts(group_sentences)
            for _target_id, group_sentences in grouped_sentences
            if group_sentences
        ]
        paragraph_target_ids: list[str] = []
        paragraph_target_texts: list[str] = []
        for target_id, _group_sentences in grouped_sentences:
            if not target_id or target_id not in target_map or target_id in paragraph_target_ids:
                continue
            paragraph_target_ids.append(target_id)
            paragraph_target_texts.append(str(getattr(target_map[target_id], "text_zh", "") or "").strip())
        source_paragraphs = [paragraph for paragraph in source_paragraphs if paragraph]
        paragraph_target_texts = [paragraph for paragraph in paragraph_target_texts if paragraph]
        if len(source_paragraphs) >= 2:
            normalized_source_text = "\n\n".join(source_paragraphs)
        if len(paragraph_target_texts) >= 2:
            normalized_target_text = "\n\n".join(paragraph_target_texts)
            normalized_target_ids = paragraph_target_ids
        source_metadata["recovery_flags"] = list(
            dict.fromkeys(
                [
                    *list(source_metadata.get("recovery_flags") or []),
                    "export_pdf_paragraph_breaks_restored",
                ]
            )
        )

    if normalized_source_text != source_text and "export_pdf_paragraph_breaks_restored" not in list(
        source_metadata.get("recovery_flags") or []
    ):
        source_metadata["recovery_flags"] = list(
            dict.fromkeys(
                [
                    *list(source_metadata.get("recovery_flags") or []),
                    "export_pdf_source_soft_wrap_normalized",
                ]
            )
        )
    return normalized_source_text, normalized_target_ids, normalized_target_text


def normalize_academic_paper_render_blocks(
    bundle: ChapterExportBundle,
    render_blocks: list[MergedRenderBlock],
) -> list[MergedRenderBlock]:
    if bundle.chapter.ordinal != 1:
        return render_blocks
    normalized_blocks: list[MergedRenderBlock] = []
    for block in render_blocks:
        normalized_blocks.extend(split_academic_paper_frontmatter_block(block))
    return normalized_blocks


def split_mixed_book_code_prose_render_block(
    block: MergedRenderBlock,
) -> list[MergedRenderBlock]:
    if block.render_mode != "source_artifact_full_width" or block.artifact_kind != "code":
        return [block]
    raw_lines = _expanded_code_candidate_lines(block.source_text or "")
    if len(raw_lines) < 2:
        return [block]

    first_code_index: int | None = None
    last_code_index: int | None = None
    code_line_count = 0
    for index, line in enumerate(raw_lines):
        if (
            _looks_like_embedded_code_line(line)
            or _looks_like_code_docstring_line(line)
            or _looks_like_code_continuation_line(line, raw_lines[:index])
        ):
            if first_code_index is None:
                first_code_index = index
            last_code_index = index
            code_line_count += 1

    if first_code_index is None or last_code_index is None or code_line_count < 2:
        if not (
            first_code_index == 0
            and last_code_index == 0
            and len(raw_lines) >= 2
            and _looks_like_splitworthy_single_line_code_fragment(raw_lines[0])
        ):
            return [block]

    leading_lines = raw_lines[:first_code_index]
    code_lines = raw_lines[first_code_index : last_code_index + 1]
    trailing_lines = raw_lines[last_code_index + 1 :]
    if leading_lines and not _looks_like_prose_line_group(leading_lines):
        return [block]
    if trailing_lines and not _looks_like_prose_line_group(trailing_lines):
        return [block]
    if not (leading_lines or trailing_lines):
        return [block]
    single_line_code_ok = len(code_lines) == 1 and _looks_like_splitworthy_single_line_code_fragment(code_lines[0])
    if not _looks_like_code("\n".join(code_lines), len(code_lines)) and not single_line_code_ok:
        return [block]

    def _derived_metadata(role: str, split_kind: str) -> dict[str, object]:
        metadata = dict(block.source_metadata)
        recovery_flags = list(metadata.get("recovery_flags") or [])
        metadata["recovery_flags"] = list(
            dict.fromkeys([*recovery_flags, "export_mixed_code_prose_split", split_kind])
        )
        metadata["pdf_block_role"] = role
        metadata["pdf_mixed_code_prose_split"] = split_kind
        return metadata

    def _persisted_split_target(split_kind: str, source_text: str) -> tuple[str | None, list[str]]:
        repairs = block.source_metadata.get("mixed_code_prose_repair_targets")
        if not isinstance(repairs, list):
            return None, []
        source_signature = _normalize_signature_text(source_text)
        for repair in repairs:
            if not isinstance(repair, dict):
                continue
            if str(repair.get("split_kind") or "").strip() != split_kind:
                continue
            if str(repair.get("source_signature") or "").strip() != source_signature:
                continue
            target_text = str(repair.get("target_text") or "").strip() or None
            if target_text is None:
                continue
            target_segment_ids = [str(item) for item in list(repair.get("target_segment_ids") or []) if str(item)]
            return target_text, target_segment_ids
        return None, []

    prose_target_text = str(block.target_text or "").strip() or None
    prose_target_ids = list(block.target_segment_ids) if prose_target_text else []
    leading_target_text = prose_target_text if leading_lines and not trailing_lines else None
    trailing_target_text = prose_target_text if trailing_lines and not leading_lines else None
    leading_target_ids = prose_target_ids if leading_target_text else []
    trailing_target_ids = prose_target_ids if trailing_target_text else []
    if leading_lines and leading_target_text is None:
        leading_target_text, leading_target_ids = _persisted_split_target(
            "leading_prose_prefix",
            "\n".join(leading_lines),
        )
    if trailing_lines and trailing_target_text is None:
        trailing_target_text, trailing_target_ids = _persisted_split_target(
            "trailing_prose_suffix",
            "\n".join(trailing_lines),
        )

    fragments: list[MergedRenderBlock] = []
    if leading_lines:
        fragments.append(
            replace(
                block,
                block_id=f"{block.block_id}::leading-prose",
                block_type=BlockType.PARAGRAPH.value,
                render_mode="zh_primary_with_optional_source",
                artifact_kind=None,
                source_text="\n".join(leading_lines),
                target_text=leading_target_text,
                source_metadata=_derived_metadata("body", "leading_prose_prefix"),
                target_segment_ids=leading_target_ids,
                is_expected_source_only=False,
                notice=None,
            )
        )
    fragments.append(
        replace(
            block,
            block_type=BlockType.CODE.value,
            source_text="\n".join(code_lines),
            target_text=None,
            source_metadata=_derived_metadata("code_like", "embedded_code_span"),
            target_segment_ids=[],
        )
    )
    if trailing_lines:
        fragments.append(
            replace(
                block,
                block_id=f"{block.block_id}::trailing-prose",
                block_type=BlockType.PARAGRAPH.value,
                render_mode="zh_primary_with_optional_source",
                artifact_kind=None,
                source_text="\n".join(trailing_lines),
                target_text=trailing_target_text,
                source_metadata=_derived_metadata("body", "trailing_prose_suffix"),
                target_segment_ids=trailing_target_ids,
                is_expected_source_only=False,
                notice=None,
            )
        )
    return fragments


def looks_like_refresh_split_code_prefix_terminal_line(
    line: str,
    previous_lines: list[str],
) -> bool:
    if (
        _looks_like_embedded_code_line(line)
        or _looks_like_code_docstring_line(line)
        or _looks_like_code_continuation_line(line, previous_lines)
    ):
        return True
    if _looks_like_prose_line_group([line]):
        return False
    candidate_lines = [*previous_lines, line]
    return _looks_like_code("\n".join(candidate_lines), max(2, len(candidate_lines)))


def looks_like_refresh_split_prose_onset(lines: list[str]) -> bool:
    if not lines:
        return False
    first_line = lines[0]
    if _looks_like_sentence_prose_line(first_line):
        return True
    if looks_like_refresh_split_code_start_line(first_line):
        return False
    if code_text.looks_like_codeish_line_for_artifact_rejection(first_line):
        return False
    window_limit = min(3, len(lines))
    for window in range(2, window_limit + 1):
        if _looks_like_prose_line_group(lines[:window]):
            return True
    return False


def looks_like_refresh_split_code_start_line(line: str) -> bool:
    stripped = (line or "").strip()
    if not stripped:
        return False
    if looks_like_refresh_split_code_prefix_terminal_line(stripped, []):
        return True
    if re.match(r"^(?:await|return|yield|raise|break|continue)\b", stripped):
        return True
    if re.match(r"^[\]\)\}]\s*,?\s*$", stripped):
        return True
    if re.match(r"^[A-Za-z_][\w.]*\([^)]*\)\s*$", stripped):
        return True
    return False


def refresh_split_fragment_target_matches_prose_text(
    fragment: dict[str, object],
    prose_text: str,
) -> bool:
    if not prose_text:
        return False
    target_text = (
        str(fragment.get("target_text") or "").strip()
        or str(fragment.get("repair_target_text") or "").strip()
    )
    if not target_text:
        return False
    current_signature = _normalize_signature_text(prose_text)
    stored_signature = str(fragment.get("repair_source_signature") or "").strip()
    if stored_signature:
        return stored_signature == current_signature
    raw_source = str(fragment.get("source_text") or "").strip()
    return _normalize_signature_text(raw_source) == current_signature


def split_leading_code_prefix_from_refresh_fragment(
    block: MergedRenderBlock,
    fragment: dict[str, object],
) -> tuple[str, str] | None:
    raw_block_type = str(fragment.get("block_type") or BlockType.PARAGRAPH.value).strip().casefold()
    if raw_block_type != BlockType.PARAGRAPH.value:
        return None
    fragment_source = str(fragment.get("source_text") or "")
    fragment_lines = _expanded_code_candidate_lines(fragment_source)
    if len(fragment_lines) < 2:
        return None
    previous_lines = _expanded_code_candidate_lines(block.source_text or "")
    if not previous_lines:
        return None
    if (
        raw_block_type == BlockType.PARAGRAPH.value
        and looks_like_refresh_split_prose_onset(fragment_lines)
        and not looks_like_refresh_split_code_start_line(fragment_lines[0])
    ):
        return None
    lookahead_limit = min(8, len(fragment_lines))
    has_leading_code_prefix = any(
        looks_like_refresh_split_code_prefix_terminal_line(
            fragment_lines[index],
            [*previous_lines, *fragment_lines[:index]],
        )
        or looks_like_refresh_split_code_start_line(fragment_lines[index])
        for index in range(lookahead_limit)
    )
    if not has_leading_code_prefix:
        return None

    for split_index in range(1, len(fragment_lines)):
        prefix_lines = fragment_lines[:split_index]
        remainder_lines = fragment_lines[split_index:]
        if not remainder_lines or not looks_like_refresh_split_prose_onset(remainder_lines):
            continue
        if not looks_like_refresh_split_code_prefix_terminal_line(
            prefix_lines[-1],
            [*previous_lines, *prefix_lines[:-1]],
        ):
            continue
        merged_lines = [*previous_lines, *prefix_lines]
        if not _looks_like_code("\n".join(merged_lines), max(2, len(merged_lines))):
            continue
        code_prefix = "\n".join(prefix_lines).strip()
        prose_suffix = "\n".join(remainder_lines).strip()
        if code_prefix and prose_suffix:
            return code_prefix, prose_suffix
    return None


def extract_refresh_split_fragment_prose_text(
    block: MergedRenderBlock,
    fragment: dict[str, object],
) -> str | None:
    fragment_source = str(fragment.get("source_text") or "")
    if not fragment_source.strip():
        return None
    raw_block_type = str(fragment.get("block_type") or BlockType.PARAGRAPH.value).strip().casefold()
    fragment_lines = _expanded_code_candidate_lines(fragment_source)
    if not fragment_lines:
        return None
    split_prefix = split_leading_code_prefix_from_refresh_fragment(block, fragment)
    if split_prefix is not None:
        _, prose_suffix = split_prefix
        return prose_suffix or None

    if raw_block_type == BlockType.PARAGRAPH.value and _looks_like_prose_line_group(fragment_lines):
        return fragment_source.strip()

    if raw_block_type != BlockType.CODE.value:
        return None

    fragment_block = replace(
        block,
        block_id=f"{block.block_id}::refresh-fragment-prose-scan",
        block_type=BlockType.CODE.value,
        render_mode="source_artifact_full_width",
        artifact_kind="code",
        source_text=fragment_source,
        target_text=None,
        source_metadata=dict(fragment.get("source_metadata") or {}),
        source_sentence_ids=[],
        target_segment_ids=[],
        is_expected_source_only=True,
        notice="代码保持原样",
    )
    split_blocks = split_mixed_book_code_prose_render_block(fragment_block)
    prose_blocks = [candidate for candidate in split_blocks if candidate.block_type == BlockType.PARAGRAPH.value]
    code_blocks = [candidate for candidate in split_blocks if candidate.block_type == BlockType.CODE.value]
    if len(prose_blocks) != 1 or not code_blocks:
        return None
    return (prose_blocks[0].source_text or "").strip() or None


def should_restore_labeled_prose_refresh_split(
    block: MergedRenderBlock,
    fragment: dict[str, object],
) -> bool:
    if not _looks_like_labeled_prose_line(block.source_text or ""):
        return False
    raw_block_type = str(fragment.get("block_type") or BlockType.PARAGRAPH.value).strip().casefold()
    if raw_block_type != BlockType.PARAGRAPH.value:
        return False
    fragment_source = str(fragment.get("source_text") or "")
    fragment_lines = _expanded_code_candidate_lines(fragment_source)
    return bool(fragment_lines) and _looks_like_prose_line_group(fragment_lines)


def restore_labeled_prose_refresh_split(
    block: MergedRenderBlock,
    fragment: dict[str, object],
) -> MergedRenderBlock:
    source_metadata = dict(block.source_metadata)
    recovery_flags = list(source_metadata.get("recovery_flags") or [])
    source_metadata["recovery_flags"] = list(
        dict.fromkeys([*recovery_flags, "export_refresh_split_labeled_prose_restored"])
    )
    source_metadata["pdf_block_role"] = "body"
    source_metadata.pop("refresh_split_render_fragments", None)
    return replace(
        block,
        block_type=BlockType.PARAGRAPH.value,
        render_mode="zh_primary_with_optional_source",
        artifact_kind=None,
        title=None,
        source_text="\n".join(
            segment
            for segment in [
                (block.source_text or "").rstrip("\n"),
                str(fragment.get("source_text") or "").lstrip("\n"),
            ]
            if segment
        ),
        source_metadata=source_metadata,
        is_expected_source_only=False,
        notice=None,
    )


def should_restore_code_refresh_split(
    block: MergedRenderBlock,
    fragment: dict[str, object],
) -> bool:
    fragment_source = str(fragment.get("source_text") or "")
    fragment_lines = _expanded_code_candidate_lines(fragment_source)
    if not fragment_lines:
        return False
    previous_lines = _expanded_code_candidate_lines(block.source_text or "")
    if not previous_lines:
        return False
    first_line = fragment_lines[0]
    if not (
        _looks_like_embedded_code_line(first_line)
        or _looks_like_code_docstring_line(first_line)
        or _looks_like_code_continuation_line(first_line, previous_lines)
    ):
        return False
    merged_source = "\n".join(
        segment
        for segment in [(block.source_text or "").rstrip("\n"), fragment_source.lstrip("\n")]
        if segment
    )
    merged_lines = _expanded_code_candidate_lines(merged_source)
    return _looks_like_code(merged_source, max(2, len(merged_lines)))


def restore_code_refresh_split(
    block: MergedRenderBlock,
    fragment: dict[str, object],
) -> MergedRenderBlock:
    source_metadata = dict(block.source_metadata)
    recovery_flags = list(source_metadata.get("recovery_flags") or [])
    source_metadata["recovery_flags"] = list(
        dict.fromkeys([*recovery_flags, "export_refresh_split_code_restored"])
    )
    source_metadata["pdf_block_role"] = "code_like"
    source_metadata.pop("refresh_split_render_fragments", None)
    return replace(
        block,
        source_text="\n".join(
            segment
            for segment in [
                (block.source_text or "").rstrip("\n"),
                str(fragment.get("source_text") or "").lstrip("\n"),
            ]
            if segment
        ),
        source_metadata=source_metadata,
        notice="代码保持原样",
    )


def infer_refresh_split_fragment_target_text(
    parent_block: MergedRenderBlock,
    fragment: dict[str, object],
    *,
    fragment_block_type: BlockType,
) -> str | None:
    if fragment_block_type != BlockType.PARAGRAPH:
        return None
    repair_target = str(fragment.get("repair_target_text") or "").strip()
    if repair_target:
        return repair_target
    parent_target = str(parent_block.target_text or "").strip()
    if not parent_target or not _CJK_CHAR_PATTERN.search(parent_target):
        return None
    parent_source = str(parent_block.source_text or "").strip()
    fragment_source = extract_refresh_split_fragment_prose_text(parent_block, fragment)
    if not parent_source or not fragment_source:
        return None
    if not _looks_like_shell_command_line(parent_source.splitlines()[0]):
        return None
    match = _CJK_CHAR_PATTERN.search(parent_target)
    if match is None or match.start() <= 0:
        return None
    candidate = parent_target[match.start() :].strip()
    return candidate or None


def should_drop_book_heading_label(
    bundle: ChapterExportBundle,
    index: int,
    block: MergedRenderBlock,
) -> bool:
    normalized = _normalize_render_text(block.source_text)
    if not normalized or not _PURE_CHAPTER_LABEL_PATTERN.match(normalized):
        return False
    chapter_title = _normalize_render_text(bundle.chapter.title_src)
    if chapter_title and chapter_title.casefold() == normalized.casefold():
        return True
    return index > 0


def repair_collapsed_list_target_text(block: MergedRenderBlock) -> MergedRenderBlock:
    if block.block_type not in {BlockType.PARAGRAPH.value, BlockType.QUOTE.value}:
        return block
    page_family = str(block.source_metadata.get("pdf_page_family") or "").strip().casefold()
    if page_family == "references":
        return block
    source_text = str(block.source_text or "")
    source_layouts = list_line_layouts(source_text)
    if len(source_layouts) < 2:
        return block
    target_text = str(block.target_text or "").strip()
    if not target_text:
        return block

    target_lines = [line.rstrip() for line in target_text.splitlines() if line.strip()]
    repaired_lines = (
        target_lines
        if len(target_lines) >= len(source_layouts)
        else split_inline_list_target_lines(target_text)
    )
    if len(repaired_lines) < len(source_layouts):
        return block
    repaired_lines = apply_list_line_layouts(source_layouts, repaired_lines)

    repaired_target_text = "\n".join(repaired_lines)
    if repaired_target_text == target_text:
        return block

    source_metadata = dict(block.source_metadata)
    recovery_flags = list(source_metadata.get("recovery_flags") or [])
    source_metadata["recovery_flags"] = list(
        dict.fromkeys([*recovery_flags, "export_book_list_target_layout_restored"])
    )
    return replace(block, target_text=repaired_target_text, source_metadata=source_metadata)


def list_line_layouts(text: str) -> list[tuple[int, str]]:
    raw_lines = [line for line in str(text or "").splitlines() if line.strip()]
    if len(raw_lines) < 2:
        raw_lines = split_inline_list_target_lines(text, preserve_leading_ws=True)
    layouts: list[tuple[int, str]] = []
    for raw_line in raw_lines:
        match = _UNORDERED_LIST_LINE_PATTERN.match(raw_line) or _ORDERED_LIST_LINE_PATTERN.match(raw_line)
        if match is None:
            continue
        layouts.append(
            (
                infer_list_indent_level(
                    str(match.group("indent") or ""),
                    str(match.group("marker") or ""),
                ),
                raw_line.strip(),
            )
        )
    return layouts


def infer_list_indent_level(leading_ws: str, marker: str) -> int:
    indent_width = _leading_whitespace_width(re.sub(r"[\u200b\ufeff]", "", leading_ws))
    if indent_width >= 3:
        return max(1, indent_width // 3)
    if marker in {"○", "◯", "◦"}:
        return 1
    return 0


def apply_list_line_layouts(
    source_layouts: list[tuple[int, str]],
    target_lines: list[str],
) -> list[str]:
    if len(target_lines) != len(source_layouts):
        return [line.strip() for line in target_lines if line.strip()]
    repaired: list[str] = []
    for (level, _source_line), target_line in zip(source_layouts, target_lines):
        stripped_target = target_line.strip()
        if not stripped_target:
            continue
        repaired.append(f"{'   ' * max(level, 0)}{stripped_target}")
    return repaired


def split_inline_list_target_lines(text: str, *, preserve_leading_ws: bool = False) -> list[str]:
    normalized = str(text or "").strip()
    if not normalized:
        return []
    prepared = re.sub(
        r"(?<!^)\s*(?=(?:[-*+•●▪◦○◯]|\d+[.)])(?:[\s\u200b\ufeff]*|$))",
        "\n",
        normalized,
    )
    if preserve_leading_ws:
        return [line.rstrip() for line in prepared.splitlines() if line.strip()]
    return [line.strip() for line in prepared.splitlines() if line.strip()]


def should_promote_book_block_to_code(block: MergedRenderBlock) -> bool:
    if block.render_mode == "source_artifact_full_width" and block.artifact_kind == "code":
        return False
    if block.block_type not in {BlockType.HEADING.value, BlockType.PARAGRAPH.value, BlockType.TABLE.value}:
        return False
    page_family = str(block.source_metadata.get("pdf_page_family") or "body").strip().casefold()
    if page_family == "references":
        return False
    normalized = _normalize_render_text(block.source_text)
    if not normalized or code_text.looks_like_prose_artifact_text(normalized, academic_paper=False):
        return False
    if code_text.looks_like_book_structural_heading_text(normalized):
        return False
    if code_text.looks_like_code_artifact_text(block.source_text or "", academic_paper=False):
        return True
    return code_text.looks_like_single_line_codeish_text(normalized)


def should_demote_book_code_block_to_paragraph(block: MergedRenderBlock) -> bool:
    if block.render_mode != "source_artifact_full_width" or block.artifact_kind != "code":
        return False
    page_family = str(block.source_metadata.get("pdf_page_family") or "body").strip().casefold()
    if page_family == "references" and code_text.looks_like_reference_listing_text(block.source_text or ""):
        return True
    normalized = _normalize_render_text(block.source_text)
    if not normalized:
        return False
    if code_text.looks_like_code_artifact_text(block.source_text or "", academic_paper=False):
        return False
    if code_text.looks_like_short_book_prose_line(normalized):
        return True
    return code_text.looks_like_prose_artifact_text(normalized, academic_paper=False)


def should_drop_demoted_book_code_target_text(block: MergedRenderBlock) -> bool:
    normalized_source = _normalize_render_text(block.source_text)
    normalized_target = _normalize_render_text(block.target_text)
    if not normalized_source or not normalized_target:
        return False
    if not code_text.looks_like_short_book_prose_line(normalized_source):
        return False
    return len(normalized_target) >= max(80, len(normalized_source) * 4)


def should_clear_suspicious_short_source_target_text(block: MergedRenderBlock) -> bool:
    if block.block_type not in {BlockType.PARAGRAPH.value, BlockType.QUOTE.value}:
        return False
    return should_drop_demoted_book_code_target_text(block)


_FONT_EMPHASIS_HEADING_FLAGS = frozenset({"embedded_book_styled_heading_recovered", "styled_heading_line_merged"})


def should_demote_book_heading_to_paragraph(
    bundle: ChapterExportBundle,
    index: int,
    block: MergedRenderBlock,
) -> bool:
    normalized = _normalize_render_text(block.source_text)
    if not normalized:
        return False
    chapter_title = _normalize_render_text(bundle.chapter.title_src)
    if index == 0 and chapter_title and normalized.casefold() == chapter_title.casefold():
        return False
    if code_text.looks_like_book_structural_heading_text(normalized):
        return False
    if code_text.looks_like_code_artifact_text(block.source_text or "", academic_paper=False):
        return False
    if code_text.looks_like_single_line_codeish_text(normalized):
        return False
    page_family = str(block.source_metadata.get("pdf_page_family") or "body").strip().casefold()
    lowered = normalized.casefold()
    token_count = len(re.findall(r"[A-Za-z][A-Za-z'-]*", normalized))
    if page_family == "references" and lowered not in _BOOK_ALLOWED_REFERENCE_HEADINGS:
        return True
    if _FONT_EMPHASIS_HEADING_FLAGS.intersection(block.source_metadata.get("recovery_flags") or ()):
        # The parser saw the line set apart in bold/larger type; the prose-shape rules
        # below would demote titles such as "7. Chart patterns like Triangles, ... etc."
        return False
    if _CHAPTER_LOWERCASE_TAIL_PATTERN.match(normalized):
        return True
    if normalized[:1].islower() and token_count >= 4:
        return True
    if token_count >= 10 and _BOOK_PROSE_HEADING_VERB_PATTERN.search(lowered):
        return True
    if token_count >= 14:
        return True
    if re.search(r"[.!?](?:[\"'\)\]\u201d\u2019])?$", normalized) and token_count >= 8:
        return True
    return False


def should_demote_book_heading_with_prose_target(
    bundle: ChapterExportBundle,
    block: MergedRenderBlock,
) -> bool:
    if block.block_type != BlockType.HEADING.value:
        return False
    target_text = _normalize_render_text(block.target_text)
    if not target_text:
        return False
    return looks_like_prose_title_text(
        target_text,
        source_heading_text=block.source_text,
        fallback_title=bundle.chapter.title_src,
    )


def replace_render_block_as_paragraph(
    block: MergedRenderBlock,
    *,
    flag: str,
    drop_target_text: bool = False,
) -> MergedRenderBlock:
    source_metadata = dict(block.source_metadata)
    recovery_flags = list(source_metadata.get("recovery_flags") or [])
    source_metadata["recovery_flags"] = list(dict.fromkeys([*recovery_flags, flag]))
    source_metadata["pdf_block_role"] = "body"
    return replace(
        block,
        block_type=BlockType.PARAGRAPH.value,
        render_mode="zh_primary_with_optional_source",
        artifact_kind=None,
        title=None,
        target_text=(None if drop_target_text else block.target_text),
        source_metadata=source_metadata,
        notice=None,
    )


def drop_render_block_target_text(
    block: MergedRenderBlock,
    *,
    flag: str,
) -> MergedRenderBlock:
    source_metadata = dict(block.source_metadata)
    recovery_flags = list(source_metadata.get("recovery_flags") or [])
    source_metadata["recovery_flags"] = list(dict.fromkeys([*recovery_flags, flag]))
    return replace(
        block,
        target_text=None,
        source_metadata=source_metadata,
    )


def replace_render_block_as_code(
    block: MergedRenderBlock,
    *,
    flag: str,
) -> MergedRenderBlock:
    source_metadata = dict(block.source_metadata)
    recovery_flags = list(source_metadata.get("recovery_flags") or [])
    source_metadata["recovery_flags"] = list(dict.fromkeys([*recovery_flags, flag]))
    source_metadata["pdf_block_role"] = "code_like"
    return replace(
        block,
        block_type=BlockType.CODE.value,
        render_mode="source_artifact_full_width",
        artifact_kind="code",
        title=None,
        source_metadata=source_metadata,
        notice="代码保持原样",
    )


def split_academic_paper_frontmatter_block(block: MergedRenderBlock) -> list[MergedRenderBlock]:
    if block.block_type != BlockType.PARAGRAPH.value:
        return [block]
    split_source = split_abstract_sections(block.source_text, markers=("Abstract",))
    if split_source is None:
        return [block]
    split_target = split_abstract_sections(block.target_text or "", markers=("摘要", "Abstract"))
    if split_target is None:
        split_target = ("", "", block.target_text or "")
    source_prefix, source_heading, source_body = split_source
    target_prefix, target_heading, target_body = split_target
    author_source = normalize_academic_frontmatter_prefix(source_prefix)
    author_target = normalize_academic_frontmatter_prefix(target_prefix)
    abstract_source = source_body.strip()
    abstract_target = target_body.strip()
    if not author_source and not abstract_source:
        return [block]

    fragments: list[MergedRenderBlock] = []
    if author_source:
        fragments.append(
            replace(
                block,
                block_id=f"{block.block_id}::frontmatter",
                source_text=author_source,
                target_text=author_target or None,
            )
        )
    heading_source = (source_heading or "Abstract").strip()
    heading_target = (target_heading or "摘要").strip()
    if abstract_source:
        fragments.append(
            MergedRenderBlock(
                block_id=f"{block.block_id}::abstract-heading",
                chapter_id=block.chapter_id,
                block_type=BlockType.HEADING.value,
                render_mode="zh_primary_with_optional_source",
                artifact_kind=None,
                title=None,
                source_text=heading_source,
                target_text=heading_target or None,
                source_metadata=dict(block.source_metadata),
                source_sentence_ids=[],
                target_segment_ids=[],
                is_expected_source_only=False,
                notice=None,
            )
        )
        fragments.append(
            replace(
                block,
                block_id=f"{block.block_id}::abstract-body",
                source_text=abstract_source,
                target_text=abstract_target or None,
            )
        )
    return fragments or [block]


def split_abstract_sections(
    text: str,
    *,
    markers: tuple[str, ...],
) -> tuple[str, str, str] | None:
    normalized = (text or "").strip()
    if not normalized:
        return None
    for marker in markers:
        if re.match(r"^[A-Za-z0-9_ ]+$", marker):
            match = re.search(rf"\b{re.escape(marker)}\b", normalized, flags=re.IGNORECASE)
        else:
            match = re.search(re.escape(marker), normalized, flags=re.IGNORECASE)
        if match is None:
            continue
        prefix = normalized[: match.start()].strip()
        suffix = normalized[match.end():].lstrip(" :.-\n")
        return prefix, marker, suffix
    return None


def normalize_academic_frontmatter_prefix(text: str) -> str:
    normalized = (text or "").strip()
    if not normalized:
        return ""
    normalized = re.sub(r"\s*\n\s*", "\n", normalized)
    normalized = re.sub(r"[ \t]{2,}", " ", normalized)
    return normalized.strip()


def block_page_number(block: MergedRenderBlock) -> int | None:
    source_bbox_json = block.source_metadata.get("source_bbox_json")
    if isinstance(source_bbox_json, dict):
        regions = source_bbox_json.get("regions")
        if isinstance(regions, list) and regions and isinstance(regions[0], dict):
            page_number = regions[0].get("page_number")
            if isinstance(page_number, int):
                return page_number
    source_page_start = block.source_metadata.get("source_page_start")
    if isinstance(source_page_start, int):
        return source_page_start
    return None


def block_page_end(block: MergedRenderBlock) -> int | None:
    source_page_end = block.source_metadata.get("source_page_end")
    if isinstance(source_page_end, int):
        return source_page_end
    return block_page_number(block)


def block_bbox_regions(block: MergedRenderBlock) -> list[dict[str, object]]:
    source_bbox_json = block.source_metadata.get("source_bbox_json")
    if not isinstance(source_bbox_json, dict):
        return []
    regions = source_bbox_json.get("regions")
    if not isinstance(regions, list):
        return []
    return [region for region in regions if isinstance(region, dict)]


def should_merge_adjacent_heading_render_blocks(
    previous: MergedRenderBlock,
    current: MergedRenderBlock,
) -> bool:
    if previous.block_type != BlockType.HEADING.value or current.block_type != BlockType.HEADING.value:
        return False
    if previous.render_mode != "zh_primary_with_optional_source" or current.render_mode != "zh_primary_with_optional_source":
        return False
    if previous.chapter_id != current.chapter_id:
        return False
    previous_page = block_page_number(previous)
    current_page = block_page_number(current)
    if previous_page is None or current_page is None or previous_page != current_page:
        return False
    if block_page_end(previous) != previous_page or block_page_end(current) != current_page:
        return False
    previous_family = str(previous.source_metadata.get("pdf_page_family") or "body")
    current_family = str(current.source_metadata.get("pdf_page_family") or "body")
    if previous_family != "body" or current_family != "body":
        return False
    previous_index = previous.source_metadata.get("reading_order_index")
    current_index = current.source_metadata.get("reading_order_index")
    if isinstance(previous_index, int) and isinstance(current_index, int) and current_index - previous_index != 1:
        return False
    current_text = _normalize_render_text(current.source_text)
    previous_text = _normalize_render_text(previous.source_text)
    if code_text.looks_like_single_line_codeish_text(current_text):
        return False
    if current_text[:1].islower() and (
        _MAIN_CHAPTER_TITLE_PATTERN.match(previous_text) or _APPENDIX_TITLE_PATTERN.match(previous_text)
    ):
        return False
    if (
        len(re.findall(r"[A-Za-z][A-Za-z'-]*", current_text)) >= 10
        and _BOOK_PROSE_HEADING_VERB_PATTERN.search(current_text.casefold())
    ):
        return False
    if not _looks_like_heading_continuation_fragment(current.source_text):
        return False
    previous_regions = block_bbox_regions(previous)
    current_regions = block_bbox_regions(current)
    if not previous_regions or not current_regions:
        return True
    previous_bbox = previous_regions[-1].get("bbox")
    current_bbox = current_regions[0].get("bbox")
    if not (isinstance(previous_bbox, list) and isinstance(current_bbox, list)):
        return True
    try:
        gap = float(current_bbox[1]) - float(previous_bbox[3])
        x_delta = abs(float(previous_bbox[0]) - float(current_bbox[0]))
    except (TypeError, ValueError, IndexError):
        return True
    return gap <= 36.0 and x_delta <= 48.0


def merge_adjacent_heading_render_blocks(
    previous: MergedRenderBlock,
    current: MergedRenderBlock,
) -> MergedRenderBlock:
    merged_metadata = dict(previous.source_metadata)
    current_regions = block_bbox_regions(current)
    previous_regions = block_bbox_regions(previous)
    if previous_regions or current_regions:
        merged_metadata["source_bbox_json"] = {
            "regions": [*previous_regions, *current_regions],
        }
    merged_metadata["source_page_end"] = block_page_end(current)
    merged_metadata["recovery_flags"] = list(
        dict.fromkeys(
            [
                *list(previous.source_metadata.get("recovery_flags") or []),
                *list(current.source_metadata.get("recovery_flags") or []),
                "export_multiline_heading_merged",
            ]
        )
    )
    return replace(
        previous,
        source_text=merge_render_text_fragments(previous.source_text, current.source_text),
        target_text=merge_render_text_fragments(previous.target_text or "", current.target_text or ""),
        source_metadata=merged_metadata,
        source_sentence_ids=list(dict.fromkeys([*previous.source_sentence_ids, *current.source_sentence_ids])),
        target_segment_ids=list(dict.fromkeys([*previous.target_segment_ids, *current.target_segment_ids])),
    )


def merge_render_text_fragments(previous_text: str, current_text: str) -> str:
    previous_clean = (previous_text or "").rstrip()
    current_clean = (current_text or "").lstrip()
    if not previous_clean:
        return current_clean
    if not current_clean:
        return previous_clean
    if previous_clean.endswith("-"):
        return previous_clean + current_clean
    prev_char = previous_clean[-1]
    curr_char = current_clean[0]
    if re.match(r"[A-Za-z0-9]", prev_char) and re.match(r"[A-Za-z0-9]", curr_char):
        return f"{previous_clean} {current_clean}"
    return previous_clean + current_clean


def merge_adjacent_code_render_blocks(
    previous: MergedRenderBlock,
    current: MergedRenderBlock,
) -> MergedRenderBlock:
    merged_metadata = dict(previous.source_metadata)
    previous_regions = block_bbox_regions(previous)
    current_regions = block_bbox_regions(current)
    if previous_regions or current_regions:
        merged_metadata["source_bbox_json"] = {"regions": [*previous_regions, *current_regions]}
    merged_metadata["source_page_end"] = block_page_end(current)
    merged_metadata["pdf_block_role"] = "code_like"
    merged_source_text, deduped = merge_code_text_fragments(previous.source_text, current.source_text)
    recovery_flags = [
        *list(previous.source_metadata.get("recovery_flags") or []),
        *list(current.source_metadata.get("recovery_flags") or []),
        "export_code_blocks_merged",
    ]
    if deduped:
        recovery_flags.append("export_code_overlap_deduped")
    merged_metadata["recovery_flags"] = list(dict.fromkeys(recovery_flags))
    merged_target = previous.target_text
    if current.target_text:
        merged_target = (
            "\n".join([previous.target_text.rstrip("\n"), current.target_text.lstrip("\n")])
            if previous.target_text
            else current.target_text
        )
    return replace(
        previous,
        block_type=BlockType.CODE.value,
        render_mode="source_artifact_full_width",
        artifact_kind="code",
        title=None,
        source_text=merged_source_text,
        target_text=merged_target,
        source_metadata=merged_metadata,
        source_sentence_ids=list(dict.fromkeys([*previous.source_sentence_ids, *current.source_sentence_ids])),
        target_segment_ids=list(dict.fromkeys([*previous.target_segment_ids, *current.target_segment_ids])),
        notice="代码保持原样",
    )


def merge_code_text_fragments(previous_text: str, current_text: str) -> tuple[str, bool]:
    previous_clean = (previous_text or "").rstrip("\n")
    current_clean = (current_text or "").lstrip("\n")
    if not previous_clean:
        return current_clean, False
    if not current_clean:
        return previous_clean, False

    previous_lines = previous_clean.splitlines()
    current_lines = current_clean.splitlines()
    if code_line_sequence_is_prefix(previous_lines, current_lines):
        return current_clean, True
    if code_line_sequence_is_prefix(current_lines, previous_lines):
        return previous_clean, True

    overlap = code_line_overlap_size(previous_lines, current_lines)
    if overlap > 0:
        merged_lines = [*previous_lines, *current_lines[overlap:]]
        return "\n".join(merged_lines), True
    return "\n".join([previous_clean, current_clean]), False


def code_line_sequence_is_prefix(prefix_lines: list[str], candidate_lines: list[str]) -> bool:
    if not prefix_lines or len(prefix_lines) > len(candidate_lines):
        return False
    if len(prefix_lines) < 4:
        return False
    normalized_prefix = [line.strip() for line in prefix_lines]
    normalized_candidate = [line.strip() for line in candidate_lines[: len(prefix_lines)]]
    return normalized_prefix == normalized_candidate


def code_line_overlap_size(previous_lines: list[str], current_lines: list[str]) -> int:
    max_overlap = min(len(previous_lines), len(current_lines), 36)
    for overlap in range(max_overlap, 2, -1):
        previous_suffix = [line.strip() for line in previous_lines[-overlap:]]
        current_prefix = [line.strip() for line in current_lines[:overlap]]
        if previous_suffix == current_prefix:
            return overlap
    return 0


def should_merge_adjacent_code_blocks(previous: MergedRenderBlock, current: MergedRenderBlock) -> bool:
    if previous.artifact_kind != "code" or current.artifact_kind != "code":
        return False
    if previous.render_mode != "source_artifact_full_width" or current.render_mode != "source_artifact_full_width":
        return False
    previous_page_end = block_page_end(previous)
    current_page_start = block_page_number(current)
    if (
        previous_page_end is not None
        and current_page_start is not None
        and current_page_start - previous_page_end > 1
    ):
        return False
    return True


def should_bridge_code_blocks_across_inline_artifact(
    previous: MergedRenderBlock,
    middle: MergedRenderBlock,
    following: MergedRenderBlock,
) -> bool:
    if not should_merge_adjacent_code_blocks(previous, following):
        return False
    if middle.chapter_id != previous.chapter_id or middle.chapter_id != following.chapter_id:
        return False
    if middle.artifact_kind not in {"image", "figure"}:
        return False
    if middle.render_mode not in {"image_anchor_with_translated_caption", "source_artifact_full_width"}:
        return False
    if str(middle.source_metadata.get("linked_caption_block_id") or "").strip():
        return False
    if str(middle.source_metadata.get("linked_caption_text") or "").strip():
        return False
    if (middle.target_text or "").strip():
        return False
    previous_bbox = block_bbox_regions(previous)
    middle_bbox = block_bbox_regions(middle)
    following_bbox = block_bbox_regions(following)
    if not previous_bbox or not middle_bbox or not following_bbox:
        return False
    previous_last_bbox = previous_bbox[-1].get("bbox")
    middle_first_bbox = middle_bbox[0].get("bbox")
    following_first_bbox = following_bbox[0].get("bbox")
    if not (
        isinstance(previous_last_bbox, list)
        and isinstance(middle_first_bbox, list)
        and isinstance(following_first_bbox, list)
    ):
        return False
    try:
        previous_gap = float(middle_first_bbox[1]) - float(previous_last_bbox[3])
        following_gap = float(following_first_bbox[1]) - float(middle_first_bbox[3])
        code_width = max(
            float(previous_last_bbox[2]) - float(previous_last_bbox[0]),
            float(following_first_bbox[2]) - float(following_first_bbox[0]),
            1.0,
        )
        middle_width = float(middle_first_bbox[2]) - float(middle_first_bbox[0])
        middle_height = float(middle_first_bbox[3]) - float(middle_first_bbox[1])
        code_left_delta = abs(float(previous_last_bbox[0]) - float(following_first_bbox[0]))
    except (TypeError, ValueError, IndexError):
        return False
    if previous_gap < -12.0 or following_gap < -12.0:
        return False
    if previous_gap > 48.0 or following_gap > 48.0:
        return False
    if code_left_delta > 96.0:
        return False
    if middle_width > code_width * 0.58 and middle_height > 96.0:
        return False
    if (
        horizontal_overlap_ratio(middle_first_bbox, previous_last_bbox) < 0.22
        and horizontal_overlap_ratio(middle_first_bbox, following_first_bbox) < 0.22
    ):
        return False
    return True


def merge_code_blocks_across_inline_artifact(
    previous: MergedRenderBlock,
    middle: MergedRenderBlock,
    following: MergedRenderBlock,
) -> MergedRenderBlock:
    merged = merge_adjacent_code_render_blocks(previous, following)
    merged_metadata = dict(merged.source_metadata)
    merged_metadata["suppressed_artifact_block_ids"] = list(
        dict.fromkeys(
            [
                *list(merged_metadata.get("suppressed_artifact_block_ids") or []),
                middle.block_id,
            ]
        )
    )
    merged_metadata["recovery_flags"] = list(
        dict.fromkeys(
            [
                *list(merged_metadata.get("recovery_flags") or []),
                *list(middle.source_metadata.get("recovery_flags") or []),
                "export_inline_image_between_code_suppressed",
            ]
        )
    )
    return replace(merged, source_metadata=merged_metadata)


def should_merge_adjacent_book_paragraph_fragments(
    previous: MergedRenderBlock,
    current: MergedRenderBlock,
) -> bool:
    if previous.block_type != BlockType.PARAGRAPH.value or current.block_type != BlockType.PARAGRAPH.value:
        return False
    if previous.render_mode not in {"zh_primary_with_optional_source", "zh_primary_with_inline_protected_spans"}:
        return False
    if current.render_mode not in {"zh_primary_with_optional_source", "zh_primary_with_inline_protected_spans"}:
        return False
    if previous.chapter_id != current.chapter_id:
        return False
    if previous.artifact_kind is not None or current.artifact_kind is not None:
        return False
    previous_page = block_page_number(previous)
    current_page = block_page_number(current)
    if previous_page is not None and current_page is not None and current_page - previous_page > 1:
        return False
    previous_index = previous.source_metadata.get("reading_order_index")
    current_index = current.source_metadata.get("reading_order_index")
    if isinstance(previous_index, int) and isinstance(current_index, int) and current_index - previous_index > 1:
        return False
    previous_family = str(previous.source_metadata.get("pdf_page_family") or "body").strip().casefold()
    current_family = str(current.source_metadata.get("pdf_page_family") or "body").strip().casefold()
    if previous_family != current_family or previous_family not in {"body", "frontmatter"}:
        return False
    current_text = _normalize_render_text(current.source_text)
    if not current_text:
        return False
    previous_flags = set(str(flag) for flag in list(previous.source_metadata.get("recovery_flags") or []))
    current_flags = set(str(flag) for flag in list(current.source_metadata.get("recovery_flags") or []))
    if current_text[:1].islower():
        return True
    if "export_book_heading_demoted" in previous_flags or "export_book_heading_demoted" in current_flags:
        return not previous.source_text.rstrip().endswith(_TERMINAL_PUNCTUATION)
    return False


def merge_adjacent_book_paragraph_fragments(
    previous: MergedRenderBlock,
    current: MergedRenderBlock,
) -> MergedRenderBlock:
    merged_metadata = dict(previous.source_metadata)
    previous_regions = block_bbox_regions(previous)
    current_regions = block_bbox_regions(current)
    if previous_regions or current_regions:
        merged_metadata["source_bbox_json"] = {"regions": [*previous_regions, *current_regions]}
    merged_metadata["source_page_end"] = block_page_end(current)
    merged_metadata["pdf_block_role"] = "body"
    merged_metadata["recovery_flags"] = list(
        dict.fromkeys(
            [
                *list(previous.source_metadata.get("recovery_flags") or []),
                *list(current.source_metadata.get("recovery_flags") or []),
                "export_book_paragraph_fragments_merged",
            ]
        )
    )
    merged_target = merge_render_text_fragments(previous.target_text or "", current.target_text or "")
    return replace(
        previous,
        source_text=merge_render_text_fragments(previous.source_text, current.source_text),
        target_text=merged_target or previous.target_text,
        source_metadata=merged_metadata,
        source_sentence_ids=list(dict.fromkeys([*previous.source_sentence_ids, *current.source_sentence_ids])),
        target_segment_ids=list(dict.fromkeys([*previous.target_segment_ids, *current.target_segment_ids])),
    )


def should_merge_adjacent_prose_artifact_continuations(
    previous: MergedRenderBlock,
    current: MergedRenderBlock,
) -> bool:
    if previous.block_type != BlockType.PARAGRAPH.value:
        return False
    if previous.render_mode not in {"zh_primary_with_optional_source", "zh_primary_with_inline_protected_spans"}:
        return False
    if current.chapter_id != previous.chapter_id:
        return False
    if str(previous.source_metadata.get("pdf_page_family") or "body") != "body":
        return False
    if str(current.source_metadata.get("pdf_page_family") or "body") != "body":
        return False
    if str(current.source_metadata.get("pdf_block_role") or "").strip().casefold() not in {"code_like", "table_like"}:
        return False
    if current.target_text is None or not current.target_text.strip():
        return False
    if previous.source_text.rstrip().endswith(_TERMINAL_PUNCTUATION):
        return False
    if not code_text.looks_like_prose_continuation_artifact_text(current.source_text):
        return False
    previous_page = block_page_number(previous)
    current_page = block_page_number(current)
    if previous_page is not None and current_page is not None and current_page - previous_page > 1:
        return False
    previous_index = previous.source_metadata.get("reading_order_index")
    current_index = current.source_metadata.get("reading_order_index")
    if isinstance(previous_index, int) and isinstance(current_index, int) and current_index - previous_index > 1:
        return False
    return True


def merge_adjacent_prose_artifact_continuations(
    previous: MergedRenderBlock,
    current: MergedRenderBlock,
) -> MergedRenderBlock:
    merged_metadata = dict(previous.source_metadata)
    previous_regions = block_bbox_regions(previous)
    current_regions = block_bbox_regions(current)
    if previous_regions or current_regions:
        merged_metadata["source_bbox_json"] = {"regions": [*previous_regions, *current_regions]}
    merged_metadata["source_page_end"] = block_page_end(current)
    merged_metadata["pdf_block_role"] = "body"
    merged_metadata["recovery_flags"] = list(
        dict.fromkeys(
            [
                *list(previous.source_metadata.get("recovery_flags") or []),
                *list(current.source_metadata.get("recovery_flags") or []),
                "export_prose_artifact_continuation_merged",
            ]
        )
    )
    merged_target = merge_render_text_fragments(previous.target_text or "", current.target_text or "")
    return replace(
        previous,
        source_text=merge_render_text_fragments(previous.source_text, current.source_text),
        target_text=merged_target or previous.target_text,
        source_metadata=merged_metadata,
        source_sentence_ids=list(dict.fromkeys([*previous.source_sentence_ids, *current.source_sentence_ids])),
        target_segment_ids=list(dict.fromkeys([*previous.target_segment_ids, *current.target_segment_ids])),
    )


def normalize_reference_listing_render_blocks(
    blocks: list[MergedRenderBlock],
) -> list[MergedRenderBlock]:
    normalized: list[MergedRenderBlock] = []
    pending: list[MergedRenderBlock] = []
    in_reference_section = False

    def _flush_pending() -> None:
        nonlocal pending
        if pending:
            normalized.extend(expand_reference_listing_render_blocks(pending))
            pending = []

    for block in blocks:
        page_family = str(block.source_metadata.get("pdf_page_family") or "").strip().casefold()
        if page_family != "references":
            _flush_pending()
            in_reference_section = False
            normalized.append(block)
            continue
        heading_text = _normalize_render_text(block.target_text or block.source_text).casefold()
        if block.block_type == BlockType.HEADING.value and heading_text in _BOOK_ALLOWED_REFERENCE_HEADINGS:
            _flush_pending()
            in_reference_section = True
            normalized.append(block)
            continue
        if in_reference_section and is_reference_listing_render_block(block):
            pending.append(block)
            continue
        _flush_pending()
        normalized.append(block)

    _flush_pending()
    return normalized


def is_reference_listing_render_block(block: MergedRenderBlock) -> bool:
    source_text = str(block.source_text or "").strip()
    target_text = str(block.target_text or "").strip()
    if not source_text and not target_text:
        return False
    if block.artifact_kind == "reference":
        return True
    if _URL_ONLY_PATTERN.match(source_text):
        return True
    if _REFERENCE_ENTRY_MARKER_PATTERN.match(source_text):
        return True
    if code_text.looks_like_reference_listing_text(source_text):
        return True
    if _REFERENCE_LOCATOR_PATTERN.search(source_text) and (
        _REFERENCE_ENTRY_MARKER_PATTERN.search(source_text) or _URL_ONLY_PATTERN.match(source_text)
    ):
        return True
    return bool(target_text and _REFERENCE_ENTRY_MARKER_PATTERN.search(target_text))


def extract_numbered_reference_target_lines(text: str) -> list[str]:
    lines = code_text.split_reference_listing_lines(text)
    if not lines:
        return []
    entries: list[str] = []
    current_lines: list[str] = []
    for line in lines:
        if _REFERENCE_ENTRY_MARKER_PATTERN.match(line):
            if current_lines:
                entries.append(" ".join(current_lines).strip())
            current_lines = [line]
            continue
        if current_lines and not (_URL_ONLY_PATTERN.match(line) or _REFERENCE_LOCATOR_PATTERN.search(line)):
            current_lines.append(line)
            continue
        if current_lines:
            entries.append(" ".join(current_lines).strip())
        current_lines = []
    if current_lines:
        entries.append(" ".join(current_lines).strip())
    return entries


def expand_reference_listing_render_blocks(
    blocks: list[MergedRenderBlock],
) -> list[MergedRenderBlock]:
    line_pairs: list[tuple[str, str, MergedRenderBlock]] = []
    for block in blocks:
        source_lines = code_text.split_reference_listing_lines(block.source_text or "")
        target_queue = extract_numbered_reference_target_lines(block.target_text or "")
        for source_line in source_lines:
            if _REFERENCE_ENTRY_MARKER_PATTERN.match(source_line):
                target_line = target_queue.pop(0) if target_queue else source_line
            elif _URL_ONLY_PATTERN.match(source_line):
                target_line = source_line
            else:
                target_line = target_queue.pop(0) if target_queue else source_line
            line_pairs.append((source_line, target_line, block))

    grouped_entries: list[list[tuple[str, str, MergedRenderBlock]]] = []
    current_entry: list[tuple[str, str, MergedRenderBlock]] = []
    for pair in line_pairs:
        source_line = pair[0]
        if _REFERENCE_ENTRY_MARKER_PATTERN.match(source_line):
            if current_entry:
                grouped_entries.append(current_entry)
            current_entry = [pair]
            continue
        if current_entry:
            current_entry.append(pair)
        else:
            grouped_entries.append([pair])
    if current_entry:
        grouped_entries.append(current_entry)

    normalized_blocks: list[MergedRenderBlock] = []
    fragment_index = 0
    for entry in grouped_entries:
        title_pairs = [pair for pair in entry if not _URL_ONLY_PATTERN.match(pair[0])]
        locator_pairs = [pair for pair in entry if _URL_ONLY_PATTERN.match(pair[0])]
        if title_pairs:
            base_block = title_pairs[0][2]
            source_metadata = dict(base_block.source_metadata)
            recovery_flags = list(source_metadata.get("recovery_flags") or [])
            source_metadata["recovery_flags"] = list(
                dict.fromkeys([*recovery_flags, "export_reference_listing_normalized"])
            )
            normalized_blocks.append(
                replace(
                    base_block,
                    block_id=f"{base_block.block_id}::reference-entry::{fragment_index}",
                    block_type=BlockType.PARAGRAPH.value,
                    render_mode="zh_primary_with_optional_source",
                    artifact_kind=None,
                    title=None,
                    source_text="\n".join(pair[0] for pair in title_pairs),
                    target_text="\n".join(pair[1] for pair in title_pairs),
                    source_metadata=source_metadata,
                    notice=None,
                )
            )
            fragment_index += 1
        for source_line, _, base_block in locator_pairs:
            source_metadata = dict(base_block.source_metadata)
            recovery_flags = list(source_metadata.get("recovery_flags") or [])
            source_metadata["recovery_flags"] = list(
                dict.fromkeys([*recovery_flags, "export_reference_listing_normalized"])
            )
            normalized_blocks.append(
                replace(
                    base_block,
                    block_id=f"{base_block.block_id}::reference-locator::{fragment_index}",
                    block_type=BlockType.PARAGRAPH.value,
                    render_mode="zh_primary_with_optional_source",
                    artifact_kind=None,
                    title=None,
                    source_text=source_line,
                    target_text=source_line,
                    source_metadata=source_metadata,
                    notice=None,
                )
            )
            fragment_index += 1
    return normalized_blocks or blocks


def has_refresh_split_render_fragments(*blocks: MergedRenderBlock) -> bool:
    for block in blocks:
        if isinstance(block.source_metadata.get("refresh_split_render_fragments"), list):
            return True
    return False


def is_code_like_block(
    block,
    source_metadata: dict[str, object],
    *,
    block_type: BlockType | None = None,
    source_text: str | None = None,
    document=None,
) -> bool:
    effective_block_type = block_type or block.block_type
    effective_source_text = source_text if source_text is not None else block.source_text
    if is_translatable_prose_artifact_block(
        block,
        source_metadata,
        block_type=effective_block_type,
        source_text=effective_source_text,
        document=document,
    ):
        return False
    if effective_block_type == BlockType.CODE:
        return True
    if effective_block_type not in {BlockType.PARAGRAPH, BlockType.TABLE}:
        return False
    academic_paper = _is_academic_paper_document(document)
    page_family = str(source_metadata.get("pdf_page_family") or "").strip().casefold()
    if academic_paper and page_family == "references":
        return False
    if academic_paper and code_text.looks_like_academic_frontmatter_text(effective_source_text):
        return False
    if academic_paper and code_text.looks_like_academic_prose_text(effective_source_text):
        return False
    if str(source_metadata.get("pdf_block_role") or "").strip().casefold() == "code_like":
        return True
    return code_text.looks_like_code_artifact_text(effective_source_text, academic_paper=academic_paper)


def effective_export_block_type(block, source_metadata: dict[str, object]) -> BlockType:
    raw = str(source_metadata.get("repair_block_type") or "").strip().casefold()
    if raw:
        try:
            return BlockType(raw)
        except ValueError:
            pass
    return block.block_type


def is_translatable_prose_artifact_block(
    block,
    source_metadata: dict[str, object],
    *,
    block_type: BlockType | None = None,
    source_text: str | None = None,
    document=None,
) -> bool:
    if bool(source_metadata.get("repair_target_text")):
        return True
    effective_block_type = block_type or block.block_type
    if effective_block_type not in {BlockType.CODE, BlockType.TABLE, BlockType.PARAGRAPH}:
        return False
    if effective_block_type == BlockType.CODE:
        return False
    effective_source_text = source_text if source_text is not None else block.source_text
    if code_text.looks_like_mixed_code_prose_artifact_text(effective_source_text):
        return False
    if not code_text.looks_like_prose_artifact_text(
        effective_source_text,
        academic_paper=_is_academic_paper_document(document),
    ):
        return False
    role = str(source_metadata.get("pdf_block_role") or "").strip().casefold()
    if effective_block_type == BlockType.PARAGRAPH:
        return role in {"code_like", "table_like"}
    return True


def render_mode_for_block(
    block,
    block_sentences: list[object],
    source_metadata: dict[str, object],
    *,
    block_type: BlockType | None = None,
    source_text: str | None = None,
    document=None,
) -> str:
    effective_block_type = block_type or block.block_type
    effective_source_text = source_text if source_text is not None else block.source_text
    if is_translatable_prose_artifact_block(
        block,
        source_metadata,
        block_type=effective_block_type,
        source_text=effective_source_text,
        document=document,
    ):
        return "zh_primary_with_optional_source"
    if source_metadata.get("image_src"):
        return "image_anchor_with_translated_caption"
    if effective_block_type == BlockType.CAPTION and code_text.looks_like_figure_caption(effective_source_text):
        return "image_anchor_with_translated_caption"
    if code_text.looks_like_reference_literal(effective_source_text):
        return "reference_preserve_with_translated_label"
    if is_code_like_block(
        block,
        source_metadata,
        block_type=effective_block_type,
        source_text=effective_source_text,
        document=document,
    ):
        return "source_artifact_full_width"
    if effective_block_type == BlockType.CODE:
        return "source_artifact_full_width"
    if effective_block_type == BlockType.TABLE:
        return "translated_wrapper_with_preserved_artifact"
    if block.protected_policy.value == "protect":
        return "source_artifact_full_width"
    if block.protected_policy.value == "mixed":
        return "zh_primary_with_inline_protected_spans"
    if any(not sentence.translatable or sentence.sentence_status == SentenceStatus.PROTECTED for sentence in block_sentences):
        return "zh_primary_with_inline_protected_spans"
    return "zh_primary_with_optional_source"


def artifact_kind_for_block(
    block,
    render_mode: str,
    *,
    block_type: BlockType | None = None,
    source_text: str | None = None,
    source_metadata: dict[str, object] | None = None,
    document=None,
) -> str | None:
    effective_block_type = block_type or block.block_type
    effective_source_text = source_text if source_text is not None else block.source_text
    effective_source_metadata = source_metadata or (block.source_span_json or {})
    if is_translatable_prose_artifact_block(
        block,
        effective_source_metadata,
        block_type=effective_block_type,
        source_text=effective_source_text,
        document=document,
    ):
        return None
    tag = effective_source_metadata.get("tag")
    if render_mode == "image_anchor_with_translated_caption":
        return "image"
    if render_mode == "reference_preserve_with_translated_label":
        return "reference"
    if effective_block_type == BlockType.IMAGE:
        return "image"
    if effective_block_type == BlockType.FIGURE:
        return "figure"
    if effective_block_type == BlockType.EQUATION:
        return "equation"
    if effective_block_type == BlockType.CODE:
        if tag in {"math", "svg"}:
            return "equation"
        return "code"
    if is_code_like_block(
        block,
        effective_source_metadata,
        block_type=effective_block_type,
        source_text=effective_source_text,
        document=document,
    ):
        return "code"
    if effective_block_type == BlockType.TABLE:
        return "table"
    if render_mode == "source_artifact_full_width":
        return "protected_artifact"
    return None


def group_block_sentences_by_target_id(
    block_sentences: list[object],
    *,
    sentence_targets: dict[str, list[str]],
    target_map: dict[str, object],
) -> list[tuple[str | None, list[object]]]:
    groups: list[tuple[str | None, list[object]]] = []
    current_target_id: str | None = None
    current_sentences: list[object] = []

    for sentence in block_sentences:
        candidate_ids = [target_id for target_id in sentence_targets.get(sentence.id, []) if target_id in target_map]
        primary_target_id = candidate_ids[0] if candidate_ids else None
        if current_sentences and primary_target_id != current_target_id:
            groups.append((current_target_id, current_sentences))
            current_sentences = [sentence]
            current_target_id = primary_target_id
            continue
        if not current_sentences:
            current_target_id = primary_target_id
        current_sentences.append(sentence)

    if current_sentences:
        groups.append((current_target_id, current_sentences))
    return groups


def join_sentence_source_texts(sentences: list[object]) -> str:
    parts = [str(getattr(sentence, "source_text", "") or "").strip() for sentence in sentences]
    cleaned = [part for part in parts if part]
    if not cleaned:
        return ""
    return inline_join_target_text(cleaned)


def looks_like_prose_title_text(
    title_text: str | None,
    *,
    source_heading_text: str | None = None,
    fallback_title: str | None = None,
) -> bool:
    normalized = _normalize_render_text(title_text)
    if not normalized:
        return False
    sentence_stop_count = sum(normalized.count(marker) for marker in (".", "!", "?", "。", "！", "？"))
    clause_break_count = sum(normalized.count(marker) for marker in (",", "，", ";", "；", ":", "："))
    english_word_count = len(re.findall(r"[A-Za-z][A-Za-z'-]*", normalized))
    cjk_char_count = len(re.findall(r"[\u4e00-\u9fff]", normalized))
    reference_length = max(
        len(_normalize_render_text(source_heading_text or "")),
        len(_normalize_render_text(fallback_title or "")),
    )
    if sentence_stop_count >= 2:
        return True
    if cjk_char_count >= 52 and clause_break_count >= 2:
        return True
    if english_word_count >= 18 and clause_break_count >= 2:
        return True
    if reference_length and len(normalized) >= max(48, reference_length * 4) and clause_break_count >= 2:
        return True
    return False


def should_restore_pdf_paragraph_breaks(
    grouped_sentences: list[tuple[str | None, list[object]]],
) -> bool:
    if len(grouped_sentences) < 2:
        return False
    multi_sentence_group_count = sum(1 for _target_id, sentences in grouped_sentences if len(sentences) > 1)
    if multi_sentence_group_count >= 2:
        return True
    if multi_sentence_group_count >= 1 and any(len(sentences) == 1 for _target_id, sentences in grouped_sentences):
        return True
    return False


def inline_join_target_text(target_texts: list[str]) -> str:
    merged = target_texts[0]
    for segment in target_texts[1:]:
        if needs_ascii_sentence_gap(merged, segment):
            merged = f"{merged} {segment}"
        else:
            merged = f"{merged}{segment}"
    return merged


def needs_ascii_sentence_gap(previous: str, current: str) -> bool:
    previous = previous.rstrip()
    current = current.lstrip()
    if not previous or not current:
        return False
    prev_char = previous[-1]
    curr_char = current[0]
    if re.match(r"[A-Za-z0-9]", prev_char) and re.match(r"[A-Za-z0-9]", curr_char):
        return True
    if prev_char in {".", "!", "?", ";", ":", ",", "\"", "'", ")", "]", "}"} and re.match(
        r"[A-Za-z0-9\"'(\[]",
        curr_char,
    ):
        return True
    return False
