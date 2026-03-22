from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol, Sequence

from book_agent.domain.enums import BlockType, Severity
from book_agent.infra.repositories.export import ChapterExportBundle

_HEADING_TAG_PATTERN = re.compile(r"^h([1-6])$", re.IGNORECASE)


class RenderBlockLike(Protocol):
    block_id: str
    chapter_id: str
    block_type: str
    render_mode: str
    artifact_kind: str | None
    source_text: str
    target_text: str | None
    source_metadata: dict[str, object]


@dataclass(slots=True, frozen=True)
class LayoutValidationIssue:
    issue_code: str
    message: str
    block_id: str
    block_type: str
    severity: Severity
    blocking: bool = True
    evidence: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class LayoutValidationResult:
    chapter_id: str
    issues: list[LayoutValidationIssue]

    @property
    def issue_count(self) -> int:
        return len(self.issues)

    @property
    def blocking_issue_count(self) -> int:
        return sum(1 for issue in self.issues if issue.blocking)

    @property
    def has_blocking_issues(self) -> bool:
        return any(issue.blocking for issue in self.issues)


class LayoutValidationService:
    schema_version = 1

    def validate_chapter(
        self,
        bundle: ChapterExportBundle,
        render_blocks: Sequence[RenderBlockLike],
    ) -> LayoutValidationResult:
        issues: list[LayoutValidationIssue] = []
        previous_heading_level: int | None = None

        for block in render_blocks:
            metadata = self._metadata(block)
            issues.extend(self._validate_heading(block, metadata, previous_heading_level))
            issues.extend(self._validate_figure(block, metadata))
            issues.extend(self._validate_footnote(block, metadata))
            issues.extend(self._validate_table(block, metadata))

            heading_level = self._heading_level(metadata)
            if block.block_type == BlockType.HEADING.value and heading_level is not None:
                previous_heading_level = heading_level

        return LayoutValidationResult(chapter_id=bundle.chapter.id, issues=issues)

    def _validate_heading(
        self,
        block: RenderBlockLike,
        metadata: dict[str, object],
        previous_heading_level: int | None,
    ) -> list[LayoutValidationIssue]:
        if block.block_type != BlockType.HEADING.value:
            return []

        issues: list[LayoutValidationIssue] = []
        heading_text = self._normalize_text(block.target_text or block.source_text)
        if not heading_text:
            issues.append(
                LayoutValidationIssue(
                    issue_code="HEADING_EMPTY",
                    message="Heading render block is empty after normalization.",
                    block_id=block.block_id,
                    block_type=block.block_type,
                    severity=Severity.HIGH,
                    evidence={"render_mode": block.render_mode},
                )
            )

        current_level = self._heading_level(metadata)
        if (
            previous_heading_level is not None
            and current_level is not None
            and current_level > previous_heading_level + 1
        ):
            issues.append(
                LayoutValidationIssue(
                    issue_code="HEADING_LEVEL_SKIP",
                    message="Heading levels skip more than one level.",
                    block_id=block.block_id,
                    block_type=block.block_type,
                    severity=Severity.HIGH,
                    evidence={
                        "previous_heading_level": previous_heading_level,
                        "current_heading_level": current_level,
                    },
                )
            )
        return issues

    def _validate_figure(
        self,
        block: RenderBlockLike,
        metadata: dict[str, object],
    ) -> list[LayoutValidationIssue]:
        if block.artifact_kind not in {"image", "figure"}:
            return []
        if self._has_image_asset(metadata) or self._has_valid_bbox_region(metadata):
            return []
        return [
            LayoutValidationIssue(
                issue_code="FIGURE_ASSET_MISSING",
                message="Figure/image block has no exportable asset path or crop region.",
                block_id=block.block_id,
                block_type=block.block_type,
                severity=Severity.HIGH,
                evidence={"artifact_kind": block.artifact_kind, "render_mode": block.render_mode},
            )
        ]

    def _validate_footnote(
        self,
        block: RenderBlockLike,
        metadata: dict[str, object],
    ) -> list[LayoutValidationIssue]:
        if block.block_type != BlockType.FOOTNOTE.value:
            return []

        issues: list[LayoutValidationIssue] = []
        footnote_text = self._normalize_text(block.target_text or block.source_text)
        if not footnote_text:
            issues.append(
                LayoutValidationIssue(
                    issue_code="FOOTNOTE_EMPTY",
                    message="Footnote block is empty after normalization.",
                    block_id=block.block_id,
                    block_type=block.block_type,
                    severity=Severity.HIGH,
                    evidence={"render_mode": block.render_mode},
                )
            )
        if metadata.get("footnote_anchor_matched") is False:
            issues.append(
                LayoutValidationIssue(
                    issue_code="FOOTNOTE_ANCHOR_ORPHANED",
                    message="Footnote block is missing a matched source anchor.",
                    block_id=block.block_id,
                    block_type=block.block_type,
                    severity=Severity.MEDIUM,
                    evidence={"footnote_anchor_label": metadata.get("footnote_anchor_label")},
                )
            )
        return issues

    def _validate_table(
        self,
        block: RenderBlockLike,
        metadata: dict[str, object],
    ) -> list[LayoutValidationIssue]:
        if block.artifact_kind != "table" and block.block_type != BlockType.TABLE.value:
            return []

        source_text = str(block.source_text or "").strip()
        if not source_text:
            return [
                LayoutValidationIssue(
                    issue_code="TABLE_EMPTY",
                    message="Table block has no source text to preserve.",
                    block_id=block.block_id,
                    block_type=block.block_type,
                    severity=Severity.HIGH,
                    evidence={"render_mode": block.render_mode},
                )
            ]

        if self._looks_like_renderable_table(source_text):
            return []

        return [
            LayoutValidationIssue(
                issue_code="TABLE_STRUCTURE_UNRENDERABLE",
                message="Table block does not contain recognizable table structure.",
                block_id=block.block_id,
                block_type=block.block_type,
                severity=Severity.HIGH,
                evidence={
                    "render_mode": block.render_mode,
                    "artifact_kind": block.artifact_kind,
                    "source_excerpt": source_text[:160],
                    "has_linked_caption_text": bool(metadata.get("linked_caption_text")),
                },
            )
        ]

    def _metadata(self, block: RenderBlockLike) -> dict[str, object]:
        metadata = getattr(block, "source_metadata", None)
        return dict(metadata) if isinstance(metadata, dict) else {}

    def _heading_level(self, metadata: dict[str, object]) -> int | None:
        for key in ("heading_level", "source_heading_level", "epub_heading_level", "pdf_heading_level"):
            value = metadata.get(key)
            if isinstance(value, int) and 1 <= value <= 6:
                return value
            if isinstance(value, str) and value.isdigit():
                numeric_value = int(value)
                if 1 <= numeric_value <= 6:
                    return numeric_value

        tag = str(metadata.get("tag") or "").strip()
        match = _HEADING_TAG_PATTERN.match(tag)
        if match is None:
            return None
        return int(match.group(1))

    def _has_image_asset(self, metadata: dict[str, object]) -> bool:
        image_src = str(metadata.get("image_src") or "").strip()
        return bool(image_src)

    def _has_valid_bbox_region(self, metadata: dict[str, object]) -> bool:
        source_bbox_json = metadata.get("source_bbox_json")
        if not isinstance(source_bbox_json, dict):
            return False
        regions = source_bbox_json.get("regions")
        if not isinstance(regions, list) or not regions:
            return False
        first_region = regions[0]
        if not isinstance(first_region, dict):
            return False
        page_number = first_region.get("page_number")
        bbox = first_region.get("bbox")
        if not isinstance(page_number, int) or not isinstance(bbox, list) or len(bbox) != 4:
            return False
        try:
            x0, y0, x1, y1 = (float(value) for value in bbox)
        except (TypeError, ValueError):
            return False
        return x1 > x0 and y1 > y0

    def _looks_like_renderable_table(self, text: str) -> bool:
        normalized = text.strip()
        if not normalized:
            return False
        lowered = normalized.casefold()
        if "<table" in lowered and "</table>" in lowered:
            return True

        lines = [line.strip() for line in normalized.splitlines() if line.strip()]
        if len(lines) < 2:
            return False

        rows = [self._split_table_candidate_line(line) for line in lines]
        if any(row is None for row in rows):
            return False

        normalized_rows = [row for row in rows if row]
        if len(normalized_rows) < 2:
            return False

        if len(normalized_rows) >= 3 and self._is_table_separator_row(normalized_rows[1]):
            normalized_rows.pop(1)
        if len(normalized_rows) < 2:
            return False

        column_count = len(normalized_rows[0])
        if column_count < 2 or column_count > 8:
            return False
        return all(len(row) == column_count for row in normalized_rows)

    def _split_table_candidate_line(self, line: str) -> list[str] | None:
        stripped = line.strip().strip("|").strip()
        if not stripped:
            return None
        if "|" in stripped:
            pipe_cells = [cell.strip() for cell in stripped.split("|")]
            pipe_cells = [cell for cell in pipe_cells if cell]
            if len(pipe_cells) >= 2:
                return pipe_cells
        spaced_cells = [cell.strip() for cell in re.split(r"\t+|\s{2,}", stripped) if cell.strip()]
        if len(spaced_cells) >= 2:
            return spaced_cells
        return None

    def _is_table_separator_row(self, row: list[str]) -> bool:
        if not row:
            return False
        return all(bool(re.fullmatch(r":?-{2,}:?", cell.strip())) for cell in row)

    def _normalize_text(self, text: str | None) -> str:
        return re.sub(r"\s+", " ", (text or "")).strip()
