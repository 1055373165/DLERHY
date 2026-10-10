"""Table cells as translatable units (06 B-20).

A table block stays a protected artifact (its layout is never rewritten),
but the prose inside its cells is translated. Segmentation turns every
distinct translatable cell into its own sentence; the exporter substitutes
the translations back into the grid.

A cell is translatable when it reads as words: at least one run of three
letters, not a number or unit, not an identifier, path, URL or formula, and
not a short all-caps token (API, HTTP, CPU) that stays as written.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

MAX_CELL_CHARS = 300

_WORD = re.compile(r"[A-Za-z]{3,}")
_CODE_LIKE = re.compile(r"[=_{}<>\\]|\w\(|://|^[./~]\S*/\S*$")
_ALL_CAPS_TOKEN = re.compile(r"^[A-Z0-9][A-Z0-9.+\-/]{0,7}$")


def split_table_candidate_line(line: str) -> list[str] | None:
    bare = line.strip()
    if len(bare) > 2 and bare.startswith("|") and bare.endswith("|"):
        # A fully delimited row keeps its empty cells so later cells stay in their columns.
        cells = [cell.strip() for cell in bare[1:-1].split("|")]
        if len(cells) >= 2 and any(cells):
            return cells
    stripped = bare.strip("|").strip()
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
    padded_rows = [row + [""] * (column_count - len(row)) if len(row) < column_count else row for row in normalized_rows]
    header = padded_rows[0]
    body_rows = padded_rows[1:]
    if not body_rows:
        return None
    return header, body_rows


def markdown_table_rows(markdown: str) -> list[list[str]] | None:
    lines = [line.strip() for line in str(markdown or "").splitlines() if line.strip()]
    if len(lines) < 2 or not all(line.startswith("|") and line.endswith("|") for line in lines):
        return None
    if "---" not in lines[1]:
        return None
    return [[cell.strip() for cell in line.strip("|").split("|")] for i, line in enumerate(lines) if i != 1]


def table_grid(source_text: str | None, metadata: Mapping[str, Any] | None = None) -> list[list[str]] | None:
    """The cell grid a table renders from: the recovered markdown grid if any, else the source text."""
    markdown = str((metadata or {}).get("table_markdown") or "").strip()
    if markdown:
        rows = markdown_table_rows(markdown)
        if rows:
            return rows
    parsed = parse_structured_table_rows(source_text or "")
    if parsed is None:
        return None
    header, body = parsed
    return [header, *body]


def is_translatable_cell(text: str) -> bool:
    cell = " ".join(str(text or "").split())
    if not cell or len(cell) > MAX_CELL_CHARS:
        return False
    if not _WORD.search(cell):
        return False
    if _ALL_CAPS_TOKEN.fullmatch(cell):
        return False
    if _CODE_LIKE.search(cell):
        return False
    return True


def translatable_cells(block_type: str, source_text: str | None, metadata: Mapping[str, Any] | None = None) -> list[str]:
    """Distinct translatable cell texts of a table block, in reading order; [] for anything else."""
    normalized_type = getattr(block_type, "value", block_type)
    if normalized_type != "table":
        return []
    if (metadata or {}).get("translatable") is False:
        return []
    grid = table_grid(source_text, metadata)
    if not grid:
        return []
    seen: dict[str, None] = {}
    for row in grid:
        for cell in row:
            normalized = " ".join(cell.split())
            if is_translatable_cell(normalized):
                seen.setdefault(normalized, None)
    return list(seen)


def translate_cell(cell: str, translations: Mapping[str, str] | None) -> str:
    if not translations:
        return cell
    return translations.get(" ".join(str(cell).split()), cell)


def _translated_grid(text: str) -> list[list[str]] | None:
    rows = []
    for line in str(text or "").splitlines():
        if not line.strip():
            continue
        cells = split_table_candidate_line(line)
        if cells is None or "|" not in line:
            return None
        if not is_table_separator_row(cells):
            rows.append([cell.strip() for cell in cells])
    return rows or None


def _flattened_grid(text: str, shape: Sequence[int]) -> list[list[str]] | None:
    """A translated grid whose line breaks the model dropped ("| a | b | | c | d |"), cut back into
    rows of ``shape`` (each row's cell count): a row boundary leaves one empty cell between rows."""
    stripped = " ".join(str(text or "").split())
    if "\n" in str(text or "").strip() or not (stripped.startswith("|") and stripped.endswith("|")) or not shape:
        return None
    cells = [cell.strip() for cell in stripped[1:-1].split("|")]
    if len(cells) != sum(shape) + len(shape) - 1:
        return None
    rows: list[list[str]] = []
    position = 0
    for index, width in enumerate(shape):
        rows.append(cells[position : position + width])
        position += width
        if index < len(shape) - 1:
            if cells[position]:
                return None
            position += 1
    return rows


def split_shared_cell_translations(grid: list[list[str]] | None, translations: Mapping[str, str]) -> dict[str, str]:
    """Cells that came back from the model as one translated table, split back cell by cell.

    The model sometimes answers a table's cells with one segment holding the whole translated
    grid ("| 策略 | 描述 |\\n| 生成 | ... |"); every cell then maps to that whole text and the
    reader sees the grid in each cell. When such a shared text is a grid of the same shape as the
    source rows its cells come from, each cell takes the translation at its own position.
    """
    result = dict(translations)
    if not grid:
        return result
    sources_by_text: dict[str, set[str]] = {}
    for source, target in translations.items():
        sources_by_text.setdefault(target, set()).add(source)
    for target, sources in sources_by_text.items():
        if len(sources) < 2:
            continue
        # The rows those cells come from, or the whole table when the model translated all of it.
        rows = [row for row in grid if any(" ".join(cell.split()) in sources for cell in row)]
        translated = _translated_grid(target)
        if translated and len(translated) == 1 and len(rows) > 1:
            translated = _flattened_grid(target, [len(row) for row in rows]) or _flattened_grid(
                target, [len(row) for row in grid]
            )
        if not translated:
            continue
        if len(rows) != len(translated):
            rows = list(grid)
        if len(rows) != len(translated) or any(len(row) != len(cells) for row, cells in zip(rows, translated)):
            continue
        for row, cells in zip(rows, translated):
            for source_cell, target_cell in zip(row, cells):
                key = " ".join(source_cell.split())
                if key in sources and target_cell:
                    result[key] = target_cell
    return result
