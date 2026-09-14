"""Render merged blocks to HTML, Markdown and rebuilt-EPUB XHTML.

Covers target-text joining, tables, equations, code and image references.
"""


from __future__ import annotations

import html
import re
import urllib.parse
from pathlib import PurePosixPath

from book_agent.domain.enums import (
    BlockType,
)
from book_agent.export import code_text, render_repair
from book_agent.export.common import (
    _LIST_MARKER_PATTERN,
    _ORDERED_LIST_LINE_PATTERN,
    _ORDERED_LIST_MARKER_PATTERN,
    _REFERENCE_ENTRY_MARKER_PATTERN,
    _REFERENCE_LOCATOR_PATTERN,
    _UNORDERED_LIST_LINE_PATTERN,
    _URL_ONLY_PATTERN,
    _leading_whitespace_width,
)
from book_agent.export.models import (
    MergedRenderBlock,
)
from book_agent.infra.repositories.export import ChapterExportBundle


def render_block_markdown(
    block: MergedRenderBlock,
    asset_path_by_block_id: dict[str, str] | None = None,
    *,
    include_notice: bool = True,
    include_source_caption: bool = True,
) -> str:
    source_text = block.source_text or ""
    target_text = block.target_text or ""
    notice = str(block.notice or "").strip() if include_notice else ""
    asset_src = str((asset_path_by_block_id or {}).get(block.block_id) or "").strip()
    image_alt_text = str(block.source_metadata.get("image_alt") or block.source_text or "Embedded image").strip()

    if block.render_mode == "source_artifact_full_width":
        if asset_src and block.artifact_kind in {"image", "figure"}:
            parts = [markdown_blockquote(notice)] if notice else []
            parts.append(markdown_image_reference(image_alt_text, asset_src))
            if (
                include_source_caption
                and source_text
                and source_text not in {"[Image]", ""}
            ):
                normalized_source_caption = re.sub(r"\s+", " ", source_text).strip()
                parts.append(f"*{normalized_source_caption}*")
            return "\n\n".join(part for part in parts if part)
        if block.artifact_kind == "equation":
            parts = [markdown_blockquote(notice)] if notice else []
            parts.append(
                render_equation_markdown_metadata_aware(
                    block.source_metadata, source_text
                )
            )
            return "\n\n".join(part for part in parts if part)
        artifact_text = source_text
        if block.artifact_kind == "code":
            artifact_text = code_text.normalize_markdown_code_artifact_text(source_text, block=block)
        parts = [markdown_blockquote(notice)] if notice else []
        parts.append(
            markdown_fenced_block(
                artifact_text,
                language=markdown_language_for_artifact(block.artifact_kind, artifact_text),
            )
        )
        return "\n\n".join(part for part in parts if part)

    if block.render_mode == "translated_wrapper_with_preserved_artifact":
        parts = [target_text] if target_text else []
        if notice:
            parts.append(markdown_blockquote(notice))
        if block.artifact_kind == "equation":
            parts.append(
                render_equation_markdown_metadata_aware(
                    block.source_metadata, source_text
                )
            )
        else:
            markdown_table = (
                render_table_markdown_metadata_aware(
                    block.source_metadata, source_text
                )
                if block.artifact_kind == "table"
                else None
            )
            parts.append(
                markdown_table
                or markdown_fenced_block(
                    source_text,
                    language=markdown_language_for_artifact(block.artifact_kind, source_text),
                )
            )
        return "\n\n".join(part for part in parts if part)

    if block.render_mode == "image_anchor_with_translated_caption":
        parts: list[str] = []
        if asset_src:
            parts.append(markdown_image_reference(image_alt_text, asset_src))
        elif include_source_caption and source_text:
            parts.append(markdown_blockquote(source_text, label="Image caption"))
        if target_text:
            parts.append(target_text)
        if notice:
            parts.append(markdown_blockquote(notice))
        if (
            include_source_caption
            and source_text
            and source_text != target_text
        ):
            normalized_source_caption = re.sub(r"\s+", " ", source_text).strip()
            parts.append(f"*Source caption: {normalized_source_caption}*")
        return "\n\n".join(part for part in parts if part)

    if block.render_mode == "reference_preserve_with_translated_label":
        parts = [target_text] if target_text and target_text != source_text else []
        if notice:
            parts.append(markdown_blockquote(notice))
        parts.append(source_text)
        return "\n\n".join(part for part in parts if part)

    if block.block_type == BlockType.HEADING.value:
        heading_text = target_text or source_text
        # Long blocks misclassified as headings (e.g. chapter description
        # paragraphs starting with "Chapter N, ...") should render as body.
        if len(heading_text) <= 150:
            return f"### {heading_text}".strip()
    if block.block_type == BlockType.TABLE.value:
        table_source = target_text or source_text
        markdown_table = markdown_table_from_source_text(table_source)
        if markdown_table:
            return markdown_table
    list_markdown = markdown_list_text(target_text or source_text)
    if block.block_type == BlockType.QUOTE.value:
        parts = [list_markdown or markdown_blockquote(target_text or source_text)]
    elif block.block_type == BlockType.LIST_ITEM.value:
        list_text = target_text or source_text
        marker = "" if re.match(r"^\s*(?:[-*+]\s+|\d+\.\s+)", list_text) else "- "
        parts = [f"{marker}{list_text}".rstrip()]
    elif block.block_type == BlockType.CAPTION.value:
        parts = [f"*{target_text or source_text}*"]
    else:
        parts = [list_markdown or (target_text or source_text)]

    # Merged reading edition is Chinese-only; no "<details><summary>原文</summary>"
    # toggle with the English source. Bilingual comparison is a
    # separate export type.
    return "\n\n".join(part for part in parts if part)


