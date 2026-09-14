"""Data types shared by the export renderers."""


from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from book_agent.domain.models.review import Export, IssueAction, ReviewIssue


@dataclass(slots=True, frozen=True)
class _PdfPageLayoutBlock:
    bbox: list[float]
    text: str
    block_type: int | None


@dataclass(slots=True)
class ExportArtifacts:
    export_record: Export
    file_path: Path
    manifest_path: Path | None = None


@dataclass(slots=True)
class ExportFollowupAction:
    action_id: str
    issue_id: str
    action_type: str
    scope_type: str
    scope_id: str | None
    suggested_run_followup: bool = True


@dataclass(slots=True)
class ExportMisalignmentEvidence:
    active_target_map: dict[str, object]
    rendered_targets_by_sentence: dict[str, list[str]]
    missing_target_sentence_ids: list[str]
    sentence_ids_with_only_inactive_targets: list[str]
    orphan_target_segment_ids: list[str]
    inactive_target_segment_ids_with_edges: list[str]

    @property
    def has_anomalies(self) -> bool:
        return bool(
            self.missing_target_sentence_ids
            or self.sentence_ids_with_only_inactive_targets
            or self.orphan_target_segment_ids
        )


@dataclass(slots=True)
class ExportIssueSyncArtifacts:
    issues: list[ReviewIssue]
    actions: list[IssueAction]


@dataclass(slots=True)
class MergedRenderBlock:
    block_id: str
    chapter_id: str
    block_type: str
    render_mode: str
    artifact_kind: str | None
    title: str | None
    source_text: str
    target_text: str | None
    source_metadata: dict[str, object]
    source_sentence_ids: list[str]
    target_segment_ids: list[str]
    is_expected_source_only: bool
    notice: str | None
