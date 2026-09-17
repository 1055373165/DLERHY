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
from collections.abc import Mapping
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