def markdown_blockquote(text: str, *, label: str | None = None) -> str:
    normalized = (text or "").strip()
    if not normalized and not label:
        return ""
    lines: list[str] = []
    if label:
        lines.append(f"> {label}")
    for line in normalized.splitlines() if normalized else []:
        lines.append(f"> {line}" if line else ">")
    return "\n".join(lines) if lines else f"> {label}"


def markdown_details_source(text: str) -> str:
    normalized = (text or "").strip()
    if not normalized:
        return ""
    return f"<details>\n<summary>原文</summary>\n\n{normalized}\n\n</details>"


def markdown_list_text(text: str) -> str | None:
    raw_lines = [line.rstrip() for line in str(text or "").splitlines() if line.strip()]
    if len(raw_lines) < 2:
        raw_lines = render_repair.split_inline_list_target_lines(text, preserve_leading_ws=True)
    layouts = render_repair.list_line_layouts(text)
    if len(layouts) < 2 or len(raw_lines) != len(layouts):
        return None
    markdown_lines: list[str] = []
    for (level, _source_line), raw_line in zip(layouts, raw_lines):
        match = _UNORDERED_LIST_LINE_PATTERN.match(raw_line) or _ORDERED_LIST_LINE_PATTERN.match(raw_line)
        if match is None:
            return None
        body = str(match.group("body") or "").strip()
        if not body:
            return None
        prefix = "1. " if _ORDERED_LIST_LINE_PATTERN.match(raw_line) else "- "
        markdown_lines.append(f"{'   ' * max(level, 0)}{prefix}{body}")
    return "\n".join(markdown_lines)


def markdown_fenced_block(text: str, *, language: str = "") -> str:
    content = (text or "").rstrip("\n")
    fence = "```"
    while fence in content:
        fence += "`"
    opener = f"{fence}{language}" if language else fence
    return f"{opener}\n{content}\n{fence}"


def markdown_table_from_source_text(text: str) -> str | None:
    parsed_rows = parse_structured_table_rows(text)
    if parsed_rows is None:
        return None
    header, body_rows = parsed_rows
    escaped_header = [cell.replace("|", "\\|") for cell in header]
    lines = [
        f"| {' | '.join(escaped_header)} |",
        f"| {' | '.join(['---'] * len(escaped_header))} |",
    ]
    for row in body_rows:
        escaped_row = [cell.replace("|", "\\|") for cell in row]
        lines.append(f"| {' | '.join(escaped_row)} |")
    return "\n".join(lines)


def parse_structured_table_rows(text: str) -> tuple[list[str], list[list[str]]] | None:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if len(lines) < 2:
        return None

    rows = [split_table_candidate_line(line) for line in lines]
    if any(row is None for row in rows):
        return None
    normalized_rows = [row for row in rows if row]
    if len(normalized_rows) < 2:
        return None

    separator_index = 1 if len(normalized_rows) >= 3 and is_table_separator_row(normalized_rows[1]) else None
    if separator_index is not None:
        normalized_rows.pop(separator_index)
    if len(normalized_rows) < 2:
        return None

    column_count = max(len(row) for row in normalized_rows)
    if column_count < 2 or column_count > 12:
        return None
    padded_rows: list[list[str]] = [
        row + [""] * (column_count - len(row)) if len(row) < column_count else row
        for row in normalized_rows
    ]

    header = padded_rows[0]
    body_rows = padded_rows[1:]
    if not body_rows:
        return None
    return header, body_rows


def split_table_candidate_line(line: str) -> list[str] | None:
    stripped = line.strip().strip("|").strip()
    if not stripped:
        return None
    if "|" in stripped:
        cells = [cell.strip() for cell in stripped.split("|")]
        cells = [cell for cell in cells if cell]
        if len(cells) >= 2:
            return cells
    cells = [cell.strip() for cell in re.split(r"\t+|\s{2,}", stripped) if cell.strip()]
    if len(cells) >= 2:
        return cells
    return None


def is_table_separator_row(row: list[str]) -> bool:
    if not row:
        return False
    return all(bool(re.fullmatch(r":?-{2,}:?|={2,}", cell.strip())) for cell in row)


