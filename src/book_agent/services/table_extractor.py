"""Table modality (PDF v2 M3.2).

Heuristic table-structure recovery + protocol layer. Two surfaces:

  1. **Heuristic extractor** — `extract_table_structure(text)` infers
     a row × column grid from a multi-line text block by detecting
     consistent whitespace gutters and renders it as a markdown table.
     Tuned for the dominant case in technical books: tables that read
     correctly as monospaced text in the source PDF.

  2. **Adapter Protocol** — `TableExtractorAdapter` lets a future
     TATR / pubtables-1M / docling integration replace the heuristic
     without touching call sites. The default heuristic implementation
     fulfills the protocol so the wiring is end-to-end exercisable.

When extraction confidence is low (gutters inconsistent, rows uneven)
we leave the block text as-is and stamp `translatability=translate_none`
— half-recovered table structure is worse than the raw text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final, Protocol

from book_agent.domain.structure.models import TRANSLATE_NONE, ParsedBlock

# Minimum number of rows to attempt structural recovery. Below this it's
# almost always a false-positive (e.g., a 1-line caption that mentions
# "Table 1.2"). Two rows are also too thin — we want at least a header
# plus two data rows for the gutter-detection to be reliable.
_MIN_ROWS_FOR_RECOVERY: Final[int] = 3

# Minimum whitespace run width (in characters) to count as a column
# separator. Single-space gaps inside cell text would otherwise split
# every word into its own column.
_MIN_GUTTER_WIDTH: Final[int] = 2

# A table candidate must show a column gutter at the same character
# offset on at least this fraction of rows, otherwise we treat it as
# free prose.
_GUTTER_CONSISTENCY_THRESHOLD: Final[float] = 0.6


@dataclass(slots=True, frozen=True)
class TableStructure:
    """A successfully recovered table.

    `markdown` is the rendered output ready to embed in a markdown export.
    `cells` is row-major: `cells[r][c]` is the (r, c) cell text.
    `confidence` ∈ [0, 1] — heuristic certainty; consumers may decide
    not to render a table at very low confidence even though it parsed.
    """

    cells: tuple[tuple[str, ...], ...]
    markdown: str
    confidence: float
    column_count: int = field(init=False)

    def __post_init__(self) -> None:
        # frozen dataclass — bypass via object.__setattr__.
        object.__setattr__(
            self,
            "column_count",
            max((len(row) for row in self.cells), default=0),
        )


class TableExtractorAdapter(Protocol):
    """Protocol for swapping in a real ML-based table extractor (M3.2b)."""

    def extract(self, block_text: str) -> TableStructure | None:
        """Return a structured table or `None` if not confidently recoverable."""
        ...


# --- Heuristic implementation ---


def looks_like_table(text: str) -> bool:
    """Quick filter — does this block plausibly contain tabular data?

    Cheap pre-check used by callers that scan many blocks; the real
    structural decision happens in `extract_table_structure`.
    """
    if not text:
        return False
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < _MIN_ROWS_FOR_RECOVERY:
        return False
    # Need at least one wide gutter on a majority of lines.
    gutter_lines = sum(1 for line in lines if re.search(r" {2,}\S", line))
    return gutter_lines / len(lines) >= _GUTTER_CONSISTENCY_THRESHOLD


def extract_table_structure(text: str) -> TableStructure | None:
    """Heuristic recovery: detect column gutters by whitespace alignment.

    Algorithm:
      1. Split into non-empty lines (rows).
      2. For each character position, count how many lines have a
         space at that position. If a contiguous range of positions
         is consistently whitespace across most rows, that's a gutter.
      3. Splitting columns at gutter midpoints gives cells.
      4. Confidence = gutter consistency.
    """
    if not text:
        return None
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if len(lines) < _MIN_ROWS_FOR_RECOVERY:
        return None

    max_len = max(len(line) for line in lines)
    if max_len < 8:
        return None

    # For each column index, count lines that have whitespace there.
    space_count = [0] * max_len
    for line in lines:
        padded = line.ljust(max_len)
        for i, ch in enumerate(padded):
            if ch == " ":
                space_count[i] += 1

    threshold = max(1, int(len(lines) * _GUTTER_CONSISTENCY_THRESHOLD))
    is_gutter = [count >= threshold for count in space_count]

    # Find contiguous gutter runs.
    gutter_ranges: list[tuple[int, int]] = []
    start = None
    for i, gap in enumerate(is_gutter):
        if gap and start is None:
            start = i
        elif not gap and start is not None:
            if i - start >= _MIN_GUTTER_WIDTH:
                gutter_ranges.append((start, i))
            start = None
    if start is not None and (max_len - start) >= _MIN_GUTTER_WIDTH:
        gutter_ranges.append((start, max_len))

    # Drop a leading or trailing all-space gutter (those are margins).
    interior_gutters = [
        (lo, hi) for lo, hi in gutter_ranges
        if lo > 0 and hi < max_len
    ]
    if not interior_gutters:
        return None

    # Build column boundary indices: midpoint of each interior gutter.
    boundaries = [0] + [(lo + hi) // 2 for lo, hi in interior_gutters] + [max_len]

    cells: list[tuple[str, ...]] = []
    for line in lines:
        padded = line.ljust(max_len)
        row_cells = []
        for c in range(len(boundaries) - 1):
            cell = padded[boundaries[c] : boundaries[c + 1]].strip()
            row_cells.append(cell)
        cells.append(tuple(row_cells))

    column_count = len(boundaries) - 1
    if column_count < 2:
        return None

    consistency = sum(1 for row in cells if len(row) == column_count) / len(cells)
    if consistency < _GUTTER_CONSISTENCY_THRESHOLD:
        return None

    confidence = round(min(1.0, consistency), 3)
    return TableStructure(
        cells=tuple(cells),
        markdown=_render_markdown_table(cells),
        confidence=confidence,
    )


def _render_markdown_table(cells: list[tuple[str, ...]] | tuple[tuple[str, ...], ...]) -> str:
    rows = list(cells)
    if not rows:
        return ""
    column_count = max(len(r) for r in rows)
    header = list(rows[0])
    while len(header) < column_count:
        header.append("")
    separator = ["---"] * column_count
    out_lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(separator) + " |",
    ]
    for row in rows[1:]:
        padded = list(row) + [""] * (column_count - len(row))
        out_lines.append("| " + " | ".join(padded) + " |")
    return "\n".join(out_lines)


class HeuristicTableExtractor:
    """Default `TableExtractorAdapter` impl — pure heuristic."""

    def extract(self, block_text: str) -> TableStructure | None:
        if not looks_like_table(block_text):
            return None
        return extract_table_structure(block_text)


# --- Block-level convenience ---


def enhance_block_for_table(
    block: ParsedBlock,
    *,
    extractor: TableExtractorAdapter | None = None,
) -> tuple[ParsedBlock, TableStructure | None]:
    """If `block` is classified as a table, attempt structural recovery.

    Returns the (possibly rewritten) block and the recovered structure
    (or None when recovery was inconclusive). The block always carries
    `translatability=translate_none` after this call because table
    cells should not be translated as prose either way.
    """
    from dataclasses import replace as _replace

    if block.block_type != "table":
        return block, None
    structure = (extractor or HeuristicTableExtractor()).extract(block.text)
    new_metadata = dict(block.metadata)
    if structure is not None:
        new_metadata["table_markdown"] = structure.markdown
        new_metadata["table_confidence"] = structure.confidence
        new_metadata["table_column_count"] = structure.column_count
    new_block = _replace(
        block,
        translatability=TRANSLATE_NONE,
        metadata=new_metadata,
    )
    return new_block, structure