def markdown_language_for_artifact(artifact_kind: str | None, text: str) -> str:
    if artifact_kind == "equation":
        return "tex"
    if artifact_kind == "table":
        return "text"
    if artifact_kind == "code":
        stripped = (text or "").lstrip()
        if stripped.startswith("{") or stripped.startswith("["):
            return "json"
        if any(token in stripped for token in ["def ", "class ", "import ", "from ", "async def "]):
            return "python"
    return ""


def source_only_notice(block, artifact_kind: str | None, render_mode: str) -> str | None:
    if render_mode == "source_artifact_full_width":
        if (block.source_span_json or {}).get("pdf_page_family") == "backmatter":
            return "尾部资料页保留原样"
        if artifact_kind == "figure":
            return "插图原样保留"
        if artifact_kind == "image":
            return "图片原样保留"
        if artifact_kind == "code":
            return "代码保持原样"
        if artifact_kind == "equation":
            return "公式保持原样"
        if artifact_kind == "table":
            return "表格原结构保留"
        return "原文工件保留"
    if render_mode == "translated_wrapper_with_preserved_artifact":
        return "保留原始结构，优先保证可复制与结构保真"
    if render_mode == "image_anchor_with_translated_caption":
        return "图片锚点保留"
    if render_mode == "reference_preserve_with_translated_label":
        return "参考标识保留"
    return None


def join_block_target_text(
    target_texts: list[str],
    *,
    block_type: BlockType | None = None,
    render_mode: str | None = None,
    source_text: str | None = None,
) -> str:
    cleaned = [text.strip() for text in target_texts if text and text.strip()]
    if not cleaned:
        return ""
    structured_join = structured_target_text_join(
        cleaned,
        block_type=block_type,
        render_mode=render_mode,
        source_text=source_text,
    )
    if structured_join is not None:
        return structured_join
    if should_inline_join_target_text(block_type, render_mode):
        return render_repair.inline_join_target_text(cleaned)
    return "\n".join(cleaned)


def structured_target_text_join(
    target_texts: list[str],
    *,
    block_type: BlockType | None = None,
    render_mode: str | None = None,
    source_text: str | None = None,
) -> str | None:
    if len(target_texts) < 2:
        return None
    if render_mode in {
        "source_artifact_full_width",
        "translated_wrapper_with_preserved_artifact",
        "reference_preserve_with_translated_label",
    }:
        return None
    if should_preserve_list_segment_breaks(target_texts, source_text):
        return "\n".join(target_texts)
    return join_reference_target_segments(target_texts, block_type=block_type, source_text=source_text)


def should_preserve_list_segment_breaks(
    target_texts: list[str],
    source_text: str | None,
) -> bool:
    target_marker_count = sum(
        1
        for text in target_texts
        if _LIST_MARKER_PATTERN.match(text) or _ORDERED_LIST_MARKER_PATTERN.match(text)
    )
    if target_marker_count >= 2 and not any(_URL_ONLY_PATTERN.match(text) for text in target_texts):
        return True
    source_lines = [line.strip() for line in str(source_text or "").splitlines() if line.strip()]
    if len(source_lines) < 2:
        return False
    marker_lines = sum(
        1
        for line in source_lines
        if _LIST_MARKER_PATTERN.match(line) or _ORDERED_LIST_MARKER_PATTERN.match(line)
    )
    return marker_lines >= 2 and not any(_URL_ONLY_PATTERN.match(line) for line in source_lines)


def join_reference_target_segments(
    target_texts: list[str],
    *,
    block_type: BlockType | None = None,
    source_text: str | None = None,
) -> str | None:
    del block_type
    numbered_entries = sum(1 for text in target_texts if _REFERENCE_ENTRY_MARKER_PATTERN.match(text))
    locator_entries = sum(
        1 for text in target_texts if _URL_ONLY_PATTERN.match(text) or _REFERENCE_LOCATOR_PATTERN.search(text)
    )
    source_reference_like = code_text.looks_like_reference_listing_text(source_text or "")
    if numbered_entries < 2 and not source_reference_like:
        return None
    if locator_entries < 1 and not source_reference_like:
        return None
    blocks: list[str] = []
    current_lines: list[str] = []
    for text in target_texts:
        stripped = text.strip()
        if not stripped:
            continue
        if _REFERENCE_ENTRY_MARKER_PATTERN.match(stripped):
            if current_lines:
                blocks.append("\n".join(current_lines))
            current_lines = [stripped]
            continue
        if current_lines and (
            _URL_ONLY_PATTERN.match(stripped)
            or current_lines[-1].endswith((":", "："))
        ):
            current_lines.append(stripped)
            continue
        if current_lines:
            blocks.append("\n".join(current_lines))
        current_lines = [stripped]
    if current_lines:
        blocks.append("\n".join(current_lines))
    if len(blocks) < 2:
        return None
    return "\n\n".join(blocks)


def should_inline_join_target_text(
    block_type: BlockType | None,
    render_mode: str | None,
) -> bool:
    if render_mode in {
        "source_artifact_full_width",
        "translated_wrapper_with_preserved_artifact",
        "reference_preserve_with_translated_label",
    }:
        return False
    return block_type in {
        BlockType.HEADING,
        BlockType.PARAGRAPH,
        BlockType.LIST_ITEM,
        BlockType.QUOTE,
        BlockType.CAPTION,
        BlockType.FOOTNOTE,
    }


def render_block_html(
    block: MergedRenderBlock,
    asset_path_by_block_id: dict[str, str] | None = None,
    *,
    include_source_toggle: bool = True,
    include_notice: bool = True,
    include_source_caption: bool = True,
) -> str:
    notice_text = str(block.notice or "") if include_notice else ""
    source_html = format_inline_text(block.source_text)
    target_html = format_inline_text(block.target_text or "")
    if block.render_mode == "source_artifact_full_width":
        asset_src = ""
        if asset_path_by_block_id is not None:
            asset_src = str(asset_path_by_block_id.get(block.block_id) or "")
        if asset_src and block.artifact_kind in {"image", "figure"}:
            note = f"<div class='artifact-note'>{html.escape(notice_text)}</div>" if notice_text else ""
            image_alt_text = str(block.source_metadata.get("image_alt") or "PDF image")
            source_caption = (
                ""
                if not include_source_caption or block.source_text in {"", "[Image]"}
                else source_html
            )
            body = (
                "<figure class='artifact-figure'>"
                f"<img class='artifact-image' src='{html.escape(url_encode_asset_path(asset_src))}' alt='{html.escape(image_alt_text)}'/>"
                f"{f'<figcaption>{source_caption}</figcaption>' if source_caption else ''}"
                "</figure>"
            )
            return (
                f"<section class='block artifact {html.escape(block.block_type)}'>"
                f"{note}<div class='artifact-body'>{body}</div></section>"
            )
        if block.artifact_kind == "equation":
            body = render_equation_html_metadata_aware(
                block.source_metadata, block.source_text
            )
        elif block.artifact_kind == "code":
            body = f"<pre><code>{format_preformatted_text(block.source_text, block=block)}</code></pre>"
        else:
            body = f"<div class='artifact-body'>{source_html}</div>"
        note = f"<div class='artifact-note'>{html.escape(notice_text)}</div>" if notice_text else ""
        return f"<section class='block artifact {html.escape(block.block_type)}'>{note}{body}</section>"
    if block.render_mode == "translated_wrapper_with_preserved_artifact":
        note = f"<div class='artifact-note'>{html.escape(notice_text)}</div>" if notice_text else ""
        translated = f"<div class='zh'>{target_html}</div>" if block.target_text else ""
        if block.artifact_kind == "equation":
            body = render_equation_html_metadata_aware(
                block.source_metadata, block.source_text
            )
        else:
            table_html = render_table_html_metadata_aware(
                block.source_metadata, block.source_text
            ) if block.artifact_kind == "table" else None
            body = (
                f"<div class='artifact-body artifact-table-body'>{table_html}</div>"
                if table_html is not None
                else f"<div class='artifact-body'>{source_html}</div>"
            )
        return (
            f"<section class='block artifact {html.escape(block.block_type)}'>"
            f"{translated}{note}{body}</section>"
        )
    if block.render_mode == "image_anchor_with_translated_caption":
        note = f"<div class='artifact-note'>{html.escape(notice_text)}</div>" if notice_text else ""
        translated = f"<div class='zh'>{target_html}</div>" if block.target_text else ""
        asset_src = ""
        if asset_path_by_block_id is not None:
            asset_src = str(asset_path_by_block_id.get(block.block_id) or "")
        image_alt_text = str(block.source_metadata.get("image_alt") or block.source_text or "Embedded image")
        source_caption = source_html if (include_source_caption and block.source_text) else ""
        footer_source_caption = source_caption
        if asset_src:
            figure_html = (
                "<figure class='artifact-figure'>"
                f"<img class='artifact-image' src='{html.escape(url_encode_asset_path(asset_src))}' alt='{html.escape(image_alt_text)}'/>"
                "</figure>"
            )
            body = f"<div class='artifact-body'>{figure_html}</div>"
        else:
            image_src = html.escape(str(block.source_metadata.get("image_src", "")))
            metadata_lines = []
            if image_src:
                metadata_lines.append(f"<div><strong>Source:</strong> {image_src}</div>")
            if image_alt_text and image_alt_text != block.source_text:
                metadata_lines.append(f"<div><strong>Alt:</strong> {html.escape(image_alt_text)}</div>")
            body = f"<div class='artifact-body'>{''.join(metadata_lines) or source_html}</div>"
            if not metadata_lines:
                footer_source_caption = ""
        if not include_source_caption:
            footer_source_caption = ""
        caption_html = "".join(
            part
            for part in (
                translated,
                note,
                f"<div class='artifact-source-caption'>{footer_source_caption}</div>" if footer_source_caption else "",
            )
            if part
        )
        return (
            f"<section class='block artifact image-anchor {html.escape(block.block_type)}'>"
            f"{body}{caption_html}</section>"
        )
    if block.render_mode == "reference_preserve_with_translated_label":
        note = f"<div class='artifact-note'>{html.escape(notice_text)}</div>" if notice_text else ""
        translated = (
            f"<div class='zh'>{target_html}</div>"
            if block.target_text and block.target_text != block.source_text
            else ""
        )
        body = f"<div class='artifact-body'>{source_html}</div>"
        return (
            f"<section class='block artifact reference {html.escape(block.block_type)}'>"
            f"{translated}{note}{body}</section>"
        )
    if block.block_type == BlockType.TABLE.value:
        table_source = block.target_text or block.source_text or ""
        table_html = render_structured_table_html(table_source)
        if table_html:
            return (
                f"<section class='block artifact {html.escape(block.block_type)}'>"
                f"<div class='artifact-body artifact-table-body'>{table_html}</div>"
                "</section>"
            )
    source_details = (
        f"<details><summary>Source</summary><div class='source'>{source_html}</div></details>"
        if include_source_toggle
        and block.source_text
        and block.target_text
        and block.source_text != block.target_text
        else ""
    )
    block_class = "quote" if block.block_type == BlockType.QUOTE.value else block.block_type
    return (
        f"<section class='block {html.escape(block_class)}'>"
        f"<div class='zh'>{target_html or source_html}</div>"
        f"{source_details}"
        "</section>"
    )


def format_inline_text(text: str) -> str:
    formatted_lines: list[str] = []
    for raw_line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        expanded_line = raw_line.replace("\t", "    ")
        leading_spaces = _leading_whitespace_width(expanded_line)
        escaped_line = html.escape(expanded_line[leading_spaces:])
        escaped_line = re.sub(
            r"(&lt;/?[A-Za-z][^&]*?&gt;)",
            r"<code class='inline-token'>\1</code>",
            escaped_line,
        )
        if leading_spaces:
            escaped_line = ("&nbsp;" * leading_spaces) + escaped_line
        formatted_lines.append(escaped_line)
    return "<br/>".join(formatted_lines)


def format_preformatted_text(
    text: str,
    *,
    block: MergedRenderBlock | None = None,
) -> str:
    return html.escape(code_text.normalize_code_artifact_text(text, block=block))


def render_structured_table_html(text: str) -> str | None:
    parsed_rows = parse_structured_table_rows(text)
    if parsed_rows is None:
        return None
    header, body_rows = parsed_rows
    # Detect numeric columns for right-alignment
    alignments: list[str] = []
    for col_idx in range(len(header)):
        col_values = [row[col_idx] for row in body_rows if col_idx < len(row) and row[col_idx].strip()]
        numeric_count = sum(1 for v in col_values if re.fullmatch(r"[\d,.\-+%$€¥£]+", v.strip()))
        alignments.append("right" if col_values and numeric_count > len(col_values) * 0.6 else "left")

    thead_cells = []
    for idx, cell in enumerate(header):
        align = f" style='text-align:{alignments[idx]}'" if idx < len(alignments) else ""
        thead_cells.append(f"<th{align}>{html.escape(cell)}</th>")
    thead = "".join(thead_cells)

    body_lines = []
    for row in body_rows:
        cells = []
        for idx, cell in enumerate(row):
            align = f" style='text-align:{alignments[idx]}'" if idx < len(alignments) else ""
            cells.append(f"<td{align}>{html.escape(cell)}</td>")
        body_lines.append("<tr>" + "".join(cells) + "</tr>")
    body = "".join(body_lines)

    return (
        f"<div class='artifact-table-shell' data-source-text='{html.escape(text)}'>"
        "<table class='artifact-table'>"
        f"<thead><tr>{thead}</tr></thead>"
        f"<tbody>{body}</tbody>"
        "</table>"
        "</div>"
    )


def render_equation_html_metadata_aware(
    source_metadata: dict[str, object] | None,
    source_text: str,
) -> str:
    """PDF v2 M3.3 export wire-up — HTML side."""
    md = source_metadata or {}
    if md.get("equation_render_mode") == "latex":
        latex = str(md.get("equation_latex") or "").strip()
        if latex:
            content = html.escape(latex)
            return (
                f"<div class='math-block katex-display' data-render-mode='latex-recovered'>"
                f"<span class='katex-source'>{content}</span>"
                f"</div>"
            )
    return render_math_html(source_text)


def render_table_html_metadata_aware(
    source_metadata: dict[str, object] | None,
    source_text: str,
) -> str | None:
    """PDF v2 M3.2 export wire-up — HTML side.

    When `table_markdown` is present (heuristic or TATR recovery),
    convert it to an HTML table directly. Otherwise fall back to the
    source-text-driven structured table heuristic.
    """
    md = source_metadata or {}
    markdown = str(md.get("table_markdown") or "").strip()
    if markdown:
        html_table = markdown_table_to_html(markdown)
        if html_table:
            return html_table
    return render_structured_table_html(source_text)


def markdown_table_to_html(markdown: str) -> str | None:
    """Convert a `| a | b |\n| --- | --- |\n| 1 | 2 |` block to HTML.

    Returns None if the input doesn't look like a markdown table.
    Used by `_render_table_html_metadata_aware` to bridge our M3
    markdown-table representation into the existing HTML export.
    """
    lines = [line.strip() for line in markdown.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    if not all(line.startswith("|") and line.endswith("|") for line in lines):
        return None
    # Separator detection: row 1 (0-indexed) with --- cells.
    sep_row = lines[1]
    if "---" not in sep_row:
        return None

    def _split_row(row: str) -> list[str]:
        inner = row.strip().strip("|")
        return [cell.strip() for cell in inner.split("|")]

    header_cells = _split_row(lines[0])
    body_rows = [_split_row(line) for line in lines[2:]]

    thead = "".join(f"<th>{html.escape(cell)}</th>" for cell in header_cells)
    body_html = ""
    for row in body_rows:
        body_html += (
            "<tr>"
            + "".join(f"<td>{html.escape(cell)}</td>" for cell in row)
            + "</tr>"
        )
    return (
        "<div class='artifact-table-shell' data-source='m3-markdown-table'>"
        "<table class='artifact-table'>"
        f"<thead><tr>{thead}</tr></thead>"
        f"<tbody>{body_html}</tbody>"
        "</table>"
        "</div>"
    )


def render_math_html(text: str) -> str:
    """Wrap equation text in KaTeX-compatible HTML containers."""
    stripped = text.strip()
    if not stripped:
        return ""
    # Detect if it's already LaTeX-formatted
    is_latex = any(marker in stripped for marker in ("\\frac", "\\sum", "\\int", "\\sqrt", "\\begin", "\\end", "\\alpha", "\\beta", "\\gamma", "\\theta", "\\sigma", "\\pi", "\\mu", "\\lambda", "\\delta", "\\epsilon", "\\omega", "\\partial", "\\nabla", "\\infty", "\\text", "\\mathbb", "\\mathrm", "\\left", "\\right", "\\cdot", "\\times", "\\leq", "\\geq", "\\neq", "\\approx", "\\in", "\\notin", "\\subset", "\\cup", "\\cap", "^{", "_{"))
    if is_latex:
        # Wrap in display math delimiters for KaTeX
        content = html.escape(stripped)
        return (
            f"<div class='math-block katex-display'>"
            f"<span class='katex-source'>{content}</span>"
            f"</div>"
        )
    # Not LaTeX — render as preformatted equation
    content = html.escape(stripped)
    return f"<div class='math-block equation-text'><pre>{content}</pre></div>"


def render_math_markdown(text: str) -> str:
    """Format equation text for markdown output with $$ delimiters."""
    stripped = text.strip()
    if not stripped:
        return ""
    is_latex = any(marker in stripped for marker in ("\\frac", "\\sum", "\\int", "\\sqrt", "\\begin", "\\end", "\\alpha", "\\beta", "^{", "_{"))
    if is_latex:
        return f"$$\n{stripped}\n$$"
    return f"```\n{stripped}\n```"


def render_equation_markdown_metadata_aware(
    source_metadata: dict[str, object] | None,
    source_text: str,
) -> str:
    """PDF v2 M3.3 export wire-up.

    When the modality pipeline tagged this block with
    `equation_render_mode == "latex"` and stashed `equation_latex`,
    render the recovered LaTeX wrapped in `$$...$$`. Otherwise fall
    back to the historical heuristic in `_render_math_markdown`.
    """
    md = source_metadata or {}
    render_mode = md.get("equation_render_mode")
    if render_mode == "latex":
        latex = str(md.get("equation_latex") or "").strip()
        if latex:
            return f"$$\n{latex}\n$$"
    return render_math_markdown(source_text)


def render_table_markdown_metadata_aware(
    source_metadata: dict[str, object] | None,
    source_text: str,
) -> str | None:
    """PDF v2 M3.2 export wire-up.

    When the modality pipeline (heuristic or TATR) recovered a
    markdown grid into `table_markdown`, surface it directly.
    Otherwise fall back to the source-text-driven table heuristic.
    """
    md = source_metadata or {}
    markdown = str(md.get("table_markdown") or "").strip()
    if markdown:
        return markdown
    return markdown_table_from_source_text(source_text)


def epub_relative_asset_path(asset_path: str) -> str:
    normalized = PurePosixPath(asset_path)
    if normalized.parts and normalized.parts[0] == "assets":
        return PurePosixPath("..", *normalized.parts).as_posix()
    return normalized.as_posix()


def build_rebuilt_epub_stylesheet() -> str:
    return (
        "body{font-family:Georgia,'Times New Roman',serif;line-height:1.7;margin:0 auto;max-width:48rem;"
        "padding:1.2rem;color:#1f2933;background:#fffdfa;}"
        "h1,h2,h3{font-family:'Helvetica Neue','Arial',sans-serif;line-height:1.2;color:#17313a;}"
        "h1{font-size:1.9rem;margin:0 0 0.8rem;}h2{font-size:1.45rem;margin:1.6rem 0 0.6rem;}"
        "p{margin:0.8rem 0;}blockquote{margin:1rem 0;padding-left:1rem;border-left:0.25rem solid #9dc8cf;}"
        "pre{white-space:pre-wrap;background:#f5f8fc;border:1px solid #dbe4ef;border-radius:0.5rem;padding:0.9rem;overflow-x:auto;}"
        "code{font-family:'SFMono-Regular',Menlo,monospace;}figure{margin:1rem 0;}img{max-width:100%;height:auto;}"
        ".chapter-meta,.source-note,.artifact-note,.caption{color:#5c6776;font-size:0.95rem;}"
        ".artifact{margin:1rem 0;padding:0.9rem;border:1px solid #dbe4ef;border-radius:0.75rem;background:#fbfcfe;}"
        ".source-note{margin-top:0.5rem;font-style:italic;}.toc ol{padding-left:1.2rem;}"
        "table{border-collapse:collapse;width:100%;margin:1rem 0;}th,td{border:1px solid #dbe4ef;padding:0.45rem 0.6rem;text-align:left;}"
        "thead th{background:#eef4fb;}"
    )


def render_block_rebuilt_epub_xhtml(
    block: MergedRenderBlock,
    asset_path_by_block_id: dict[str, str] | None = None,
) -> str:
    source_html = format_inline_text(block.source_text)
    target_html = format_inline_text(block.target_text or "")
    notice = html.escape(str(block.notice or "").strip())
    asset_src = str((asset_path_by_block_id or {}).get(block.block_id) or "").strip()
    if asset_src:
        asset_src = epub_relative_asset_path(asset_src)
    image_alt_text = html.escape(str(block.source_metadata.get("image_alt") or block.source_text or "Embedded image"))

    if block.render_mode == "source_artifact_full_width":
        note_html = f"<div class='artifact-note'>{notice}</div>" if notice else ""
        if asset_src and block.artifact_kind in {"image", "figure"}:
            caption_html = ""
            if block.source_text and block.source_text not in {"", "[Image]"}:
                caption_html = f"<figcaption class='caption'>{source_html}</figcaption>"
            return (
                "<section class='artifact'>"
                f"{note_html}<figure><img src='{html.escape(asset_src)}' alt='{image_alt_text}' />{caption_html}</figure>"
                "</section>"
            )
        if block.artifact_kind == "equation":
            body_html = f"<pre><code>{html.escape(block.source_text or '')}</code></pre>"
        elif block.artifact_kind == "code":
            body_html = (
                f"<pre><code>{format_preformatted_text(block.source_text, block=block)}</code></pre>"
            )
        elif block.artifact_kind == "table":
            table_html = render_structured_table_html(block.source_text)
            body_html = table_html or f"<pre><code>{html.escape(block.source_text or '')}</code></pre>"
        else:
            body_html = f"<div>{source_html}</div>"
        return f"<section class='artifact'>{note_html}{body_html}</section>"

    if block.render_mode == "translated_wrapper_with_preserved_artifact":
        translated_html = f"<p>{target_html}</p>" if block.target_text else ""
        note_html = f"<div class='artifact-note'>{notice}</div>" if notice else ""
        if block.artifact_kind == "equation":
            artifact_html = f"<pre><code>{html.escape(block.source_text or '')}</code></pre>"
        elif block.artifact_kind == "table":
            artifact_html = (
                render_structured_table_html(block.source_text)
                or f"<pre><code>{html.escape(block.source_text or '')}</code></pre>"
            )
        else:
            artifact_html = f"<pre><code>{html.escape(block.source_text or '')}</code></pre>"
        return f"<section class='artifact'>{translated_html}{note_html}{artifact_html}</section>"

    if block.render_mode == "image_anchor_with_translated_caption":
        figure_html = ""
        if asset_src:
            figure_html = f"<figure><img src='{html.escape(asset_src)}' alt='{image_alt_text}' /></figure>"
        elif block.source_text:
            figure_html = f"<div class='artifact-note'>{source_html}</div>"
        caption_parts = []
        if block.target_text:
            caption_parts.append(f"<p>{target_html}</p>")
        if notice:
            caption_parts.append(f"<div class='artifact-note'>{notice}</div>")
        if block.source_text and block.source_text != block.target_text:
            caption_parts.append(f"<div class='source-note'>Source: {source_html}</div>")
        return f"<section class='artifact'>{figure_html}{''.join(caption_parts)}</section>"

    if block.render_mode == "reference_preserve_with_translated_label":
        translated_html = f"<p>{target_html}</p>" if block.target_text and block.target_text != block.source_text else ""
        return f"<section class='artifact'>{translated_html}<div>{source_html}</div></section>"

    text_html = target_html or source_html
    source_note = ""
    if block.source_text and block.target_text and block.source_text != block.target_text:
        source_note = f"<div class='source-note'>Source: {source_html}</div>"
    if block.block_type == BlockType.HEADING.value:
        return f"<h2>{text_html}</h2>"
    if block.block_type == BlockType.QUOTE.value:
        return f"<blockquote><p>{text_html}</p>{source_note}</blockquote>"
    if block.block_type == BlockType.CAPTION.value:
        return f"<p class='caption'>{text_html}</p>"
    if block.block_type == BlockType.LIST_ITEM.value:
        return f"<p>{text_html}</p>{source_note}"
    return f"<p>{text_html}</p>{source_note}"


def build_rebuilt_epub_chapter_xhtml(
    chapter_bundle: ChapterExportBundle,
    *,
    visible_ordinal: int,
    title_text: str | None,
    render_blocks: list[MergedRenderBlock],
    asset_path_by_block_id: dict[str, str] | None = None,
) -> str:
    body = [
        f"<h1>{html.escape(str(title_text or chapter_bundle.chapter.title_tgt or chapter_bundle.chapter.title_src or f'Chapter {visible_ordinal}'))}</h1>",
    ]
    if chapter_bundle.chapter.title_src and title_text and chapter_bundle.chapter.title_src != title_text:
        body.append(f"<p class='chapter-meta'>Source title: {html.escape(chapter_bundle.chapter.title_src)}</p>")
    for block in render_blocks:
        rendered = render_block_rebuilt_epub_xhtml(block, asset_path_by_block_id)
        if rendered:
            body.append(rendered)
    return (
        "<?xml version='1.0' encoding='utf-8'?>"
        "<html xmlns='http://www.w3.org/1999/xhtml' xml:lang='zh-CN'>"
        "<head>"
        f"<title>{html.escape(str(title_text or chapter_bundle.chapter.title_src or f'Chapter {visible_ordinal}'))}</title>"
        "<meta charset='utf-8' />"
        "<link rel='stylesheet' type='text/css' href='../styles/book.css' />"
        "</head>"
        f"<body>{''.join(body)}</body>"
        "</html>"
    )


def url_encode_asset_path(path: str) -> str:
    if not path:
        return ""
    return urllib.parse.quote(path, safe="/-_.~")


def markdown_image_reference(alt_text: str, path: str) -> str:
    escaped_alt = re.sub(r"([\[\]\\])", r"\\\1", str(alt_text or ""))
    return f"![{escaped_alt}]({url_encode_asset_path(path)})"


def build_merged_toc(
    visible_chapters: list[tuple[int, ChapterExportBundle, list[MergedRenderBlock], str | None]],
) -> str:
    items: list[str] = []
    for visible_ordinal, chapter_bundle, _render_blocks, title_text in visible_chapters:
        if not title_text:
            continue
        items.append(
            "<li class='toc-item'>"
            f"<a href='#chapter-{html.escape(chapter_bundle.chapter.id)}'>"
            f"<span class='toc-ordinal'>Chapter {visible_ordinal}</span>"
            f"{html.escape(title_text)}"
            "</a>"
            "</li>"
        )
    return f"<ol class='toc-list'>{''.join(items)}</ol>" if items else ""


def usage_summary_css() -> str:
    return (
        ".usage-summary{margin:0 0 22px;padding:18px 22px;border:1px solid rgba(184,197,218,.75);"
        "border-radius:18px;background:linear-gradient(180deg,#fbfdff 0%,#f3f7fc 100%);"
        "box-shadow:0 6px 18px rgba(60,74,97,.05);}"
        ".usage-summary .usage-kicker{font-family:var(--font-ui);font-size:11px;letter-spacing:.14em;"
        "text-transform:uppercase;color:var(--accent);font-weight:700;margin-bottom:10px;}"
        ".usage-summary .usage-list{list-style:none;margin:0;padding:0;display:grid;gap:6px;}"
        ".usage-summary .usage-row{display:flex;justify-content:space-between;gap:16px;font-family:var(--font-ui);"
        "font-size:14px;color:#314152;}"
        ".usage-summary .usage-label{color:var(--muted);}"
        ".usage-summary .usage-value{font-variant-numeric:tabular-nums;color:#17313a;font-weight:600;}"
        "@media (max-width:600px){.usage-summary .usage-row{flex-direction:column;gap:2px;}}"
    )
