from __future__ import annotations

import html
import json
import posixpath
import re
import shutil
import zipfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from book_agent.core.ids import stable_id
from book_agent.domain.enums import (
    ActionActorType,
    ActionStatus,
    ActionType,
    BlockType,
    ChapterStatus,
    Detector,
    DocumentStatus,
    ExportStatus,
    ExportType,
    IssueStatus,
    JobScopeType,
    RootCauseLayer,
    SentenceStatus,
    Severity,
    SourceType,
    TargetSegmentStatus,
)
from book_agent.domain.models.review import Export, IssueAction, ReviewIssue
from book_agent.domain.structure.artifact_grouping import resolve_artifact_group_context_ids
from book_agent.infra.repositories.export import ChapterExportBundle, DocumentExportBundle, ExportRepository
from book_agent.orchestrator.rule_engine import IssueRoutingContext, resolve_action

_SPECIAL_PDF_PAGE_FAMILIES = {"frontmatter", "appendix", "references", "index", "backmatter", "toc"}
_TERMINAL_PUNCTUATION = (".", "!", "?", ":", ";", "\"", "'", "\u201d", "\u2019")


@dataclass(slots=True, frozen=True)
class _PdfPageLayoutBlock:
    bbox: list[float]
    text: str
    block_type: int | None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _excerpt_text(text: str, *, limit: int = 220) -> str:
    normalized = re.sub(r"\s+", " ", (text or "")).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 3)].rstrip() + "..."


def _normalize_render_text(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _normalize_signature_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().casefold()


def _looks_like_metadata_filename(value: str) -> bool:
    candidate = re.sub(r"\s+", " ", (value or "")).strip().casefold()
    if not candidate:
        return False
    if "/" in candidate or "\\" in candidate:
        return True
    return candidate.endswith((".html", ".xhtml", ".htm", ".xml", ".opf", ".ncx"))


def _display_author_value(author: str | None) -> str | None:
    normalized = re.sub(r"\s+", " ", (author or "")).strip()
    if not normalized or _looks_like_metadata_filename(normalized):
        return None
    return normalized


def _pdf_profile_payload(document) -> dict[str, object]:
    metadata = getattr(document, "metadata_json", None)
    if not isinstance(metadata, dict):
        return {}
    profile = metadata.get("pdf_profile")
    return profile if isinstance(profile, dict) else {}


def _is_academic_paper_document(document) -> bool:
    return str(_pdf_profile_payload(document).get("recovery_lane") or "").strip() == "academic_paper"


def _is_pdf_document(document) -> bool:
    return getattr(document, "source_type", None) in {
        SourceType.PDF_TEXT,
        SourceType.PDF_MIXED,
        SourceType.PDF_SCAN,
    }


def _looks_like_heading_continuation_fragment(text: str | None) -> bool:
    normalized = _normalize_render_text(text)
    if not normalized or len(normalized) > 80:
        return False
    if _MAIN_CHAPTER_TITLE_PATTERN.match(normalized) or _LEADING_SECTION_NUMBER_PATTERN.match(normalized):
        return False
    lead = normalized.split(" ", 1)[0].casefold()
    if lead in _HEADING_CONTINUATION_START_WORDS:
        return True
    return normalized[:1].islower()


_CODE_BLOCK_KEYWORD_PATTERN = re.compile(
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
_CODE_ASSIGNMENT_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]{0,80}(?::\s*[A-Za-z_][A-Za-z0-9_\[\],. ]{0,80})?\s*=\s*.+$"
)
_OCR_TOLERANT_CODE_ASSIGNMENT_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_ ]{0,80}\s*=\s*.+$"
)
_MAIN_CHAPTER_TITLE_PATTERN = re.compile(r"^\s*chapter\s+(\d+)\b", re.IGNORECASE)
_APPENDIX_TITLE_PATTERN = re.compile(r"^\s*appendix\b", re.IGNORECASE)
_FIGURE_CAPTION_PATTERN = re.compile(r"^\s*(?:fig(?:ure)?\.?|image|diagram|chart)\b", re.IGNORECASE)
_ACADEMIC_CITATION_PATTERN = re.compile(r"\[[^\]]+\d{4}[^\]]*\]")
_ACADEMIC_FRONTMATTER_MARKER_PATTERN = re.compile(
    r"\b(?:abstract|keywords?)\b|@|"
    r"\b(?:university|institute|department|school|laboratory|center|centre|society|sciences?)\b",
    re.IGNORECASE,
)
_LEADING_SECTION_NUMBER_PATTERN = re.compile(
    r"^(?:(?:\d+(?:\.\d+)*)|[ivxlcdm]+)[.):\-]?\s+",
    re.IGNORECASE,
)
_HEADING_CONTINUATION_START_WORDS = {
    "and",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "into",
    "of",
    "on",
    "or",
    "the",
    "to",
    "via",
    "with",
    "without",
}
_PROSE_CONTINUATION_START_WORDS = _HEADING_CONTINUATION_START_WORDS.union(
    {
        "because",
        "but",
        "that",
        "which",
        "who",
        "whose",
        "where",
        "when",
        "while",
    }
)
_INLINE_CODE_LIKE_PATTERN = re.compile(
    r"(?:"
    r"\bdef\s+[A-Za-z_][A-Za-z0-9_]*\s*\("
    r"|\bclass\s+[A-Za-z_][A-Za-z0-9_]*(?:\([^)]*\))?\s*:"
    r"|\breturn\s+\S+"
    r"|\byield\s+\S+"
    r"|\braise\s+[A-Za-z_]"
    r"|\blambda\b[^\\n:]+:"
    r"|\bfrom\s+\S+\s+import\s+\S+"
    r"|\bimport\s+\S+(?:\s+as\s+\S+)?"
    r"|\btry:"
    r"|\bexcept\b.*:"
    r"|\bfinally:"
    r"|\bif\b.+:"
    r"|\belif\b.+:"
    r"|\belse:"
    r"|\bfor\b.+\bin\b.+:"
    r"|\bwhile\b.+:"
    r"|\bwith\b.+:"
    r"|==|!=|:=|->|=>|\{|\}|\[|\]|</?\w+>|`"
    r")",
    re.IGNORECASE,
)
_PROSE_ARTIFACT_STOPWORDS = {
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
    "how",
    "if",
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
_FRONTMATTER_TITLES = {
    "acknowledgment",
    "acknowledgements",
    "foreword",
    "preface",
    "prologue",
    "introduction",
}
_PURE_CHAPTER_LABEL_PATTERN = re.compile(r"^\s*chapter\s+\d+[.)]?\s*$", re.IGNORECASE)
_CHAPTER_LOWERCASE_TAIL_PATTERN = re.compile(r"^\s*chapter\s+\d+[.):]?\s+[a-z]", re.IGNORECASE)
_CJK_MAIN_CHAPTER_TITLE_PATTERN = re.compile(
    r"^\s*第[一二三四五六七八九十百千万零〇两0-9]+(?:章|节|篇|部分)\s*[：:].+"
)
_CJK_APPENDIX_TITLE_PATTERN = re.compile(r"^\s*附录\s*[A-Za-z一二三四五六七八九十百千万零〇两0-9]")
_BOOK_STRUCTURAL_HEADING_TITLES = {
    "acknowledgment",
    "acknowledgements",
    "foreword",
    "preface",
    "introduction",
    "conclusion",
    "references",
    "appendix",
    "致谢",
    "前言",
    "引言",
    "结论",
    "参考文献",
}
_BOOK_ALLOWED_REFERENCE_HEADINGS = {
    "references",
    "conclusion",
    "key takeaways",
    "overview",
    "glossary",
    "index",
}
_BOOK_PROSE_HEADING_VERB_PATTERN = re.compile(
    r"\b(?:"
    r"is|are|was|were|be|being|been|include|includes|required|requires|requiring|"
    r"returns?|returned|provides?|provided|prints?|printed|demonstrates?|"
    r"allow(?:s|ed|ing)?|instantiate(?:s|d)?|initialized|creates?|created|"
    r"stored|saving|saved|contains?|containing|uses?|using|executes?|executed|"
    r"performs?|performing|organizing|organized|designed|named"
    r")\b",
    re.IGNORECASE,
)
_SINGLE_LINE_CODEISH_PATTERN = re.compile(
    r"(?:"
    r"^\s*#"
    r"|^\s*@"
    r"|^\s*(?:async\s+def|def|class|from|import|return|yield|raise)\b"
    r"|^\s*(?:if|elif|for|while|with|except)\b.+:"
    r"|^\s*else\s*:"
    r"|^\s*try\s*:"
    r"|(?:^|\s)(?:print|invoke|Agent|LlmAgent|Runner|Runnable|ChatPromptTemplate|StrOutputParser)\s*\("
    r"|session\.state\s*\["
    r"|```"
    r"|->"
    r"|=>"
    r")",
    re.IGNORECASE,
)


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


class ExportGateError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        chapter_id: str | None = None,
        issue_ids: list[str] | None = None,
        followup_actions: list[ExportFollowupAction] | None = None,
        auto_followup_requested: bool = False,
        auto_followup_attempt_count: int = 0,
        auto_followup_attempt_limit: int | None = None,
        auto_followup_stop_reason: str | None = None,
        auto_followup_executions: list[dict] | None = None,
    ) -> None:
        super().__init__(message)
        self.chapter_id = chapter_id
        self.issue_ids = issue_ids or []
        self.followup_actions = followup_actions or []
        self.auto_followup_requested = auto_followup_requested
        self.auto_followup_attempt_count = auto_followup_attempt_count
        self.auto_followup_attempt_limit = auto_followup_attempt_limit
        self.auto_followup_stop_reason = auto_followup_stop_reason
        self.auto_followup_executions = auto_followup_executions or []

    def to_http_detail(self) -> dict:
        return {
            "message": str(self),
            "chapter_id": self.chapter_id,
            "issue_ids": self.issue_ids,
            "action_ids": [action.action_id for action in self.followup_actions],
            "auto_followup_requested": self.auto_followup_requested,
            "auto_followup_attempt_count": self.auto_followup_attempt_count,
            "auto_followup_attempt_limit": self.auto_followup_attempt_limit,
            "auto_followup_stop_reason": self.auto_followup_stop_reason,
            "auto_followup_executions": self.auto_followup_executions,
            "followup_actions": [
                {
                    "action_id": action.action_id,
                    "issue_id": action.issue_id,
                    "action_type": action.action_type,
                    "scope_type": action.scope_type,
                    "scope_id": action.scope_id,
                    "suggested_run_followup": action.suggested_run_followup,
                }
                for action in self.followup_actions
            ],
        }


class ExportService:
    def __init__(self, repository: ExportRepository, output_root: str | Path = "artifacts/exports"):
        self.repository = repository
        self.output_root = Path(output_root)

    def export_review_package(self, chapter_id: str) -> ExportArtifacts:
        return self.export_chapter(chapter_id, ExportType.REVIEW_PACKAGE)

    def export_bilingual_html(self, chapter_id: str) -> ExportArtifacts:
        return self.export_chapter(chapter_id, ExportType.BILINGUAL_HTML)

    def export_document_merged_html(self, document_id: str) -> ExportArtifacts:
        bundle = self.repository.load_document_bundle(document_id)
        for chapter_bundle in bundle.chapters:
            self._enforce_gate(chapter_bundle, ExportType.MERGED_HTML)
        output_dir = self.output_root / bundle.document.id
        output_dir.mkdir(parents=True, exist_ok=True)
        file_path = output_dir / "merged-document.html"
        manifest_path = output_dir / "merged-document.manifest.json"
        asset_path_by_block_id = self._export_epub_assets_for_document_bundle(bundle, output_dir)
        file_path.write_text(
            self._build_merged_document_html(bundle, asset_path_by_block_id),
            encoding="utf-8",
        )
        manifest_path.write_text(
            json.dumps(self._build_merged_document_manifest(bundle, file_path), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        export = self._record_document_export(bundle, ExportType.MERGED_HTML, file_path, manifest_path)
        self.repository.save_export(export)
        self._apply_document_export_status_updates(bundle, ExportType.MERGED_HTML)
        self.repository.session.flush()
        return ExportArtifacts(export_record=export, file_path=file_path, manifest_path=manifest_path)

    def export_document_merged_markdown(self, document_id: str) -> ExportArtifacts:
        bundle = self.repository.load_document_bundle(document_id)
        for chapter_bundle in bundle.chapters:
            self._enforce_gate(chapter_bundle, ExportType.MERGED_MARKDOWN)
        output_dir = self.output_root / bundle.document.id
        output_dir.mkdir(parents=True, exist_ok=True)
        file_path = output_dir / "merged-document.md"
        manifest_path = output_dir / "merged-document.markdown.manifest.json"
        asset_path_by_block_id = self._export_epub_assets_for_document_bundle(bundle, output_dir)
        file_path.write_text(
            self._build_merged_document_markdown(bundle, asset_path_by_block_id),
            encoding="utf-8",
        )
        manifest_path.write_text(
            json.dumps(
                self._build_merged_document_manifest(
                    bundle,
                    file_path,
                    export_type=ExportType.MERGED_MARKDOWN,
                ),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        export = self._record_document_export(bundle, ExportType.MERGED_MARKDOWN, file_path, manifest_path)
        self.repository.save_export(export)
        self._apply_document_export_status_updates(bundle, ExportType.MERGED_MARKDOWN)
        self.repository.session.flush()
        return ExportArtifacts(export_record=export, file_path=file_path, manifest_path=manifest_path)

    def assert_chapter_exportable(self, chapter_id: str, export_type: ExportType) -> None:
        bundle = self.repository.load_chapter_bundle(chapter_id)
        self._enforce_gate(bundle, export_type)

    def export_chapter(self, chapter_id: str, export_type: ExportType) -> ExportArtifacts:
        bundle = self.repository.load_chapter_bundle(chapter_id)
        self._enforce_gate(bundle, export_type)
        output_dir = self.output_root / bundle.chapter.document_id
        output_dir.mkdir(parents=True, exist_ok=True)
        file_path, manifest_path = self._export_files(bundle, export_type, output_dir)
        export = self._record(bundle, export_type, file_path, manifest_path)
        self.repository.save_export(export)
        self._apply_status_updates(bundle, export_type)
        self.repository.session.flush()
        return ExportArtifacts(export_record=export, file_path=file_path, manifest_path=manifest_path)

    def _export_files(
        self,
        bundle: ChapterExportBundle,
        export_type: ExportType,
        output_dir: Path,
    ) -> tuple[Path, Path | None]:
        if export_type == ExportType.REVIEW_PACKAGE:
            file_path = output_dir / f"review-package-{bundle.chapter.id}.json"
            file_path.write_text(
                json.dumps(self._build_review_package(bundle), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return file_path, None
        if export_type == ExportType.BILINGUAL_HTML:
            file_path = output_dir / f"bilingual-{bundle.chapter.id}.html"
            asset_path_by_block_id = self._export_epub_assets_for_chapter_bundle(bundle, output_dir)
            file_path.write_text(
                self._build_bilingual_html(bundle, asset_path_by_block_id),
                encoding="utf-8",
            )
            manifest_path = output_dir / f"bilingual-{bundle.chapter.id}.manifest.json"
            manifest_path.write_text(
                json.dumps(self._build_bilingual_manifest(bundle, file_path), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return file_path, manifest_path
        raise ExportGateError(f"Unsupported export type in P0: {export_type.value}")

    def _record(
        self,
        bundle: ChapterExportBundle,
        export_type: ExportType,
        file_path: Path,
        manifest_path: Path | None,
    ) -> Export:
        now = _utcnow()
        misalignment_evidence = self._build_export_misalignment_evidence(bundle)
        return Export(
            id=stable_id("export", bundle.chapter.document_id, bundle.chapter.id, export_type.value),
            document_id=bundle.chapter.document_id,
            export_type=export_type,
            input_version_bundle_json={
                "chapter_id": bundle.chapter.id,
                "sentence_count": len(bundle.sentences),
                "target_segment_count": len(bundle.target_segments),
                "issue_count": len(bundle.review_issues),
                "document_parser_version": bundle.document.parser_version,
                "document_segmentation_version": bundle.document.segmentation_version,
                "book_profile_version": bundle.document.active_book_profile_version,
                "chapter_summary_version": bundle.chapter.summary_version,
                "active_snapshot_versions": self._snapshot_version_map(bundle),
                "translation_usage_summary": self._translation_usage_summary(bundle),
                "translation_usage_breakdown": self._translation_usage_breakdown(bundle),
                "translation_usage_timeline": self._translation_usage_timeline(bundle),
                "translation_usage_highlights": self._translation_usage_highlights(bundle),
                "issue_status_summary": self._issue_status_summary(bundle),
                "sidecar_manifest_path": str(manifest_path) if manifest_path is not None else None,
                "export_auto_followup_summary": self._export_auto_followup_summary(bundle),
                "export_time_misalignment_counts": {
                    "missing_target_sentence_count": len(misalignment_evidence.missing_target_sentence_ids),
                    "inactive_only_sentence_count": len(misalignment_evidence.sentence_ids_with_only_inactive_targets),
                    "orphan_target_segment_count": len(misalignment_evidence.orphan_target_segment_ids),
                    "inactive_target_segment_with_edges_count": len(misalignment_evidence.inactive_target_segment_ids_with_edges),
                },
            },
            file_path=str(file_path),
            status=ExportStatus.SUCCEEDED,
            created_at=now,
            updated_at=now,
        )

    def _record_document_export(
        self,
        bundle: DocumentExportBundle,
        export_type: ExportType,
        file_path: Path,
        manifest_path: Path | None,
    ) -> Export:
        now = _utcnow()
        chapter_issue_count = sum(len(chapter.review_issues) for chapter in bundle.chapters)
        visible_chapters = self._visible_merged_chapters(bundle)
        return Export(
            id=stable_id("export", bundle.document.id, export_type.value),
            document_id=bundle.document.id,
            export_type=export_type,
            input_version_bundle_json={
                "chapter_id": None,
                "chapter_count": len(visible_chapters),
                "issue_count": chapter_issue_count,
                "document_parser_version": bundle.document.parser_version,
                "document_segmentation_version": bundle.document.segmentation_version,
                "book_profile_version": bundle.document.active_book_profile_version,
                "translation_usage_summary": self._translation_usage_summary_from_runs(
                    [run for chapter in bundle.chapters for run in chapter.translation_runs]
                ),
                "translation_usage_breakdown": self._translation_usage_breakdown_from_runs(
                    [run for chapter in bundle.chapters for run in chapter.translation_runs]
                ),
                "translation_usage_timeline": self._translation_usage_timeline_from_runs(
                    [run for chapter in bundle.chapters for run in chapter.translation_runs]
                ),
                "translation_usage_highlights": self._translation_usage_highlights_from_runs(
                    [run for chapter in bundle.chapters for run in chapter.translation_runs]
                ),
                "issue_status_summary": self._document_issue_status_summary(bundle),
                "sidecar_manifest_path": str(manifest_path) if manifest_path is not None else None,
                "merged_render_summary": self._merged_render_summary(visible_chapters),
            },
            file_path=str(file_path),
            status=ExportStatus.SUCCEEDED,
            created_at=now,
            updated_at=now,
        )

    def _translation_usage_summary(self, bundle: ChapterExportBundle) -> dict[str, object]:
        return self._translation_usage_summary_from_runs(bundle.translation_runs)

    def _translation_usage_summary_from_runs(self, runs: list[object]) -> dict[str, object]:
        run_count = len(runs)
        succeeded_run_count = sum(1 for run in runs if run.status.value == "succeeded")
        total_token_in = sum(run.token_in or 0 for run in runs)
        total_token_out = sum(run.token_out or 0 for run in runs)
        total_cost_usd = round(sum(float(run.cost_usd or 0) for run in runs), 6)
        latency_values = [run.latency_ms for run in runs if run.latency_ms is not None]
        total_latency_ms = sum(latency_values)
        avg_latency_ms = round(total_latency_ms / len(latency_values), 3) if latency_values else None
        latest_run_at = max(run.created_at for run in runs).isoformat() if runs else None
        return {
            "run_count": run_count,
            "succeeded_run_count": succeeded_run_count,
            "total_token_in": total_token_in,
            "total_token_out": total_token_out,
            "total_cost_usd": total_cost_usd,
            "total_latency_ms": total_latency_ms,
            "avg_latency_ms": avg_latency_ms,
            "latest_run_at": latest_run_at,
        }

    def _translation_usage_breakdown(self, bundle: ChapterExportBundle) -> list[dict[str, object]]:
        return self._translation_usage_breakdown_from_runs(bundle.translation_runs)

    def _translation_usage_breakdown_from_runs(self, runs: list[object]) -> list[dict[str, object]]:
        grouped: dict[tuple[str, str | None, str | None], list] = {}
        for run in runs:
            model_config = run.model_config_json or {}
            key = (
                run.model_name,
                model_config.get("worker"),
                model_config.get("provider"),
            )
            grouped.setdefault(key, []).append(run)

        breakdown: list[dict[str, object]] = []
        for (model_name, worker_name, provider), runs in grouped.items():
            latency_values = [run.latency_ms for run in runs if run.latency_ms is not None]
            total_latency_ms = sum(latency_values)
            avg_latency_ms = round(total_latency_ms / len(latency_values), 3) if latency_values else None
            breakdown.append(
                {
                    "model_name": model_name,
                    "worker_name": worker_name,
                    "provider": provider,
                    "run_count": len(runs),
                    "succeeded_run_count": sum(1 for run in runs if run.status.value == "succeeded"),
                    "total_token_in": sum(run.token_in or 0 for run in runs),
                    "total_token_out": sum(run.token_out or 0 for run in runs),
                    "total_cost_usd": round(sum(float(run.cost_usd or 0) for run in runs), 6),
                    "total_latency_ms": total_latency_ms,
                    "avg_latency_ms": avg_latency_ms,
                    "latest_run_at": max(run.created_at for run in runs).isoformat(),
                }
            )

        breakdown.sort(
            key=lambda entry: (
                -float(entry["total_cost_usd"]),
                -int(entry["run_count"]),
                str(entry["model_name"]),
                str(entry["worker_name"] or ""),
            )
        )
        return breakdown

    def _translation_usage_timeline(self, bundle: ChapterExportBundle) -> list[dict[str, object]]:
        return self._translation_usage_timeline_from_runs(bundle.translation_runs)

    def _translation_usage_timeline_from_runs(self, runs: list[object]) -> list[dict[str, object]]:
        grouped: dict[str, list] = {}
        for run in runs:
            bucket_start = run.created_at.date().isoformat()
            grouped.setdefault(bucket_start, []).append(run)

        timeline: list[dict[str, object]] = []
        for bucket_start, runs in grouped.items():
            latency_values = [run.latency_ms for run in runs if run.latency_ms is not None]
            total_latency_ms = sum(latency_values)
            avg_latency_ms = round(total_latency_ms / len(latency_values), 3) if latency_values else None
            timeline.append(
                {
                    "bucket_start": bucket_start,
                    "bucket_granularity": "day",
                    "run_count": len(runs),
                    "succeeded_run_count": sum(1 for run in runs if run.status.value == "succeeded"),
                    "total_token_in": sum(run.token_in or 0 for run in runs),
                    "total_token_out": sum(run.token_out or 0 for run in runs),
                    "total_cost_usd": round(sum(float(run.cost_usd or 0) for run in runs), 6),
                    "total_latency_ms": total_latency_ms,
                    "avg_latency_ms": avg_latency_ms,
                }
            )

        timeline.sort(key=lambda entry: str(entry["bucket_start"]), reverse=True)
        return timeline

    def _translation_usage_highlights(self, bundle: ChapterExportBundle) -> dict[str, object]:
        return self._translation_usage_highlights_from_runs(bundle.translation_runs)

    def _translation_usage_highlights_from_runs(self, runs: list[object]) -> dict[str, object]:
        breakdown = self._translation_usage_breakdown_from_runs(runs)
        if not breakdown:
            return {
                "top_cost_entry": None,
                "top_latency_entry": None,
                "top_volume_entry": None,
            }

        top_cost_entry = max(
            breakdown,
            key=lambda entry: (
                float(entry["total_cost_usd"]),
                int(entry["run_count"]),
                str(entry["model_name"]),
                str(entry["worker_name"] or ""),
            ),
        )
        top_latency_entry = max(
            breakdown,
            key=lambda entry: (
                float(entry["avg_latency_ms"] or 0.0),
                int(entry["total_latency_ms"]),
                str(entry["model_name"]),
                str(entry["worker_name"] or ""),
            ),
        )
        top_volume_entry = max(
            breakdown,
            key=lambda entry: (
                int(entry["run_count"]),
                int(entry["total_token_out"]),
                str(entry["model_name"]),
                str(entry["worker_name"] or ""),
            ),
        )
        return {
            "top_cost_entry": top_cost_entry,
            "top_latency_entry": top_latency_entry,
            "top_volume_entry": top_volume_entry,
        }

    def _issue_status_summary(self, bundle: ChapterExportBundle) -> dict[str, int]:
        blocking_issue_count = sum(1 for issue in bundle.review_issues if issue.blocking)
        open_issue_count = sum(1 for issue in bundle.review_issues if issue.status == IssueStatus.OPEN)
        resolved_issue_count = sum(1 for issue in bundle.review_issues if issue.status == IssueStatus.RESOLVED)
        return {
            "issue_count": len(bundle.review_issues),
            "open_issue_count": open_issue_count,
            "resolved_issue_count": resolved_issue_count,
            "blocking_issue_count": blocking_issue_count,
        }

    def _document_issue_status_summary(self, bundle: DocumentExportBundle) -> dict[str, int]:
        all_issues = [issue for chapter in bundle.chapters for issue in chapter.review_issues]
        blocking_issue_count = sum(1 for issue in all_issues if issue.blocking)
        open_issue_count = sum(1 for issue in all_issues if issue.status == IssueStatus.OPEN)
        resolved_issue_count = sum(1 for issue in all_issues if issue.status == IssueStatus.RESOLVED)
        return {
            "issue_count": len(all_issues),
            "open_issue_count": open_issue_count,
            "resolved_issue_count": resolved_issue_count,
            "blocking_issue_count": blocking_issue_count,
        }

    def _open_blocking_followup_actions(
        self,
        chapter_id: str,
    ) -> tuple[list[ReviewIssue], list[ExportFollowupAction]]:
        issues = self.repository.list_open_blocking_issues(chapter_id)
        if not issues:
            return [], []
        actions = self.repository.list_planned_issue_actions([issue.id for issue in issues])
        return issues, [
            ExportFollowupAction(
                action_id=action.id,
                issue_id=action.issue_id,
                action_type=action.action_type.value,
                scope_type=action.scope_type.value,
                scope_id=action.scope_id,
            )
            for action in actions
        ]

    def _enforce_gate(self, bundle: ChapterExportBundle, export_type: ExportType) -> None:
        chapter_status = bundle.chapter.status
        if export_type == ExportType.REVIEW_PACKAGE:
            if chapter_status not in {
                ChapterStatus.TRANSLATED,
                ChapterStatus.QA_CHECKED,
                ChapterStatus.REVIEW_REQUIRED,
                ChapterStatus.APPROVED,
                ChapterStatus.EXPORTED,
            }:
                raise ExportGateError(
                    f"Chapter {bundle.chapter.id} is not ready for review export from status {chapter_status.value}."
                )
            self._sync_export_alignment_issues(bundle)
            return

        if export_type in {ExportType.BILINGUAL_HTML, ExportType.MERGED_HTML, ExportType.MERGED_MARKDOWN}:
            if chapter_status not in {ChapterStatus.QA_CHECKED, ChapterStatus.APPROVED, ChapterStatus.EXPORTED}:
                blocking_issues, followup_actions = self._open_blocking_followup_actions(bundle.chapter.id)
                raise ExportGateError(
                    f"Chapter {bundle.chapter.id} must pass review before final export; current status is {chapter_status.value}.",
                    chapter_id=bundle.chapter.id,
                    issue_ids=[issue.id for issue in blocking_issues],
                    followup_actions=followup_actions,
                )
            export_alignment_artifacts = self._sync_export_alignment_issues(bundle)
            if export_alignment_artifacts.issues:
                raise ExportGateError(
                    "Chapter "
                    f"{bundle.chapter.id} has export-time misalignment anomalies and cannot be exported. "
                    f"Review issues created: {', '.join(issue.id for issue in export_alignment_artifacts.issues)}.",
                    chapter_id=bundle.chapter.id,
                    issue_ids=[issue.id for issue in export_alignment_artifacts.issues],
                    followup_actions=[
                        ExportFollowupAction(
                            action_id=action.id,
                            issue_id=action.issue_id,
                            action_type=action.action_type.value,
                            scope_type=action.scope_type.value,
                            scope_id=action.scope_id,
                        )
                        for action in export_alignment_artifacts.actions
                    ],
                )
            if self.repository.has_open_blocking_issues(bundle.chapter.id):
                blocking_issues, followup_actions = self._open_blocking_followup_actions(bundle.chapter.id)
                raise ExportGateError(
                    f"Chapter {bundle.chapter.id} still has open blocking review issues and cannot be exported.",
                    chapter_id=bundle.chapter.id,
                    issue_ids=[issue.id for issue in blocking_issues],
                    followup_actions=followup_actions,
                )
            return

        raise ExportGateError(f"Unsupported export type in P0: {export_type.value}")

    def _apply_status_updates(self, bundle: ChapterExportBundle, export_type: ExportType) -> None:
        now = _utcnow()
        if export_type == ExportType.BILINGUAL_HTML:
            bundle.chapter.status = ChapterStatus.EXPORTED
            bundle.chapter.updated_at = now

            document = self.repository.get_document(bundle.chapter.document_id)
            total_chapters = self.repository.list_document_chapters(bundle.chapter.document_id)
            exported_chapters = [chapter for chapter in total_chapters if chapter.status == ChapterStatus.EXPORTED]
            document.status = (
                DocumentStatus.EXPORTED
                if total_chapters and len(exported_chapters) == len(total_chapters)
                else DocumentStatus.PARTIALLY_EXPORTED
            )
            document.updated_at = now
            self.repository.session.merge(document)

        self.repository.session.merge(bundle.chapter)

    def _apply_document_export_status_updates(self, bundle: DocumentExportBundle, export_type: ExportType) -> None:
        now = _utcnow()
        if export_type not in {ExportType.MERGED_HTML, ExportType.MERGED_MARKDOWN}:
            return
        for chapter_bundle in bundle.chapters:
            chapter_bundle.chapter.status = ChapterStatus.EXPORTED
            chapter_bundle.chapter.updated_at = now
            self.repository.session.merge(chapter_bundle.chapter)
        bundle.document.status = DocumentStatus.EXPORTED
        bundle.document.updated_at = now
        self.repository.session.merge(bundle.document)

    def _build_review_package(self, bundle: ChapterExportBundle) -> dict:
        target_map = self._target_map(bundle)
        sentence_targets = self._sentence_target_map(bundle)
        misalignment_evidence = self._build_export_misalignment_evidence(bundle)
        render_blocks = self._render_blocks_for_chapter(bundle)

        return {
            "chapter_id": bundle.chapter.id,
            "chapter_title": bundle.chapter.title_src,
            "quality_summary": self._quality_summary_payload(bundle),
            "pdf_page_evidence": self._pdf_page_evidence_payload(bundle),
            "pdf_image_evidence": self._pdf_image_evidence_payload(bundle),
            "pdf_preserve_evidence": self._pdf_preserve_evidence_payload(bundle, render_blocks),
            "pdf_page_debug_evidence": self._pdf_page_debug_evidence_payload(bundle, render_blocks),
            "version_evidence": self._version_evidence_payload(bundle),
            "recent_repair_events": self._recent_repair_events_payload(bundle),
            "export_auto_followup_evidence": self._export_auto_followup_evidence_payload(bundle),
            "export_time_misalignment_evidence": self._misalignment_evidence_payload(misalignment_evidence),
            "sentences": [
                {
                    "sentence_id": sentence.id,
                    "source_text": sentence.source_text,
                    "sentence_status": sentence.sentence_status.value,
                    "target_texts": [
                        target_map[target_id].text_zh
                        for target_id in sentence_targets.get(sentence.id, [])
                        if target_id in target_map
                    ],
                }
                for sentence in bundle.sentences
            ],
            "issues": [
                {
                    "issue_id": issue.id,
                    "issue_type": issue.issue_type,
                    "severity": issue.severity.value,
                    "blocking": issue.blocking,
                    "sentence_id": issue.sentence_id,
                    "evidence": issue.evidence_json,
                }
                for issue in bundle.review_issues
            ],
        }

    def _build_bilingual_manifest(self, bundle: ChapterExportBundle, html_path: Path) -> dict:
        target_map = self._target_map(bundle)
        sentence_targets = self._sentence_target_map(bundle)
        misalignment_evidence = self._build_export_misalignment_evidence(bundle)
        render_blocks = self._render_blocks_for_chapter(bundle)
        issue_type_counts: dict[str, int] = {}
        open_issue_count = 0
        render_mode_counts: dict[str, int] = {}
        expected_source_only_count = 0
        for issue in bundle.review_issues:
            issue_type_counts[issue.issue_type] = issue_type_counts.get(issue.issue_type, 0) + 1
            if issue.status.value == "open":
                open_issue_count += 1
        for block in render_blocks:
            render_mode_counts[block.render_mode] = render_mode_counts.get(block.render_mode, 0) + 1
            if block.is_expected_source_only:
                expected_source_only_count += 1

        return {
            "chapter_id": bundle.chapter.id,
            "chapter_title": bundle.chapter.title_src,
            "export_type": ExportType.BILINGUAL_HTML.value,
            "html_path": str(html_path),
            "quality_summary": self._quality_summary_payload(bundle),
            "pdf_page_evidence": self._pdf_page_evidence_payload(bundle),
            "pdf_image_evidence": self._pdf_image_evidence_payload(bundle),
            "pdf_preserve_evidence": self._pdf_preserve_evidence_payload(bundle, render_blocks),
            "version_evidence": self._version_evidence_payload(bundle),
            "recent_repair_events": self._recent_repair_events_payload(bundle),
            "export_auto_followup_evidence": self._export_auto_followup_evidence_payload(bundle),
            "export_time_misalignment_evidence": self._misalignment_evidence_payload(misalignment_evidence),
            "row_summary": {
                "sentence_row_count": len(bundle.sentences),
                "aligned_sentence_count": sum(1 for sentence in bundle.sentences if sentence.id in sentence_targets),
                "target_segment_count": len(target_map),
                "orphan_target_segment_count": len(misalignment_evidence.orphan_target_segment_ids),
                "alignment_edge_count": sum(len(target_ids) for target_ids in sentence_targets.values()),
                "inactive_alignment_target_count": len(misalignment_evidence.inactive_target_segment_ids_with_edges),
            },
            "issue_summary": {
                "total_issue_count": len(bundle.review_issues),
                "open_issue_count": open_issue_count,
                "issue_type_counts": issue_type_counts,
            },
            "render_summary": {
                "render_block_count": len(render_blocks),
                "render_mode_counts": render_mode_counts,
                "expected_source_only_block_count": expected_source_only_count,
            },
        }

    def _snapshot_version_map(self, bundle: ChapterExportBundle) -> dict[str, int]:
        return {
            snapshot.snapshot_type.value: snapshot.version
            for snapshot in bundle.active_snapshots
        }

    def _target_map(self, bundle: ChapterExportBundle) -> dict[str, object]:
        return {
            segment.id: segment
            for segment in bundle.target_segments
            if segment.final_status != TargetSegmentStatus.SUPERSEDED
        }

    def _sentence_target_map(self, bundle: ChapterExportBundle) -> dict[str, list[str]]:
        target_map = self._target_map(bundle)
        sentence_targets: dict[str, list[str]] = {}
        for edge in bundle.alignment_edges:
            if edge.target_segment_id not in target_map:
                continue
            sentence_targets.setdefault(edge.sentence_id, []).append(edge.target_segment_id)
        return sentence_targets

    def _build_export_misalignment_evidence(self, bundle: ChapterExportBundle) -> ExportMisalignmentEvidence:
        active_target_map = self._target_map(bundle)
        rendered_targets_by_sentence = self._sentence_target_map(bundle)
        all_target_ids_by_sentence: dict[str, list[str]] = {}
        inactive_target_segment_ids_with_edges: set[str] = set()
        for edge in bundle.alignment_edges:
            all_target_ids_by_sentence.setdefault(edge.sentence_id, []).append(edge.target_segment_id)
            if edge.target_segment_id not in active_target_map:
                inactive_target_segment_ids_with_edges.add(edge.target_segment_id)

        missing_target_sentence_ids: list[str] = []
        sentence_ids_with_only_inactive_targets: list[str] = []
        for sentence in bundle.sentences:
            if not sentence.translatable or sentence.sentence_status == SentenceStatus.BLOCKED:
                continue
            if sentence.id in rendered_targets_by_sentence:
                continue
            missing_target_sentence_ids.append(sentence.id)
            if sentence.id in all_target_ids_by_sentence:
                sentence_ids_with_only_inactive_targets.append(sentence.id)

        rendered_target_ids = {
            target_id
            for target_ids in rendered_targets_by_sentence.values()
            for target_id in target_ids
        }
        orphan_target_segment_ids = sorted(
            target_segment_id for target_segment_id in active_target_map if target_segment_id not in rendered_target_ids
        )

        return ExportMisalignmentEvidence(
            active_target_map=active_target_map,
            rendered_targets_by_sentence=rendered_targets_by_sentence,
            missing_target_sentence_ids=sorted(missing_target_sentence_ids),
            sentence_ids_with_only_inactive_targets=sorted(sentence_ids_with_only_inactive_targets),
            orphan_target_segment_ids=orphan_target_segment_ids,
            inactive_target_segment_ids_with_edges=sorted(inactive_target_segment_ids_with_edges),
        )

    def _sync_export_alignment_issues(self, bundle: ChapterExportBundle) -> ExportIssueSyncArtifacts:
        now = _utcnow()
        issues = self._build_export_alignment_issues(bundle, now)
        active_issue_ids = {issue.id for issue in issues}

        existing_export_issues = [
            issue
            for issue in bundle.review_issues
            if issue.issue_type == "ALIGNMENT_FAILURE"
            and issue.root_cause_layer == RootCauseLayer.EXPORT
        ]
        for issue in existing_export_issues:
            if issue.id in active_issue_ids:
                continue
            if issue.status in {IssueStatus.OPEN, IssueStatus.TRIAGED}:
                issue.status = IssueStatus.RESOLVED
                issue.resolution_note = "Resolved by latest export-time alignment check."
                issue.updated_at = now
                self.repository.session.merge(issue)

        for issue in issues:
            self.repository.session.merge(issue)
        self.repository.session.flush()

        actions = [self._build_action(issue) for issue in issues]
        for action in actions:
            self.repository.session.merge(action)
        self.repository.session.flush()

        retained_issue_ids = {issue.id for issue in existing_export_issues if issue.status != IssueStatus.RESOLVED}
        bundle.review_issues = [
            issue
            for issue in bundle.review_issues
            if issue.id not in retained_issue_ids
        ] + issues
        return ExportIssueSyncArtifacts(issues=issues, actions=actions)

    def _build_export_alignment_issues(
        self,
        bundle: ChapterExportBundle,
        now: datetime,
    ) -> list[ReviewIssue]:
        evidence = self._build_export_misalignment_evidence(bundle)
        if not evidence.has_anomalies:
            return []

        sentence_to_packet: dict[str, str] = {}
        packet_sentence_ids: dict[str, list[str]] = {}
        for packet in bundle.packets:
            current_sentence_ids = self._packet_current_sentence_ids(packet)
            packet_sentence_ids[packet.id] = current_sentence_ids
            for sentence_id in current_sentence_ids:
                sentence_to_packet[sentence_id] = packet.id

        run_to_packet = {
            run.id: run.packet_id
            for run in bundle.translation_runs
        }
        target_by_id = {segment.id: segment for segment in bundle.target_segments}

        issues_by_packet: dict[str, dict[str, list[str]]] = {}

        def _packet_bucket(packet_id: str) -> dict[str, list[str]]:
            return issues_by_packet.setdefault(
                packet_id,
                {
                    "missing_target_sentence_ids": [],
                    "sentence_ids_with_only_inactive_targets": [],
                    "orphan_target_segment_ids": [],
                    "inactive_target_segment_ids_with_edges": [],
                },
            )

        for sentence_id in evidence.missing_target_sentence_ids:
            packet_id = sentence_to_packet.get(sentence_id)
            if packet_id is None:
                continue
            _packet_bucket(packet_id)["missing_target_sentence_ids"].append(sentence_id)

        for sentence_id in evidence.sentence_ids_with_only_inactive_targets:
            packet_id = sentence_to_packet.get(sentence_id)
            if packet_id is None:
                continue
            _packet_bucket(packet_id)["sentence_ids_with_only_inactive_targets"].append(sentence_id)

        for target_segment_id in evidence.orphan_target_segment_ids:
            target_segment = target_by_id.get(target_segment_id)
            if target_segment is None:
                continue
            packet_id = run_to_packet.get(target_segment.translation_run_id)
            if packet_id is None:
                continue
            _packet_bucket(packet_id)["orphan_target_segment_ids"].append(target_segment_id)

        for target_segment_id in evidence.inactive_target_segment_ids_with_edges:
            target_segment = target_by_id.get(target_segment_id)
            if target_segment is None:
                continue
            packet_id = run_to_packet.get(target_segment.translation_run_id)
            if packet_id is None:
                continue
            _packet_bucket(packet_id)["inactive_target_segment_ids_with_edges"].append(target_segment_id)

        issues: list[ReviewIssue] = []
        for packet in bundle.packets:
            packet_evidence = issues_by_packet.get(packet.id)
            if packet_evidence is None:
                continue
            representative_sentence_id = (
                packet_evidence["missing_target_sentence_ids"][:1]
                or packet_evidence["sentence_ids_with_only_inactive_targets"][:1]
                or packet_sentence_ids.get(packet.id, [])[:1]
            )
            sentence_id = representative_sentence_id[0] if representative_sentence_id else None
            issues.append(
                ReviewIssue(
                    id=stable_id("review-issue", bundle.chapter.document_id, bundle.chapter.id, packet.id, "ALIGNMENT_FAILURE", "export"),
                    document_id=bundle.chapter.document_id,
                    chapter_id=bundle.chapter.id,
                    sentence_id=sentence_id,
                    packet_id=packet.id,
                    issue_type="ALIGNMENT_FAILURE",
                    root_cause_layer=RootCauseLayer.EXPORT,
                    severity=Severity.HIGH,
                    blocking=True,
                    detector=Detector.RULE,
                    confidence=1.0,
                    evidence_json={
                        "reason": "export_time_misalignment",
                        "packet_id": packet.id,
                        **packet_evidence,
                    },
                    status=IssueStatus.OPEN,
                    suggested_action=ActionType.REALIGN_ONLY.value,
                    created_at=now,
                    updated_at=now,
                )
            )
        return issues

    def _packet_current_sentence_ids(self, packet) -> list[str]:
        sentence_ids: list[str] = []
        for block in packet.packet_json.get("current_blocks", []):
            sentence_ids.extend(block.get("sentence_ids", []))
        return sentence_ids

    def _build_action(self, issue: ReviewIssue) -> IssueAction:
        action_type = resolve_action(
            IssueRoutingContext(
                issue_type=issue.issue_type,
                root_cause_layer=issue.root_cause_layer,
                translation_content_ok=True,
            )
        )
        scope_type, scope_id = self._scope_for_action(issue, action_type)
        return IssueAction(
            id=stable_id("issue-action", issue.id, action_type.value),
            issue_id=issue.id,
            action_type=action_type,
            scope_type=scope_type,
            scope_id=scope_id,
            status=ActionStatus.PLANNED,
            reason_json={"issue_type": issue.issue_type, "packet_id": issue.packet_id, "root_cause_layer": issue.root_cause_layer.value},
            created_by=ActionActorType.SYSTEM,
            created_at=issue.created_at,
            updated_at=issue.updated_at,
        )

    def _scope_for_action(self, issue: ReviewIssue, action_type: ActionType) -> tuple[JobScopeType, str | None]:
        if action_type in {ActionType.RERUN_PACKET, ActionType.REBUILD_PACKET_THEN_RERUN, ActionType.REALIGN_ONLY} and issue.packet_id:
            return JobScopeType.PACKET, issue.packet_id
        if action_type == ActionType.REEXPORT_ONLY:
            return JobScopeType.CHAPTER, issue.chapter_id
        return JobScopeType.SENTENCE, issue.sentence_id

    def _misalignment_evidence_payload(self, evidence: ExportMisalignmentEvidence) -> dict:
        return {
            "has_anomalies": evidence.has_anomalies,
            "missing_target_sentence_ids": evidence.missing_target_sentence_ids,
            "sentence_ids_with_only_inactive_targets": evidence.sentence_ids_with_only_inactive_targets,
            "orphan_target_segment_ids": evidence.orphan_target_segment_ids,
            "inactive_target_segment_ids_with_edges": evidence.inactive_target_segment_ids_with_edges,
        }

    def _quality_summary_payload(self, bundle: ChapterExportBundle) -> dict | None:
        if bundle.quality_summary is None:
            return None
        return {
            "issue_count": bundle.quality_summary.issue_count,
            "action_count": bundle.quality_summary.action_count,
            "resolved_issue_count": bundle.quality_summary.resolved_issue_count,
            "coverage_ok": bundle.quality_summary.coverage_ok,
            "alignment_ok": bundle.quality_summary.alignment_ok,
            "term_ok": bundle.quality_summary.term_ok,
            "format_ok": bundle.quality_summary.format_ok,
            "blocking_issue_count": bundle.quality_summary.blocking_issue_count,
            "low_confidence_count": bundle.quality_summary.low_confidence_count,
            "format_pollution_count": bundle.quality_summary.format_pollution_count,
        }

    def _pdf_page_evidence_payload(self, bundle: ChapterExportBundle) -> dict | None:
        metadata = bundle.document.metadata_json or {}
        evidence = metadata.get("pdf_page_evidence")
        if not isinstance(evidence, dict):
            return None

        pages = evidence.get("pdf_pages")
        outline_entries = evidence.get("pdf_outline_entries")
        if not isinstance(pages, list):
            return None
        if not isinstance(outline_entries, list):
            outline_entries = []

        chapter_metadata = bundle.chapter.metadata_json or {}
        page_start = chapter_metadata.get("source_page_start")
        page_end = chapter_metadata.get("source_page_end")
        if not isinstance(page_start, int) or not isinstance(page_end, int):
            return evidence

        page_range = {"start": page_start, "end": page_end}
        filtered_pages = [
            page
            for page in pages
            if isinstance(page, dict)
            and isinstance(page.get("page_number"), int)
            and page_start <= int(page["page_number"]) <= page_end
        ]
        filtered_outline_entries = [
            entry
            for entry in outline_entries
            if isinstance(entry, dict)
            and isinstance(entry.get("page_number"), int)
            and page_start <= int(entry["page_number"]) <= page_end
        ]
        return {
            "schema_version": evidence.get("schema_version"),
            "document_page_count": evidence.get("page_count"),
            "page_count": len(filtered_pages),
            "page_range": page_range,
            "pdf_pages": filtered_pages,
            "pdf_outline_entries": filtered_outline_entries,
        }

    def _pdf_image_evidence_payload(self, bundle: ChapterExportBundle) -> dict:
        block_by_id = {block.id: block for block in bundle.blocks}
        images_payload: list[dict[str, object]] = []
        caption_linked_count = 0
        for image in sorted(bundle.document_images, key=lambda item: (item.page_number, item.id)):
            storage_path = str(image.storage_path or "")
            storage_exists = bool(storage_path) and Path(storage_path).is_file()
            metadata = dict(image.metadata_json or {})
            linked_caption_block_id = metadata.get("linked_caption_block_id")
            linked_caption_block = (
                block_by_id.get(str(linked_caption_block_id))
                if isinstance(linked_caption_block_id, str)
                else None
            )
            linked_caption_text = (
                linked_caption_block.source_text
                if linked_caption_block is not None and linked_caption_block.source_text
                else None
            )
            caption_linked = bool(linked_caption_block_id)
            if caption_linked:
                caption_linked_count += 1
            images_payload.append(
                {
                    "image_id": image.id,
                    "block_id": image.block_id,
                    "page_number": image.page_number,
                    "image_type": image.image_type,
                    "storage_path": storage_path,
                    "storage_exists": storage_exists,
                    "storage_status": (image.metadata_json or {}).get("storage_status"),
                    "width_px": image.width_px,
                    "height_px": image.height_px,
                    "alt_text": image.alt_text,
                    "caption_linked": caption_linked,
                    "linked_caption_block_id": linked_caption_block_id,
                    "linked_caption_text": linked_caption_text,
                    "bbox_json": image.bbox_json,
                    "metadata": metadata,
                }
            )
        return {
            "schema_version": 1,
            "image_count": len(images_payload),
            "caption_linked_count": caption_linked_count,
            "uncaptioned_image_count": len(images_payload) - caption_linked_count,
            "images": images_payload,
        }

    def _pdf_preserve_evidence_payload(
        self,
        bundle: ChapterExportBundle,
        render_blocks: list[MergedRenderBlock] | None = None,
    ) -> dict | None:
        page_evidence = self._pdf_page_evidence_payload(bundle)
        if not isinstance(page_evidence, dict):
            return None

        pages = page_evidence.get("pdf_pages")
        if not isinstance(pages, list):
            return None

        render_blocks = render_blocks if render_blocks is not None else self._render_blocks_for_chapter(bundle)
        chapter_metadata = bundle.chapter.metadata_json or {}
        page_lookup = {
            int(page["page_number"]): page
            for page in pages
            if isinstance(page, dict) and isinstance(page.get("page_number"), int)
        }
        page_contracts: dict[int, dict[str, object]] = {}
        special_section_page_family_counts: dict[str, int] = {}
        preserved_block_ids: set[str] = set()
        source_only_block_ids: set[str] = set()
        preserved_sentence_ids: set[str] = set()

        def ensure_page_contract(page_number: int) -> dict[str, object]:
            contract = page_contracts.get(page_number)
            if contract is not None:
                return contract
            page = page_lookup.get(page_number, {})
            page_family = str(page.get("page_family") or "body")
            contract = {
                "page_number": page_number,
                "page_family": page_family,
                "content_family": str(page.get("content_family") or page_family),
                "family_source": page.get("page_family_source") or page.get("family_source"),
                "backmatter_cue": page.get("backmatter_cue"),
                "backmatter_cue_source": page.get("backmatter_cue_source"),
                "page_layout_risk": str(page.get("page_layout_risk") or "low"),
                "page_layout_reasons": [
                    str(reason)
                    for reason in list(page.get("page_layout_reasons") or [])
                    if isinstance(reason, str)
                ],
                "layout_suspect": bool(page.get("layout_suspect")),
                "layout_signals": [
                    str(signal)
                    for signal in list(page.get("layout_signals") or [])
                    if isinstance(signal, str)
                ],
                "block_count": 0,
                "preserved_block_count": 0,
                "source_only_block_count": 0,
                "render_mode_counts": {},
                "notices": [],
                "block_ids": [],
                "source_sentence_ids": [],
            }
            page_contracts[page_number] = contract
            if page_family in _SPECIAL_PDF_PAGE_FAMILIES:
                special_section_page_family_counts[page_family] = special_section_page_family_counts.get(page_family, 0) + 1
            return contract

        for page_number, page in page_lookup.items():
            if str(page.get("page_family") or "body") in _SPECIAL_PDF_PAGE_FAMILIES:
                ensure_page_contract(page_number)

        for block in render_blocks:
            source_page_start = block.source_metadata.get("source_page_start")
            source_page_end = block.source_metadata.get("source_page_end")
            if not isinstance(source_page_start, int) or not isinstance(source_page_end, int):
                continue
            if block.is_expected_source_only:
                preserved_block_ids.add(block.block_id)
                preserved_sentence_ids.update(block.source_sentence_ids)
            if block.render_mode == "source_artifact_full_width":
                source_only_block_ids.add(block.block_id)
            for page_number in range(source_page_start, source_page_end + 1):
                page = page_lookup.get(page_number, {})
                page_family = str(page.get("page_family") or "body")
                if not (block.is_expected_source_only or page_family in _SPECIAL_PDF_PAGE_FAMILIES):
                    continue
                contract = ensure_page_contract(page_number)
                contract["block_count"] = int(contract["block_count"]) + 1
                if block.is_expected_source_only:
                    contract["preserved_block_count"] = int(contract["preserved_block_count"]) + 1
                if block.render_mode == "source_artifact_full_width":
                    contract["source_only_block_count"] = int(contract["source_only_block_count"]) + 1
                render_mode_counts = dict(contract["render_mode_counts"])
                render_mode_counts[block.render_mode] = render_mode_counts.get(block.render_mode, 0) + 1
                contract["render_mode_counts"] = render_mode_counts
                if block.notice and block.notice not in contract["notices"]:
                    contract["notices"] = [*contract["notices"], block.notice]
                if block.block_id not in contract["block_ids"]:
                    contract["block_ids"] = [*contract["block_ids"], block.block_id]
                for sentence_id in block.source_sentence_ids:
                    if sentence_id not in contract["source_sentence_ids"]:
                        contract["source_sentence_ids"] = [*contract["source_sentence_ids"], sentence_id]

        ordered_page_contracts = sorted(page_contracts.values(), key=lambda item: int(item["page_number"]))
        for contract in ordered_page_contracts:
            contract["preserve_policy"] = self._pdf_page_preserve_policy(contract)

        return {
            "schema_version": 1,
            "chapter_section_family": str(chapter_metadata.get("pdf_section_family") or "body"),
            "page_range": page_evidence.get("page_range"),
            "special_section_page_count": sum(
                1
                for contract in ordered_page_contracts
                if str(contract.get("page_family") or "body") in _SPECIAL_PDF_PAGE_FAMILIES
            ),
            "special_section_page_family_counts": dict(sorted(special_section_page_family_counts.items())),
            "preserved_block_count": len(preserved_block_ids),
            "source_only_block_count": len(source_only_block_ids),
            "preserved_sentence_count": len(preserved_sentence_ids),
            "page_contracts": ordered_page_contracts,
        }

    def _pdf_page_preserve_policy(self, page_contract: dict[str, object]) -> str:
        page_family = str(page_contract.get("page_family") or "body")
        block_count = int(page_contract.get("block_count") or 0)
        preserved_block_count = int(page_contract.get("preserved_block_count") or 0)
        source_only_block_count = int(page_contract.get("source_only_block_count") or 0)
        if block_count > 0 and source_only_block_count == block_count:
            return "source_only"
        if source_only_block_count > 0:
            return "mixed_source_only"
        if preserved_block_count > 0:
            return "preserved_artifacts"
        if page_family == "toc" and block_count == 0:
            return "filtered_noise_only"
        if page_family == "backmatter":
            return "source_only_expected"
        if page_family in _SPECIAL_PDF_PAGE_FAMILIES:
            return "family_only"
        return "none"

    def _pdf_page_debug_evidence_payload(
        self,
        bundle: ChapterExportBundle,
        render_blocks: list[MergedRenderBlock] | None = None,
    ) -> dict | None:
        page_evidence = self._pdf_page_evidence_payload(bundle)
        if not isinstance(page_evidence, dict):
            return None

        pages = page_evidence.get("pdf_pages")
        if not isinstance(pages, list):
            return None

        render_blocks = render_blocks if render_blocks is not None else self._render_blocks_for_chapter(bundle)
        preserve_evidence = self._pdf_preserve_evidence_payload(bundle, render_blocks)
        preserve_contracts = (
            list(preserve_evidence.get("page_contracts") or [])
            if isinstance(preserve_evidence, dict)
            else []
        )
        preserve_by_page = {
            int(contract["page_number"]): contract
            for contract in preserve_contracts
            if isinstance(contract, dict) and isinstance(contract.get("page_number"), int)
        }
        page_lookup = {
            int(page["page_number"]): page
            for page in pages
            if isinstance(page, dict) and isinstance(page.get("page_number"), int)
        }
        render_block_by_id = {block.block_id: block for block in render_blocks}
        sentences_by_block: dict[str, list[object]] = {}
        for sentence in bundle.sentences:
            sentences_by_block.setdefault(sentence.block_id, []).append(sentence)

        interesting_page_numbers: set[int] = set(preserve_by_page)
        for page in pages:
            if not isinstance(page, dict) or not isinstance(page.get("page_number"), int):
                continue
            page_number = int(page["page_number"])
            if bool(page.get("layout_suspect")):
                interesting_page_numbers.add(page_number)
            if str(page.get("page_layout_risk") or "low") != "low":
                interesting_page_numbers.add(page_number)
            if str(page.get("page_family") or "body") in _SPECIAL_PDF_PAGE_FAMILIES:
                interesting_page_numbers.add(page_number)
            if int(page.get("relocated_footnote_count") or 0) > 0:
                interesting_page_numbers.add(page_number)

        page_blocks: dict[int, list[dict[str, object]]] = {page_number: [] for page_number in interesting_page_numbers}
        for block in bundle.blocks:
            source_metadata = dict(block.source_span_json or {})
            source_page_start = source_metadata.get("source_page_start")
            source_page_end = source_metadata.get("source_page_end")
            if not isinstance(source_page_start, int) or not isinstance(source_page_end, int):
                continue
            render_block = render_block_by_id.get(block.id)
            block_sentences = sentences_by_block.get(block.id, [])
            target_segment_count = len(render_block.target_segment_ids) if render_block is not None else 0
            target_excerpt = (
                _excerpt_text(render_block.target_text or "")
                if render_block is not None and render_block.target_text
                else None
            )
            block_payload = {
                "block_id": block.id,
                "ordinal": block.ordinal,
                "block_type": block.block_type.value,
                "pdf_block_role": source_metadata.get("pdf_block_role"),
                "anchor": source_metadata.get("anchor"),
                "reading_order_index": source_metadata.get("reading_order_index"),
                "page_span": {"start": source_page_start, "end": source_page_end},
                "translatable": any(sentence.translatable for sentence in block_sentences),
                "nontranslatable_reason": next(
                    (
                        sentence.nontranslatable_reason
                        for sentence in block_sentences
                        if sentence.nontranslatable_reason
                    ),
                    None,
                ),
                "protected_policy": block.protected_policy.value,
                "render_mode": render_block.render_mode if render_block is not None else None,
                "artifact_kind": render_block.artifact_kind if render_block is not None else None,
                "expected_source_only": bool(render_block.is_expected_source_only) if render_block is not None else False,
                "notice": render_block.notice if render_block is not None else None,
                "sentence_count": len(block_sentences),
                "translatable_sentence_count": sum(1 for sentence in block_sentences if sentence.translatable),
                "target_segment_count": target_segment_count,
                "source_excerpt": _excerpt_text(block.source_text),
                "target_excerpt": target_excerpt,
                "recovery_flags": list(source_metadata.get("recovery_flags") or []),
                "source_bbox_json": source_metadata.get("source_bbox_json"),
            }
            for page_number in range(source_page_start, source_page_end + 1):
                if page_number not in interesting_page_numbers:
                    continue
                page_blocks.setdefault(page_number, []).append(block_payload)

        pages_payload: list[dict[str, object]] = []
        for page_number in sorted(interesting_page_numbers):
            page = page_lookup.get(page_number, {})
            preserve_contract = preserve_by_page.get(page_number)
            debug_reasons: list[str] = []
            if bool(page.get("layout_suspect")):
                debug_reasons.append("layout_suspect")
            if str(page.get("page_layout_risk") or "low") != "low":
                debug_reasons.append("page_layout_risk")
            if str(page.get("page_family") or "body") in _SPECIAL_PDF_PAGE_FAMILIES:
                debug_reasons.append("special_section")
            nested_appendix_subheadings = [
                item
                for item in list(page.get("appendix_nested_subheadings") or [])
                if isinstance(item, dict)
            ]
            if nested_appendix_subheadings:
                debug_reasons.append("nested_appendix_subheading_candidate")
            if page.get("backmatter_cue"):
                debug_reasons.append("backmatter_cue")
            if int(page.get("relocated_footnote_count") or 0) > 0:
                debug_reasons.append("footnote_relocated")
            if preserve_contract is not None:
                debug_reasons.append("preserve_contract")
            pages_payload.append(
                {
                    "page_number": page_number,
                    "page_family": str(page.get("page_family") or "body"),
                    "content_family": page.get("content_family"),
                    "family_source": page.get("page_family_source") or page.get("family_source"),
                    "backmatter_cue": page.get("backmatter_cue"),
                    "backmatter_cue_source": page.get("backmatter_cue_source"),
                    "page_layout_risk": str(page.get("page_layout_risk") or "low"),
                    "page_layout_reasons": [
                        str(reason)
                        for reason in list(page.get("page_layout_reasons") or [])
                        if isinstance(reason, str)
                    ],
                    "layout_suspect": bool(page.get("layout_suspect")),
                    "layout_signals": [
                        str(signal)
                        for signal in list(page.get("layout_signals") or [])
                        if isinstance(signal, str)
                    ],
                    "preserve_policy": (
                        str(preserve_contract.get("preserve_policy"))
                        if isinstance(preserve_contract, dict) and preserve_contract.get("preserve_policy") is not None
                        else None
                    ),
                    "relocated_footnote_count": int(page.get("relocated_footnote_count") or 0),
                    "max_footnote_segment_count": int(page.get("max_footnote_segment_count") or 0),
                    "appendix_nested_subheadings": nested_appendix_subheadings,
                    "debug_reasons": debug_reasons,
                    "blocks": page_blocks.get(page_number, []),
                }
            )

        return {
            "schema_version": 1,
            "page_range": page_evidence.get("page_range"),
            "page_count": len(pages_payload),
            "pages": pages_payload,
        }

    def _version_evidence_payload(self, bundle: ChapterExportBundle) -> dict:
        return {
            "document": {
                "document_id": bundle.document.id,
                "parser_version": bundle.document.parser_version,
                "segmentation_version": bundle.document.segmentation_version,
                "active_book_profile_version": bundle.document.active_book_profile_version,
            },
            "chapter": {
                "chapter_id": bundle.chapter.id,
                "status": bundle.chapter.status.value,
                "summary_version": bundle.chapter.summary_version,
            },
            "book_profile": (
                {
                    "profile_id": bundle.book_profile.id,
                    "version": bundle.book_profile.version,
                }
                if bundle.book_profile is not None
                else None
            ),
            "active_snapshots": [
                {
                    "snapshot_id": snapshot.id,
                    "snapshot_type": snapshot.snapshot_type.value,
                    "scope_type": snapshot.scope_type.value,
                    "scope_id": snapshot.scope_id,
                    "version": snapshot.version,
                }
                for snapshot in sorted(bundle.active_snapshots, key=lambda item: (item.snapshot_type.value, item.version))
            ],
            "packet_context_versions": [
                {
                    "packet_id": packet.id,
                    "packet_type": packet.packet_type.value,
                    "status": packet.status.value,
                    "book_profile_version": packet.book_profile_version,
                    "chapter_brief_version": packet.chapter_brief_version,
                    "termbase_version": packet.termbase_version,
                    "entity_snapshot_version": packet.entity_snapshot_version,
                    "style_snapshot_version": packet.style_snapshot_version,
                }
                for packet in bundle.packets
            ],
        }

    def _recent_repair_events_payload(self, bundle: ChapterExportBundle) -> list[dict]:
        return [
            {
                "audit_id": event.id,
                "object_type": event.object_type,
                "object_id": event.object_id,
                "action": event.action,
                "actor_id": event.actor_id,
                "created_at": event.created_at.isoformat(),
                "payload": event.payload_json,
            }
            for event in self._repair_audits(bundle)
        ]

    def _repair_audits(self, bundle: ChapterExportBundle) -> list[object]:
        repair_actions = {"snapshot.rebuilt", "packet.rebuilt", "packet.realigned"}
        events = [event for event in bundle.audit_events if event.action in repair_actions]
        return events[:20]

    def _export_auto_followup_evidence_payload(self, bundle: ChapterExportBundle) -> dict:
        events = self._export_auto_followup_audits(bundle)
        executed_events = [event for event in events if event.action == "export.auto_followup.executed"]
        stopped_events = [event for event in events if event.action == "export.auto_followup.stopped"]
        return {
            "event_count": len(events),
            "executed_event_count": len(executed_events),
            "stop_event_count": len(stopped_events),
            "events": [
                {
                    "audit_id": event.id,
                    "object_type": event.object_type,
                    "object_id": event.object_id,
                    "action": event.action,
                    "actor_id": event.actor_id,
                    "created_at": event.created_at.isoformat(),
                    "payload": event.payload_json,
                }
                for event in events
            ],
        }

    def _export_auto_followup_summary(self, bundle: ChapterExportBundle) -> dict:
        events = self._export_auto_followup_audits(bundle)
        executed_events = [event for event in events if event.action == "export.auto_followup.executed"]
        stopped_events = [event for event in events if event.action == "export.auto_followup.stopped"]
        latest_event = events[0] if events else None
        latest_stop_event = stopped_events[0] if stopped_events else None
        return {
            "event_count": len(events),
            "executed_event_count": len(executed_events),
            "stop_event_count": len(stopped_events),
            "latest_event_at": latest_event.created_at.isoformat() if latest_event is not None else None,
            "last_stop_reason": (
                latest_stop_event.payload_json.get("stop_reason")
                if latest_stop_event is not None
                else None
            ),
        }

    def _export_auto_followup_audits(self, bundle: ChapterExportBundle) -> list[object]:
        auto_followup_actions = {"export.auto_followup.executed", "export.auto_followup.stopped"}
        events = [event for event in bundle.audit_events if event.action in auto_followup_actions]
        return events[:20]

    def _build_merged_document_html(
        self,
        bundle: DocumentExportBundle,
        asset_path_by_block_id: dict[str, str] | None = None,
    ) -> str:
        visible_chapters = self._visible_merged_chapters(bundle)
        render_summary = self._merged_render_summary(visible_chapters)
        usage_summary = self._translation_usage_summary_from_runs([run for chapter in bundle.chapters for run in chapter.translation_runs])
        toc_items = self._build_merged_toc(visible_chapters)
        chapters_html = "".join(
            self._render_chapter_for_merged_html(chapter_bundle, visible_ordinal, render_blocks, title_text, asset_path_by_block_id)
            for visible_ordinal, chapter_bundle, render_blocks, title_text in visible_chapters
        )
        title = html.escape(bundle.document.title or bundle.document.id)
        author_value = _display_author_value(bundle.document.author)
        author = html.escape(author_value) if author_value is not None else ""
        author_html = f"<div class='meta'>{author}</div>" if author else ""
        chapter_count = render_summary["chapter_count"]
        protected_count = render_summary["expected_source_only_block_count"]
        total_cost = usage_summary.get("total_cost_usd")
        latest_run_at = usage_summary.get("latest_run_at")
        summary_chips = [
            f"<span class='meta-chip'>{chapter_count} chapters</span>",
            f"<span class='meta-chip'>{protected_count} preserved artifacts</span>",
        ]
        if total_cost is not None:
            summary_chips.append(f"<span class='meta-chip'>${float(total_cost):.3f} provider cost</span>")
        if latest_run_at:
            summary_chips.append(f"<span class='meta-chip'>Last run {html.escape(str(latest_run_at)[:10])}</span>")
        return (
            "<html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{title}</title>"
            "<style>"
            ":root{--paper:#f6f1e8;--paper-deep:#efe7d8;--ink:#1f2933;--muted:#586274;--accent:#0f6c7a;--accent-soft:#d9eef0;--border:#d5c7b3;--card:#fffdf8;--artifact:#f5f7fb;--shadow:0 14px 40px rgba(77,57,32,.08);"
            "--font-body:'Iowan Old Style','Palatino Linotype','Book Antiqua',Georgia,serif;--font-display:'Avenir Next Condensed','Gill Sans','Trebuchet MS',sans-serif;--font-ui:'Helvetica Neue','Segoe UI',sans-serif;}"
            "*{box-sizing:border-box;}html{scroll-behavior:smooth;}body{margin:0;color:var(--ink);background:linear-gradient(180deg,var(--paper) 0%,#fbf8f2 35%,#f4efe4 100%);font-family:var(--font-body);line-height:1.75;}"
            "a{color:var(--accent);text-decoration:none;}a:hover{text-decoration:underline;}"
            ".page-shell{display:grid;grid-template-columns:minmax(220px,280px) minmax(0,1fr);gap:32px;max-width:1400px;margin:0 auto;padding:28px 22px 64px;}"
            ".sidebar{position:sticky;top:18px;align-self:start;min-width:0;inline-size:100%;max-inline-size:100%;max-height:calc(100vh - 36px);overflow-x:hidden;overflow-y:auto;overscroll-behavior:contain;scrollbar-gutter:stable;background:rgba(255,253,248,.82);backdrop-filter:blur(10px);border:1px solid rgba(213,199,179,.85);border-radius:22px;padding:22px 18px;box-shadow:var(--shadow);}"
            ".sidebar-kicker,.hero-kicker,.chapter-kicker{font-family:var(--font-ui);letter-spacing:.14em;text-transform:uppercase;font-size:11px;color:var(--accent);font-weight:700;}"
            ".toc-title{margin:10px 0 14px;font-family:var(--font-display);font-size:22px;line-height:1.1;color:#17313a;}"
            ".toc-list{list-style:none;padding:0;margin:0;display:grid;gap:10px;min-width:0;}"
            ".toc-item{min-width:0;}"
            ".toc-item a{display:block;inline-size:100%;max-inline-size:100%;min-width:0;padding:10px 12px;border-radius:12px;color:var(--ink);background:rgba(217,238,240,.45);border:1px solid transparent;font-family:var(--font-ui);font-size:14px;line-height:1.4;overflow:hidden;overflow-wrap:anywhere;word-break:break-word;}"
            ".toc-item a:hover{border-color:#8dc5cb;background:#eff8f8;text-decoration:none;}"
            ".toc-item .toc-ordinal{display:block;font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin-bottom:4px;}"
            ".book-main{min-width:0;}"
            ".hero{padding:34px 38px 28px;border:1px solid rgba(213,199,179,.92);border-radius:28px;background:radial-gradient(circle at top left,rgba(217,238,240,.9),rgba(255,253,248,.94) 38%,rgba(255,253,248,1) 100%);box-shadow:var(--shadow);margin-bottom:26px;}"
            ".hero h1{margin:12px 0 8px;font-family:var(--font-display);font-size:clamp(34px,5vw,62px);line-height:.96;color:#14323a;}"
            ".hero .meta{font-family:var(--font-ui);font-size:16px;color:var(--muted);margin-bottom:16px;}"
            ".hero-summary{max-width:62ch;font-size:18px;color:#314152;}"
            ".meta-row{display:flex;flex-wrap:wrap;gap:10px;margin-top:18px;}"
            ".meta-chip{display:inline-flex;align-items:center;padding:8px 12px;border-radius:999px;background:rgba(255,253,248,.88);border:1px solid rgba(163,143,116,.35);font-family:var(--font-ui);font-size:13px;color:#415160;}"
            ".chapter{margin:0 0 28px;padding:28px 32px 30px;background:var(--card);border:1px solid rgba(213,199,179,.92);border-radius:24px;box-shadow:var(--shadow);}"
            ".chapter-head{margin-bottom:24px;padding-bottom:14px;border-bottom:1px solid rgba(177,157,127,.35);}"
            ".chapter-head h2{margin:10px 0 0;font-family:var(--font-display);font-size:clamp(28px,3vw,40px);line-height:1.02;color:#17313a;}"
            ".chapter-head .source-title{margin-top:8px;color:var(--muted);font-family:var(--font-ui);font-size:14px;}"
            ".block{margin:22px 0;}"
            ".block .zh{font-size:19px;color:#16202a;max-width:36em;text-wrap:pretty;}"
            ".heading .zh{font-family:var(--font-display);font-size:28px;line-height:1.12;color:#17313a;}"
            ".paragraph .zh,.list_item .zh,.quote .zh{hyphens:auto;}"
            ".block details{margin-top:10px;border-top:1px dashed rgba(163,143,116,.45);padding-top:10px;color:var(--muted);}"
            ".block details summary{cursor:pointer;font-size:12px;font-family:var(--font-ui);letter-spacing:.08em;text-transform:uppercase;color:var(--accent);}"
            ".block .source{margin-top:8px;color:var(--muted);font-family:var(--font-ui);font-size:14px;white-space:pre-wrap;}"
            ".artifact{background:linear-gradient(180deg,#ffffff 0%,#fbfcfe 100%);border:1px solid rgba(184,197,218,.85);border-radius:18px;padding:18px 18px 16px;box-shadow:0 8px 22px rgba(60,74,97,.06);}"
            ".artifact .artifact-note{font-size:12px;font-family:var(--font-ui);letter-spacing:.08em;text-transform:uppercase;color:var(--accent);margin-bottom:10px;}"
            ".artifact pre{margin:0;white-space:pre;overflow-x:auto;tab-size:4;font-family:'SFMono-Regular',Menlo,Monaco,monospace;font-size:14px;line-height:1.68;color:#102033;background:#eef3fa;border-radius:12px;padding:14px;}"
            ".artifact .artifact-body{white-space:pre-wrap;font-family:'SFMono-Regular',Menlo,Monaco,monospace;font-size:14px;line-height:1.68;color:#102033;background:#f5f8fc;border-radius:12px;padding:14px;}"
            ".artifact.image-anchor .artifact-body,.artifact.reference .artifact-body{font-family:var(--font-ui);}"
            ".artifact.image-anchor .artifact-body div{margin:4px 0;}"
            ".artifact-figure{margin:0;display:grid;gap:12px;}"
            ".artifact-image{display:block;max-width:100%;height:auto;border-radius:14px;border:1px solid rgba(184,197,218,.9);background:#fff;box-shadow:0 12px 28px rgba(60,74,97,.08);}"
            ".artifact-figure figcaption{font-family:var(--font-ui);font-size:14px;line-height:1.6;color:var(--muted);}"
            ".artifact.reference .artifact-body{word-break:break-all;}"
            ".quote{border-left:5px solid #8cc4ce;padding-left:18px;margin-left:4px;}"
            ".footnote .zh,.caption .zh{font-size:16px;color:#334155;}"
            ".inline-token{font-family:'SFMono-Regular',Menlo,Monaco,monospace;background:#e3edf4;padding:1px 6px;border-radius:6px;font-size:.92em;}"
            ".back-top{display:inline-flex;margin-top:16px;font-family:var(--font-ui);font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--accent);}"
            "@media (max-width: 980px){.page-shell{grid-template-columns:1fr;padding:18px 14px 40px;}.sidebar{position:relative;top:auto;order:-1;}.hero{padding:26px 22px;}.chapter{padding:22px 18px;}.block .zh{font-size:17px;max-width:none;}}"
            "@media print{body{background:#fff;}.page-shell{display:block;max-width:none;padding:0;}.sidebar{display:none;}.hero,.chapter{box-shadow:none;border:1px solid #d7d7d7;break-inside:avoid;}.artifact{box-shadow:none;}}"
            "</style></head><body>"
            "<div class='page-shell'>"
            "<aside class='sidebar'>"
            "<div class='sidebar-kicker'>Reading Map</div>"
            "<div class='toc-title'>Chapters</div>"
            f"{toc_items}"
            "</aside>"
            "<main class='book-main'>"
            "<header class='hero' id='top'>"
            "<div class='hero-kicker'>Merged Reading Edition</div>"
            f"<h1>{title}</h1>"
            f"{author_html}"
            "<div class='hero-summary'>"
            "这是一份面向长时阅读的合并导出稿。正文以中文为主，必要时可展开原文；代码、公式、表格与引用性工件会按可读且可复制的方式保留。"
            "</div>"
            f"<div class='meta-row'>{''.join(summary_chips)}</div>"
            "</header>"
            f"{chapters_html}"
            "</main>"
            "</div>"
            "</body></html>"
        )

    def _build_merged_document_markdown(
        self,
        bundle: DocumentExportBundle,
        asset_path_by_block_id: dict[str, str] | None = None,
    ) -> str:
        visible_chapters = self._visible_merged_chapters(bundle)
        render_summary = self._merged_render_summary(visible_chapters)
        usage_summary = self._translation_usage_summary_from_runs([run for chapter in bundle.chapters for run in chapter.translation_runs])
        title = (bundle.document.title or bundle.document.id or "Merged Reading Edition").strip()
        author = _display_author_value(bundle.document.author)
        chapter_count = render_summary["chapter_count"]
        protected_count = render_summary["expected_source_only_block_count"]
        total_cost = usage_summary.get("total_cost_usd")
        latest_run_at = usage_summary.get("latest_run_at")

        lines: list[str] = [f"# {title}", ""]
        if author:
            lines.extend([f"_Author: {author}_", ""])
        lines.extend(
            [
                "> Merged Reading Edition",
                "> 这是一份面向长时阅读的合并导出稿。正文以中文为主；代码、公式、表格与图片工件按可读且可复制的方式保留。",
                "",
                "## Reading Map",
                "",
            ]
        )
        for visible_ordinal, _chapter_bundle, _render_blocks, title_text in visible_chapters:
            if not title_text:
                continue
            lines.append(f"{visible_ordinal}. {title_text}")
        lines.extend(
            [
                "",
                "## Document Summary",
                "",
                f"- Chapters: {chapter_count}",
                f"- Preserved artifacts: {protected_count}",
            ]
        )
        if total_cost is not None:
            lines.append(f"- Provider cost (USD): {float(total_cost):.3f}")
        if latest_run_at:
            lines.append(f"- Last run: {str(latest_run_at)[:10]}")
        lines.append("")

        for visible_ordinal, chapter_bundle, render_blocks, title_text in visible_chapters:
            lines.extend(
                self._render_chapter_for_merged_markdown(
                    chapter_bundle,
                    visible_ordinal,
                    render_blocks,
                    title_text,
                    asset_path_by_block_id,
                )
            )
        return "\n".join(lines).rstrip() + "\n"

    def _render_chapter_for_merged_markdown(
        self,
        bundle: ChapterExportBundle,
        visible_ordinal: int,
        render_blocks: list[MergedRenderBlock],
        title_text: str | None,
        asset_path_by_block_id: dict[str, str] | None = None,
    ) -> list[str]:
        if not title_text and not render_blocks:
            return []
        title = str(title_text or "").strip()
        lines = [f"## Chapter {visible_ordinal}: {title}" if title else f"## Chapter {visible_ordinal}", ""]
        if title and bundle.chapter.title_src and title != bundle.chapter.title_src:
            lines.extend([f"_Source title: {bundle.chapter.title_src}_", ""])
        for block in render_blocks:
            block_markdown = self._render_block_markdown(block, asset_path_by_block_id)
            if not block_markdown:
                continue
            lines.append(block_markdown)
            lines.append("")
        return lines

    def _render_block_markdown(
        self,
        block: MergedRenderBlock,
        asset_path_by_block_id: dict[str, str] | None = None,
    ) -> str:
        source_text = block.source_text or ""
        target_text = block.target_text or ""
        notice = str(block.notice or "").strip()
        asset_src = str((asset_path_by_block_id or {}).get(block.block_id) or "").strip()
        image_alt_text = str(block.source_metadata.get("image_alt") or block.source_text or "Embedded image").strip()

        if block.render_mode == "source_artifact_full_width":
            if asset_src and block.artifact_kind in {"image", "figure"}:
                parts = [self._markdown_blockquote(notice)] if notice else []
                parts.append(f"![{image_alt_text}]({asset_src})")
                if source_text and source_text not in {"[Image]", ""}:
                    parts.append(f"*{re.sub(r'\\s+', ' ', source_text).strip()}*")
                return "\n\n".join(part for part in parts if part)
            artifact_text = source_text
            if block.artifact_kind == "code":
                artifact_text = self._normalize_markdown_code_artifact_text(source_text)
            parts = [self._markdown_blockquote(notice)] if notice else []
            parts.append(
                self._markdown_fenced_block(
                    artifact_text,
                    language=self._markdown_language_for_artifact(block.artifact_kind, artifact_text),
                )
            )
            return "\n\n".join(part for part in parts if part)

        if block.render_mode == "translated_wrapper_with_preserved_artifact":
            parts = [target_text] if target_text else []
            if notice:
                parts.append(self._markdown_blockquote(notice))
            parts.append(
                self._markdown_fenced_block(
                    source_text,
                    language=self._markdown_language_for_artifact(block.artifact_kind, source_text),
                )
            )
            return "\n\n".join(part for part in parts if part)

        if block.render_mode == "image_anchor_with_translated_caption":
            parts = [target_text] if target_text else []
            if notice:
                parts.append(self._markdown_blockquote(notice))
            if asset_src:
                parts.append(f"![{image_alt_text}]({asset_src})")
            elif source_text:
                parts.append(self._markdown_blockquote(source_text, label="Image caption"))
            if source_text and source_text != target_text:
                parts.append(f"*Source caption: {re.sub(r'\\s+', ' ', source_text).strip()}*")
            return "\n\n".join(part for part in parts if part)

        if block.render_mode == "reference_preserve_with_translated_label":
            parts = [target_text] if target_text and target_text != source_text else []
            if notice:
                parts.append(self._markdown_blockquote(notice))
            parts.append(source_text)
            return "\n\n".join(part for part in parts if part)

        if block.block_type == BlockType.HEADING.value:
            heading_text = target_text or source_text
            return f"### {heading_text}".strip()
        if block.block_type == BlockType.QUOTE.value:
            parts = [self._markdown_blockquote(target_text or source_text)]
        elif block.block_type == BlockType.LIST_ITEM.value:
            list_text = target_text or source_text
            marker = "" if re.match(r"^\s*(?:[-*+]\s+|\d+\.\s+)", list_text) else "- "
            parts = [f"{marker}{list_text}".rstrip()]
        elif block.block_type == BlockType.CAPTION.value:
            parts = [f"*{target_text or source_text}*"]
        else:
            parts = [target_text or source_text]

        if source_text and target_text and source_text != target_text:
            parts.append(self._markdown_blockquote(source_text, label="Source"))
        return "\n\n".join(part for part in parts if part)

    def _normalize_markdown_code_artifact_text(self, text: str) -> str:
        normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        if self._looks_like_code_artifact_text(normalized):
            normalized = self._reflow_code_artifact_text(normalized)
        return normalized.rstrip("\n")

    def _markdown_blockquote(self, text: str, *, label: str | None = None) -> str:
        normalized = (text or "").strip()
        if not normalized and not label:
            return ""
        lines: list[str] = []
        if label:
            lines.append(f"> {label}")
        for line in normalized.splitlines() if normalized else []:
            lines.append(f"> {line}" if line else ">")
        return "\n".join(lines) if lines else f"> {label}"

    def _markdown_fenced_block(self, text: str, *, language: str = "") -> str:
        content = (text or "").rstrip("\n")
        fence = "```"
        while fence in content:
            fence += "`"
        opener = f"{fence}{language}" if language else fence
        return f"{opener}\n{content}\n{fence}"

    def _markdown_language_for_artifact(self, artifact_kind: str | None, text: str) -> str:
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

    def _build_merged_document_manifest(
        self,
        bundle: DocumentExportBundle,
        output_path: Path,
        *,
        export_type: ExportType = ExportType.MERGED_HTML,
    ) -> dict:
        runs = [run for chapter in bundle.chapters for run in chapter.translation_runs]
        visible_chapters = self._visible_merged_chapters(bundle)
        chapter_summaries = []
        render_mode_counts: dict[str, int] = {}
        expected_source_only_count = 0
        for visible_ordinal, chapter_bundle, render_blocks, title_text in visible_chapters:
            for block in render_blocks:
                render_mode_counts[block.render_mode] = render_mode_counts.get(block.render_mode, 0) + 1
                if block.is_expected_source_only:
                    expected_source_only_count += 1
            chapter_summaries.append(
                {
                    "chapter_id": chapter_bundle.chapter.id,
                    "ordinal": visible_ordinal,
                    "source_ordinal": chapter_bundle.chapter.ordinal,
                    "title_src": title_text or chapter_bundle.chapter.title_src,
                    "status": chapter_bundle.chapter.status.value,
                    "block_count": len(chapter_bundle.blocks),
                    "sentence_count": len(chapter_bundle.sentences),
                    "render_block_count": len(render_blocks),
                    "quality_summary": self._quality_summary_payload(chapter_bundle),
                }
            )
        manifest = {
            "document_id": bundle.document.id,
            "title": bundle.document.title,
            "author": _display_author_value(bundle.document.author),
            "export_type": export_type.value,
            "output_path": str(output_path),
            "chapter_count": len(visible_chapters),
            "pdf_image_summary": self._pdf_image_summary_payload(bundle),
            "translation_usage_summary": self._translation_usage_summary_from_runs(runs),
            "translation_usage_breakdown": self._translation_usage_breakdown_from_runs(runs),
            "translation_usage_timeline": self._translation_usage_timeline_from_runs(runs),
            "translation_usage_highlights": self._translation_usage_highlights_from_runs(runs),
            "issue_status_summary": self._document_issue_status_summary(bundle),
            "render_summary": {
                "render_mode_counts": render_mode_counts,
                "expected_source_only_block_count": expected_source_only_count,
            },
            "chapters": chapter_summaries,
        }
        if export_type == ExportType.MERGED_HTML:
            manifest["html_path"] = str(output_path)
        elif export_type == ExportType.MERGED_MARKDOWN:
            manifest["markdown_path"] = str(output_path)
        return manifest

    def _merged_render_summary(
        self,
        visible_chapters: list[tuple[int, ChapterExportBundle, list[MergedRenderBlock], str | None]],
    ) -> dict[str, object]:
        render_mode_counts: dict[str, int] = {}
        expected_source_only_count = 0
        for _visible_ordinal, _chapter_bundle, render_blocks, _title_text in visible_chapters:
            for block in render_blocks:
                render_mode_counts[block.render_mode] = render_mode_counts.get(block.render_mode, 0) + 1
                if block.is_expected_source_only:
                    expected_source_only_count += 1
        return {
            "chapter_count": len(visible_chapters),
            "render_mode_counts": render_mode_counts,
            "expected_source_only_block_count": expected_source_only_count,
        }

    def _pdf_image_summary_payload(self, bundle: DocumentExportBundle) -> dict:
        image_type_counts: dict[str, int] = {}
        chapter_image_counts: dict[str, int] = {}
        image_count = 0
        stored_asset_count = 0
        caption_linked_count = 0
        for chapter_bundle in bundle.chapters:
            chapter_count = len(chapter_bundle.document_images)
            if chapter_count:
                chapter_image_counts[chapter_bundle.chapter.id] = chapter_count
            for image in chapter_bundle.document_images:
                image_count += 1
                image_type_counts[image.image_type] = image_type_counts.get(image.image_type, 0) + 1
                storage_path = str(image.storage_path or "")
                if storage_path and Path(storage_path).is_file():
                    stored_asset_count += 1
                if (image.metadata_json or {}).get("linked_caption_block_id"):
                    caption_linked_count += 1
        return {
            "schema_version": 1,
            "image_count": image_count,
            "stored_asset_count": stored_asset_count,
            "caption_linked_count": caption_linked_count,
            "uncaptioned_image_count": image_count - caption_linked_count,
            "image_type_counts": image_type_counts,
            "chapter_image_counts": chapter_image_counts,
        }

    def _render_chapter_for_merged_html(
        self,
        bundle: ChapterExportBundle,
        visible_ordinal: int,
        render_blocks: list[MergedRenderBlock],
        title_text: str | None,
        asset_path_by_block_id: dict[str, str] | None = None,
    ) -> str:
        blocks_html = "".join(
            self._render_block_html(block, asset_path_by_block_id)
            for block in render_blocks
        )
        if not title_text and not blocks_html:
            return ""
        title = html.escape(title_text) if title_text else ""
        source_title = (
            f"<div class='source-title'>{html.escape(bundle.chapter.title_src)}</div>"
            if title_text and bundle.chapter.title_src and title_text != bundle.chapter.title_src
            else ""
        )
        chapter_head = (
            "<div class='chapter-head'>"
            f"<div class='chapter-kicker'>Chapter {visible_ordinal}</div>"
            f"<h2>{title}</h2>"
            f"{source_title}"
            "</div>"
            if title
            else ""
        )
        return (
            f"<section class='chapter' id='chapter-{html.escape(bundle.chapter.id)}'>"
            f"{chapter_head}"
            f"{blocks_html}"
            "<a class='back-top' href='#top'>Back to top</a>"
            "</section>"
        )

    def _build_merged_toc(
        self,
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

    def _visible_merged_chapters(
        self,
        bundle: DocumentExportBundle,
    ) -> list[tuple[int, ChapterExportBundle, list[MergedRenderBlock], str | None]]:
        visible_candidates: list[tuple[ChapterExportBundle, list[MergedRenderBlock], str | None]] = []
        is_pdf_source = bundle.document.source_type in {SourceType.PDF_TEXT, SourceType.PDF_MIXED, SourceType.PDF_SCAN}
        seen_epub_hrefs: set[str] = set()
        seen_exact_signatures: set[str] = set()
        for chapter_bundle in bundle.chapters:
            render_blocks = self._render_blocks_for_chapter(chapter_bundle)
            title_text = next(
                (block.target_text for block in render_blocks if block.block_type == BlockType.HEADING.value and block.target_text),
                None,
            ) or chapter_bundle.chapter.title_src
            if not title_text and not render_blocks:
                continue

            href = str((chapter_bundle.chapter.metadata_json or {}).get("href") or "").strip()
            normalized_href = href.casefold()
            if not is_pdf_source and normalized_href and normalized_href in seen_epub_hrefs:
                continue

            source_signature = "\n".join(
                _normalize_signature_text(block.source_text)
                for block in render_blocks
                if _normalize_signature_text(block.source_text)
            )
            exact_signature = f"{_normalize_signature_text(chapter_bundle.chapter.title_src or '')}::{source_signature}"
            if source_signature and exact_signature in seen_exact_signatures:
                continue

            if not is_pdf_source and normalized_href:
                seen_epub_hrefs.add(normalized_href)
            if source_signature:
                seen_exact_signatures.add(exact_signature)
            visible_candidates.append((chapter_bundle, render_blocks, title_text))

        if not is_pdf_source:
            return [
                (index + 1, chapter_bundle, render_blocks, title_text)
                for index, (chapter_bundle, render_blocks, title_text) in enumerate(visible_candidates)
            ]

        if _is_academic_paper_document(bundle.document):
            return [
                (index + 1, chapter_bundle, render_blocks, title_text)
                for index, (chapter_bundle, render_blocks, title_text) in enumerate(visible_candidates)
            ]

        grouped: list[dict[str, object]] = []
        next_expected_main_chapter = 1
        main_sequence_started = False
        for chapter_bundle, render_blocks, title_text in visible_candidates:
            source_title = self._source_title_for_chapter(chapter_bundle, title_text)
            main_chapter_number = self._extract_main_chapter_number(source_title)
            starts_appendix = self._looks_like_appendix_title(source_title)
            starts_frontmatter = self._looks_like_frontmatter_title(source_title)

            start_new_group = False
            if main_chapter_number is not None:
                if not main_sequence_started:
                    start_new_group = main_chapter_number == 1 or not grouped
                elif main_chapter_number >= next_expected_main_chapter:
                    start_new_group = True
            elif starts_appendix:
                start_new_group = True
            elif not grouped:
                start_new_group = True
            elif not main_sequence_started and starts_frontmatter:
                start_new_group = True

            if start_new_group:
                grouped.append(
                    {
                        "chapter_bundle": chapter_bundle,
                        "render_blocks": list(render_blocks),
                        "title_text": title_text,
                    }
                )
                if main_chapter_number is not None:
                    main_sequence_started = True
                    next_expected_main_chapter = main_chapter_number + 1
                continue

            if not grouped:
                grouped.append(
                    {
                        "chapter_bundle": chapter_bundle,
                        "render_blocks": list(render_blocks),
                        "title_text": title_text,
                    }
                )
                continue
            grouped[-1]["render_blocks"].extend(render_blocks)

        return [
            (
                index + 1,
                group["chapter_bundle"],
                group["render_blocks"],
                group["title_text"],
            )
            for index, group in enumerate(grouped)
        ]

    def _render_blocks_for_chapter(self, bundle: ChapterExportBundle) -> list[MergedRenderBlock]:
        target_map = self._target_map(bundle)
        sentence_targets = self._sentence_target_map(bundle)
        blocks_by_id = {block.id: block for block in bundle.blocks}
        artifact_group_context_ids = resolve_artifact_group_context_ids(
            bundle.blocks,
            academic_paper=_is_academic_paper_document(bundle.document),
        )
        sentences_by_block: dict[str, list[object]] = {}
        for sentence in sorted(bundle.sentences, key=lambda item: (item.block_id, item.ordinal_in_block)):
            sentences_by_block.setdefault(sentence.block_id, []).append(sentence)
        render_blocks: list[MergedRenderBlock] = []
        skipped_block_ids: set[str] = set()
        for block in bundle.blocks:
            if block.id in skipped_block_ids:
                continue
            block_sentences = sentences_by_block.get(block.id, [])
            target_ids = self._target_ids_for_block_sentences(block_sentences, sentence_targets, target_map)
            source_metadata = dict(block.source_span_json or {})
            if bool(source_metadata.get("repair_hidden_from_export")):
                continue
            if source_metadata.get("pdf_block_role") in {"header", "footer", "toc_entry"}:
                continue
            if block.block_type == BlockType.CAPTION and source_metadata.get("caption_for_block_id"):
                continue
            effective_block_type = self._effective_export_block_type(block, source_metadata)

            render_mode = self._render_mode_for_block(
                block,
                block_sentences,
                source_metadata,
                block_type=effective_block_type,
                source_text=str(source_metadata.get("repair_source_text") or block.source_text or ""),
                document=bundle.document,
            )
            render_source_text = str(source_metadata.get("repair_source_text") or block.source_text or "")
            render_source_sentence_ids = [sentence.id for sentence in block_sentences]
            target_block_type = effective_block_type
            linked_caption_block_id = source_metadata.get("linked_caption_block_id")
            linked_caption_block = (
                blocks_by_id.get(linked_caption_block_id)
                if isinstance(linked_caption_block_id, str)
                else None
            )
            grouped_context_blocks = [
                blocks_by_id[grouped_block_id]
                for grouped_block_id in artifact_group_context_ids.get(block.id, [])
                if grouped_block_id in blocks_by_id
            ]
            if effective_block_type == BlockType.IMAGE and linked_caption_block is not None:
                if linked_caption_block.block_type == BlockType.CAPTION:
                    caption_sentences = sentences_by_block.get(linked_caption_block.id, [])
                    grouped_context_sentences = [
                        sentence
                        for grouped_block in grouped_context_blocks
                        for sentence in sentences_by_block.get(grouped_block.id, [])
                    ]
                    render_mode = "image_anchor_with_translated_caption"
                    render_source_text = linked_caption_block.source_text
                    render_source_sentence_ids = [
                        *render_source_sentence_ids,
                        *[sentence.id for sentence in caption_sentences],
                        *[sentence.id for sentence in grouped_context_sentences],
                    ]
                    target_ids = list(
                        dict.fromkeys(
                            [
                                *self._target_ids_for_block_sentences(
                                    caption_sentences,
                                    sentence_targets,
                                    target_map,
                                ),
                                *self._target_ids_for_block_sentences(
                                    grouped_context_sentences,
                                    sentence_targets,
                                    target_map,
                                ),
                            ]
                        )
                    )
                    target_block_type = None if grouped_context_blocks else linked_caption_block.block_type
                    if grouped_context_blocks:
                        source_metadata["artifact_group_context_block_ids"] = [
                            grouped_block.id for grouped_block in grouped_context_blocks
                        ]
                    skipped_block_ids.add(linked_caption_block.id)
                    skipped_block_ids.update(grouped_block.id for grouped_block in grouped_context_blocks)
            elif effective_block_type in {BlockType.TABLE, BlockType.EQUATION} and linked_caption_block is not None:
                if linked_caption_block.block_type == BlockType.CAPTION:
                    caption_sentences = sentences_by_block.get(linked_caption_block.id, [])
                    grouped_context_sentences = [
                        sentence
                        for grouped_block in grouped_context_blocks
                        for sentence in sentences_by_block.get(grouped_block.id, [])
                    ]
                    render_mode = "translated_wrapper_with_preserved_artifact"
                    render_source_sentence_ids = [
                        *render_source_sentence_ids,
                        *[sentence.id for sentence in caption_sentences],
                        *[sentence.id for sentence in grouped_context_sentences],
                    ]
                    source_metadata["linked_caption_text"] = linked_caption_block.source_text
                    source_metadata["linked_caption_page"] = linked_caption_block.source_span_json.get(
                        "source_page_start"
                    )
                    target_ids = list(
                        dict.fromkeys(
                            [
                                *self._target_ids_for_block_sentences(
                                    caption_sentences,
                                    sentence_targets,
                                    target_map,
                                ),
                                *self._target_ids_for_block_sentences(
                                    grouped_context_sentences,
                                    sentence_targets,
                                    target_map,
                                ),
                            ]
                        )
                    )
                    target_block_type = linked_caption_block.block_type
                    if grouped_context_blocks:
                        source_metadata["artifact_group_context_block_ids"] = [
                            grouped_block.id for grouped_block in grouped_context_blocks
                        ]
                    skipped_block_ids.add(linked_caption_block.id)
                    skipped_block_ids.update(grouped_block.id for grouped_block in grouped_context_blocks)
            elif effective_block_type == BlockType.IMAGE and source_metadata.get("linked_caption_text"):
                render_mode = "image_anchor_with_translated_caption"
                render_source_text = str(source_metadata.get("linked_caption_text") or "")

            target_text = self._join_block_target_text(
                [target_map[target_id].text_zh for target_id in target_ids],
                block_type=target_block_type,
                render_mode=render_mode,
            )
            repair_target_text = str(source_metadata.get("repair_target_text") or "").strip()
            if repair_target_text:
                target_text = repair_target_text
            artifact_kind = self._artifact_kind_for_block(
                block,
                render_mode,
                block_type=effective_block_type,
                source_text=render_source_text,
                source_metadata=source_metadata,
                document=bundle.document,
            )
            render_blocks.append(
                MergedRenderBlock(
                    block_id=block.id,
                    chapter_id=bundle.chapter.id,
                    block_type=effective_block_type.value,
                    render_mode=render_mode,
                    artifact_kind=artifact_kind,
                    title=(bundle.chapter.title_src if effective_block_type == BlockType.HEADING else None),
                    source_text=render_source_text,
                    target_text=target_text or None,
                    source_metadata=source_metadata,
                    source_sentence_ids=list(dict.fromkeys(render_source_sentence_ids)),
                    target_segment_ids=target_ids,
                    is_expected_source_only=render_mode in {
                        "source_artifact_full_width",
                        "translated_wrapper_with_preserved_artifact",
                        "image_anchor_with_translated_caption",
                        "reference_preserve_with_translated_label",
                    },
                    notice=self._source_only_notice(block, artifact_kind, render_mode),
                )
            )
            repair_skip_ids = source_metadata.get("repair_skip_block_ids")
            if isinstance(repair_skip_ids, list):
                skipped_block_ids.update(
                    str(candidate)
                    for candidate in repair_skip_ids
                    if isinstance(candidate, str) and candidate.strip()
                )
            if len(render_blocks) >= 2 and self._should_merge_adjacent_heading_render_blocks(render_blocks[-2], render_blocks[-1]):
                render_blocks[-2] = self._merge_adjacent_heading_render_blocks(render_blocks[-2], render_blocks[-1])
                render_blocks.pop()
                continue
            if len(render_blocks) >= 2 and self._should_merge_adjacent_prose_artifact_continuations(
                render_blocks[-2],
                render_blocks[-1],
            ):
                render_blocks[-2] = self._merge_adjacent_prose_artifact_continuations(
                    render_blocks[-2],
                    render_blocks[-1],
                )
                render_blocks.pop()
                continue
            if len(render_blocks) >= 2 and self._should_merge_adjacent_code_blocks(render_blocks[-2], render_blocks[-1]):
                render_blocks[-2] = self._merge_adjacent_code_render_blocks(render_blocks[-2], render_blocks[-1])
                render_blocks.pop()
        if _is_academic_paper_document(bundle.document):
            return self._normalize_academic_paper_render_blocks(bundle, render_blocks)
        if _is_pdf_document(bundle.document):
            return self._normalize_book_pdf_render_blocks(bundle, render_blocks)
        return render_blocks

    def _target_ids_for_block_sentences(
        self,
        block_sentences: list[object],
        sentence_targets: dict[str, list[str]],
        target_map: dict[str, object],
    ) -> list[str]:
        target_ids: list[str] = []
        seen_target_ids: set[str] = set()
        for sentence in block_sentences:
            for target_id in sentence_targets.get(sentence.id, []):
                if target_id in target_map and target_id not in seen_target_ids:
                    seen_target_ids.add(target_id)
                    target_ids.append(target_id)
        return target_ids

    def _render_mode_for_block(
        self,
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
        if self._is_translatable_prose_artifact_block(
            block,
            source_metadata,
            block_type=effective_block_type,
            source_text=effective_source_text,
            document=document,
        ):
            return "zh_primary_with_optional_source"
        if source_metadata.get("image_src"):
            return "image_anchor_with_translated_caption"
        if effective_block_type == BlockType.CAPTION and self._looks_like_figure_caption(effective_source_text):
            return "image_anchor_with_translated_caption"
        if self._looks_like_reference_literal(effective_source_text):
            return "reference_preserve_with_translated_label"
        if self._is_code_like_block(
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

    def _artifact_kind_for_block(
        self,
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
        if self._is_translatable_prose_artifact_block(
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
        if self._is_code_like_block(
            block,
            effective_source_metadata,
            block_type=effective_block_type,
            source_text=effective_source_text,
            document=document,
        ):
            return "code"
        if effective_block_type == BlockType.CODE:
            if tag in {"math", "svg"}:
                return "equation"
            return "code"
        if effective_block_type == BlockType.TABLE:
            return "table"
        if render_mode == "source_artifact_full_width":
            return "protected_artifact"
        return None

    def _source_title_for_chapter(
        self,
        chapter_bundle: ChapterExportBundle,
        title_text: str | None,
    ) -> str:
        return str(chapter_bundle.chapter.title_src or title_text or "").strip()

    def _extract_main_chapter_number(self, title: str | None) -> int | None:
        if not title:
            return None
        stripped = title.strip()
        match = _MAIN_CHAPTER_TITLE_PATTERN.match(stripped)
        if not match:
            return None
        remainder = stripped[match.end():]
        normalized_remainder = remainder.lstrip(" .:-)\u2013\u2014")
        if normalized_remainder and not normalized_remainder[:1].isupper():
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    def _looks_like_appendix_title(self, title: str | None) -> bool:
        return bool(title and _APPENDIX_TITLE_PATTERN.match(title.strip()))

    def _looks_like_frontmatter_title(self, title: str | None) -> bool:
        normalized = re.sub(r"\s+", " ", (title or "")).strip().casefold()
        return normalized in _FRONTMATTER_TITLES

    def _is_code_like_block(
        self,
        block,
        source_metadata: dict[str, object],
        *,
        block_type: BlockType | None = None,
        source_text: str | None = None,
        document=None,
    ) -> bool:
        effective_block_type = block_type or block.block_type
        effective_source_text = source_text if source_text is not None else block.source_text
        if self._is_translatable_prose_artifact_block(
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
        if academic_paper and self._looks_like_academic_frontmatter_text(effective_source_text):
            return False
        if academic_paper and self._looks_like_academic_prose_text(effective_source_text):
            return False
        if str(source_metadata.get("pdf_block_role") or "").strip().casefold() == "code_like":
            return True
        return self._looks_like_code_artifact_text(effective_source_text, academic_paper=academic_paper)

    def _effective_export_block_type(self, block, source_metadata: dict[str, object]) -> BlockType:
        raw = str(source_metadata.get("repair_block_type") or "").strip().casefold()
        if raw:
            try:
                return BlockType(raw)
            except ValueError:
                pass
        return block.block_type

    def _is_translatable_prose_artifact_block(
        self,
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
        effective_source_text = source_text if source_text is not None else block.source_text
        if not self._looks_like_prose_artifact_text(
            effective_source_text,
            academic_paper=_is_academic_paper_document(document),
        ):
            return False
        role = str(source_metadata.get("pdf_block_role") or "").strip().casefold()
        if effective_block_type == BlockType.PARAGRAPH:
            return role in {"code_like", "table_like"}
        return True

    def _looks_like_prose_artifact_text(self, text: str, *, academic_paper: bool = False) -> bool:
        lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
        if not lines:
            return False
        normalized = " ".join(lines)
        if self._looks_like_reference_literal(normalized):
            return False
        if self._looks_like_figure_caption(normalized):
            return False
        if self._looks_like_code_artifact_text(normalized, academic_paper=academic_paper):
            return False
        strong_codeish_lines = sum(
            1 for line in lines[:24] if self._looks_like_strong_codeish_line_for_artifact_rejection(line)
        )
        codeish_lines = sum(1 for line in lines[:24] if self._looks_like_codeish_line_for_artifact_rejection(line))
        if strong_codeish_lines >= 1 or codeish_lines >= 2:
            return False
        tokens = re.findall(r"[A-Za-z][A-Za-z'-]*", normalized.casefold())
        if len(tokens) < 8:
            return False
        stopword_hits = sum(1 for token in tokens if token in _PROSE_ARTIFACT_STOPWORDS)
        sentence_punctuation = len(re.findall(r"[.!?](?:[\"'\)\]\u201d\u2019])?(?:\s|$)", normalized))
        bullet_lines = sum(
            1
            for line in lines
            if line.startswith(("-", "*", "•")) and len(re.findall(r"[A-Za-z][A-Za-z'-]*", line)) >= 4
        )
        code_token_hits = len(_INLINE_CODE_LIKE_PATTERN.findall(normalized))
        if code_token_hits >= 2 and sentence_punctuation == 0 and bullet_lines == 0:
            return False
        if bullet_lines >= 2 and stopword_hits >= 4:
            return True
        return stopword_hits >= max(4, len(tokens) // 8) and (sentence_punctuation >= 1 or len(lines) >= 2)

    def _looks_like_codeish_line_for_artifact_rejection(self, line: str) -> bool:
        stripped = (line or "").strip()
        if not stripped or stripped.startswith(("•", "-", "*")):
            return False
        if self._looks_like_strong_codeish_line_for_artifact_rejection(stripped):
            return True
        if re.search(r"\b(?:print|await|Runnable|ChatPromptTemplate|SystemMessage|HumanMessage)\s*\(", stripped):
            return True
        if stripped.endswith(("{", "[", "(")) and len(stripped.split()) <= 12:
            return True
        return False

    def _looks_like_strong_codeish_line_for_artifact_rejection(self, line: str) -> bool:
        stripped = (line or "").strip()
        if not stripped or stripped.startswith(("•", "-", "*")):
            return False
        if _CODE_BLOCK_KEYWORD_PATTERN.match(stripped):
            return True
        if stripped.startswith(("#", "@")):
            return True
        if _CODE_ASSIGNMENT_PATTERN.match(stripped) or _OCR_TOLERANT_CODE_ASSIGNMENT_PATTERN.match(stripped):
            return True
        return False

    def _looks_like_prose_continuation_artifact_text(self, text: str) -> bool:
        lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
        if not lines:
            return False
        normalized = " ".join(lines)
        tokens = re.findall(r"[A-Za-z][A-Za-z'-]*", normalized.casefold())
        if len(tokens) < 10:
            return False
        lead = tokens[0]
        if lead not in _PROSE_CONTINUATION_START_WORDS and not normalized[:1].islower():
            return False
        return self._looks_like_prose_artifact_text(text)

    def _normalize_academic_paper_render_blocks(
        self,
        bundle: ChapterExportBundle,
        render_blocks: list[MergedRenderBlock],
    ) -> list[MergedRenderBlock]:
        if bundle.chapter.ordinal != 1:
            return render_blocks
        normalized_blocks: list[MergedRenderBlock] = []
        for block in render_blocks:
            normalized_blocks.extend(self._split_academic_paper_frontmatter_block(block))
        return normalized_blocks

    def _normalize_book_pdf_render_blocks(
        self,
        bundle: ChapterExportBundle,
        render_blocks: list[MergedRenderBlock],
    ) -> list[MergedRenderBlock]:
        normalized: list[MergedRenderBlock] = []
        for index, block in enumerate(render_blocks):
            repaired_block = self._repair_book_pdf_render_block(bundle, index, block)
            if repaired_block is None:
                continue
            if normalized and self._should_merge_adjacent_code_blocks(normalized[-1], repaired_block):
                normalized[-1] = self._merge_adjacent_code_render_blocks(normalized[-1], repaired_block)
                continue
            if normalized and self._should_merge_adjacent_book_paragraph_fragments(normalized[-1], repaired_block):
                normalized[-1] = self._merge_adjacent_book_paragraph_fragments(normalized[-1], repaired_block)
                continue
            normalized.append(repaired_block)
        return normalized

    def _repair_book_pdf_render_block(
        self,
        bundle: ChapterExportBundle,
        index: int,
        block: MergedRenderBlock,
    ) -> MergedRenderBlock | None:
        if block.block_type == BlockType.HEADING.value and self._should_drop_book_heading_label(bundle, index, block):
            return None
        if self._should_clear_suspicious_short_source_target_text(block):
            return self._drop_render_block_target_text(block, flag="export_book_short_source_target_cleared")
        if self._should_promote_book_block_to_code(block):
            return self._replace_render_block_as_code(block, flag="export_book_code_promoted")
        if self._should_demote_book_code_block_to_paragraph(block):
            return self._replace_render_block_as_paragraph(
                block,
                flag="export_book_code_demoted",
                drop_target_text=self._should_drop_demoted_book_code_target_text(block),
            )
        if block.block_type == BlockType.HEADING.value and self._should_demote_book_heading_to_paragraph(bundle, index, block):
            return self._replace_render_block_as_paragraph(block, flag="export_book_heading_demoted")
        return block

    def _should_drop_book_heading_label(
        self,
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

    def _looks_like_book_structural_heading_text(self, text: str | None) -> bool:
        normalized = _normalize_render_text(text)
        if not normalized:
            return False
        lowered = normalized.casefold()
        if lowered in _BOOK_STRUCTURAL_HEADING_TITLES:
            return True
        if _MAIN_CHAPTER_TITLE_PATTERN.match(normalized) or _APPENDIX_TITLE_PATTERN.match(normalized):
            return True
        if _CJK_MAIN_CHAPTER_TITLE_PATTERN.match(normalized) or _CJK_APPENDIX_TITLE_PATTERN.match(normalized):
            return True
        return False

    def _looks_like_short_book_prose_line(self, text: str | None) -> bool:
        normalized = _normalize_render_text(text)
        if not normalized:
            return False
        if self._looks_like_book_structural_heading_text(normalized):
            return False
        if any(token in normalized for token in ("`", "{", "}", "[", "]", "->", "=>", "::")):
            return False
        if _SINGLE_LINE_CODEISH_PATTERN.search(normalized):
            return False
        if _CODE_ASSIGNMENT_PATTERN.match(normalized) or _OCR_TOLERANT_CODE_ASSIGNMENT_PATTERN.match(normalized):
            return False
        alpha_tokens = re.findall(r"[A-Za-z][A-Za-z'-]*", normalized)
        if not 3 <= len(alpha_tokens) <= 10:
            return False
        if not re.search(r"[.!?](?:[\"'\)\]\u201d\u2019])?$", normalized):
            return False
        return True

    def _should_promote_book_block_to_code(self, block: MergedRenderBlock) -> bool:
        if block.render_mode == "source_artifact_full_width" and block.artifact_kind == "code":
            return False
        if block.block_type not in {BlockType.HEADING.value, BlockType.PARAGRAPH.value, BlockType.TABLE.value}:
            return False
        normalized = _normalize_render_text(block.source_text)
        if not normalized or self._looks_like_prose_artifact_text(normalized, academic_paper=False):
            return False
        if self._looks_like_book_structural_heading_text(normalized):
            return False
        if self._looks_like_code_artifact_text(block.source_text or "", academic_paper=False):
            return True
        return self._looks_like_single_line_codeish_text(normalized)

    def _should_demote_book_code_block_to_paragraph(self, block: MergedRenderBlock) -> bool:
        if block.render_mode != "source_artifact_full_width" or block.artifact_kind != "code":
            return False
        normalized = _normalize_render_text(block.source_text)
        if not normalized:
            return False
        if self._looks_like_code_artifact_text(block.source_text or "", academic_paper=False):
            return False
        if self._looks_like_short_book_prose_line(normalized):
            return True
        return self._looks_like_prose_artifact_text(normalized, academic_paper=False)

    def _should_drop_demoted_book_code_target_text(self, block: MergedRenderBlock) -> bool:
        normalized_source = _normalize_render_text(block.source_text)
        normalized_target = _normalize_render_text(block.target_text)
        if not normalized_source or not normalized_target:
            return False
        if not self._looks_like_short_book_prose_line(normalized_source):
            return False
        return len(normalized_target) >= max(80, len(normalized_source) * 4)

    def _should_clear_suspicious_short_source_target_text(self, block: MergedRenderBlock) -> bool:
        if block.block_type not in {BlockType.PARAGRAPH.value, BlockType.QUOTE.value}:
            return False
        return self._should_drop_demoted_book_code_target_text(block)

    def _should_demote_book_heading_to_paragraph(
        self,
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
        if self._looks_like_book_structural_heading_text(normalized):
            return False
        if self._looks_like_code_artifact_text(block.source_text or "", academic_paper=False):
            return False
        if self._looks_like_single_line_codeish_text(normalized):
            return False
        page_family = str(block.source_metadata.get("pdf_page_family") or "body").strip().casefold()
        lowered = normalized.casefold()
        token_count = len(re.findall(r"[A-Za-z][A-Za-z'-]*", normalized))
        if page_family == "references" and lowered not in _BOOK_ALLOWED_REFERENCE_HEADINGS:
            return True
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

    def _replace_render_block_as_paragraph(
        self,
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

    def _drop_render_block_target_text(
        self,
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

    def _replace_render_block_as_code(
        self,
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

    def _split_academic_paper_frontmatter_block(self, block: MergedRenderBlock) -> list[MergedRenderBlock]:
        if block.block_type != BlockType.PARAGRAPH.value:
            return [block]
        split_source = self._split_abstract_sections(block.source_text, markers=("Abstract",))
        if split_source is None:
            return [block]
        split_target = self._split_abstract_sections(block.target_text or "", markers=("摘要", "Abstract"))
        if split_target is None:
            split_target = ("", "", block.target_text or "")
        source_prefix, source_heading, source_body = split_source
        target_prefix, target_heading, target_body = split_target
        author_source = self._normalize_academic_frontmatter_prefix(source_prefix)
        author_target = self._normalize_academic_frontmatter_prefix(target_prefix)
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

    def _split_abstract_sections(
        self,
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

    def _normalize_academic_frontmatter_prefix(self, text: str) -> str:
        normalized = (text or "").strip()
        if not normalized:
            return ""
        normalized = re.sub(r"\s*\n\s*", "\n", normalized)
        normalized = re.sub(r"[ \t]{2,}", " ", normalized)
        return normalized.strip()

    def _looks_like_academic_frontmatter_text(self, text: str) -> bool:
        normalized = re.sub(r"\s+", " ", (text or "")).strip()
        if not normalized:
            return False
        lower = normalized.casefold()
        if "abstract" in lower and _ACADEMIC_FRONTMATTER_MARKER_PATTERN.search(normalized):
            return True
        return "@" in normalized and bool(
            re.search(
                r"\b(?:university|institute|department|school|laboratory|center|centre|society|sciences?)\b",
                lower,
            )
        )

    def _looks_like_academic_prose_text(self, text: str) -> bool:
        lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
        if len(lines) < 2:
            return False
        normalized = " ".join(lines)
        lower = normalized.casefold()
        if self._looks_like_academic_frontmatter_text(normalized):
            return False
        if any(marker in lower for marker in ("def ", "class ", "import ", "from ", "return ", "async def ")):
            return False
        citation_hits = len(_ACADEMIC_CITATION_PATTERN.findall(normalized))
        prose_hits = sum(
            1
            for token in (
                "in this section",
                "in this work",
                "in this context",
                "we propose",
                "we present",
                "we evaluate",
                "we demonstrate",
                "we show",
                "our approach",
                "for example",
                "recent years",
                "dataset",
                "results",
                "baselines",
                "human expert",
                "classifier",
            )
            if token in lower
        )
        sentence_punctuation = len(re.findall(r"[.!?](?:\s|$)", normalized))
        return citation_hits >= 1 or prose_hits >= 2 or sentence_punctuation >= 2

    def _looks_like_figure_caption(self, text: str | None) -> bool:
        normalized = re.sub(r"\s+", " ", (text or "")).strip()
        return bool(normalized and _FIGURE_CAPTION_PATTERN.match(normalized))

    def _block_page_number(self, block: MergedRenderBlock) -> int | None:
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

    def _block_page_end(self, block: MergedRenderBlock) -> int | None:
        source_page_end = block.source_metadata.get("source_page_end")
        if isinstance(source_page_end, int):
            return source_page_end
        return self._block_page_number(block)

    def _block_bbox_regions(self, block: MergedRenderBlock) -> list[dict[str, object]]:
        source_bbox_json = block.source_metadata.get("source_bbox_json")
        if not isinstance(source_bbox_json, dict):
            return []
        regions = source_bbox_json.get("regions")
        if not isinstance(regions, list):
            return []
        return [region for region in regions if isinstance(region, dict)]

    def _should_merge_adjacent_heading_render_blocks(
        self,
        previous: MergedRenderBlock,
        current: MergedRenderBlock,
    ) -> bool:
        if previous.block_type != BlockType.HEADING.value or current.block_type != BlockType.HEADING.value:
            return False
        if previous.render_mode != "zh_primary_with_optional_source" or current.render_mode != "zh_primary_with_optional_source":
            return False
        if previous.chapter_id != current.chapter_id:
            return False
        previous_page = self._block_page_number(previous)
        current_page = self._block_page_number(current)
        if previous_page is None or current_page is None or previous_page != current_page:
            return False
        if self._block_page_end(previous) != previous_page or self._block_page_end(current) != current_page:
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
        if self._looks_like_single_line_codeish_text(current_text):
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
        previous_regions = self._block_bbox_regions(previous)
        current_regions = self._block_bbox_regions(current)
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

    def _merge_adjacent_heading_render_blocks(
        self,
        previous: MergedRenderBlock,
        current: MergedRenderBlock,
    ) -> MergedRenderBlock:
        merged_metadata = dict(previous.source_metadata)
        current_regions = self._block_bbox_regions(current)
        previous_regions = self._block_bbox_regions(previous)
        if previous_regions or current_regions:
            merged_metadata["source_bbox_json"] = {
                "regions": [*previous_regions, *current_regions],
            }
        merged_metadata["source_page_end"] = self._block_page_end(current)
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
            source_text=self._merge_render_text_fragments(previous.source_text, current.source_text),
            target_text=self._merge_render_text_fragments(previous.target_text or "", current.target_text or ""),
            source_metadata=merged_metadata,
            source_sentence_ids=list(dict.fromkeys([*previous.source_sentence_ids, *current.source_sentence_ids])),
            target_segment_ids=list(dict.fromkeys([*previous.target_segment_ids, *current.target_segment_ids])),
        )

    def _merge_render_text_fragments(self, previous_text: str, current_text: str) -> str:
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

    def _merge_adjacent_code_render_blocks(
        self,
        previous: MergedRenderBlock,
        current: MergedRenderBlock,
    ) -> MergedRenderBlock:
        merged_metadata = dict(previous.source_metadata)
        previous_regions = self._block_bbox_regions(previous)
        current_regions = self._block_bbox_regions(current)
        if previous_regions or current_regions:
            merged_metadata["source_bbox_json"] = {"regions": [*previous_regions, *current_regions]}
        merged_metadata["source_page_end"] = self._block_page_end(current)
        merged_metadata["pdf_block_role"] = "code_like"
        merged_metadata["recovery_flags"] = list(
            dict.fromkeys(
                [
                    *list(previous.source_metadata.get("recovery_flags") or []),
                    *list(current.source_metadata.get("recovery_flags") or []),
                    "export_code_blocks_merged",
                ]
            )
        )
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
            source_text="\n".join(
                segment for segment in [previous.source_text.rstrip("\n"), current.source_text.lstrip("\n")] if segment
            ),
            target_text=merged_target,
            source_metadata=merged_metadata,
            source_sentence_ids=list(dict.fromkeys([*previous.source_sentence_ids, *current.source_sentence_ids])),
            target_segment_ids=list(dict.fromkeys([*previous.target_segment_ids, *current.target_segment_ids])),
            notice="代码保持原样",
        )

    def _should_merge_adjacent_code_blocks(self, previous: MergedRenderBlock, current: MergedRenderBlock) -> bool:
        if previous.artifact_kind != "code" or current.artifact_kind != "code":
            return False
        if previous.render_mode != "source_artifact_full_width" or current.render_mode != "source_artifact_full_width":
            return False
        previous_page = self._block_page_number(previous)
        current_page = self._block_page_number(current)
        if previous_page is not None and current_page is not None and current_page - previous_page > 1:
            return False
        return True

    def _should_merge_adjacent_book_paragraph_fragments(
        self,
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
        previous_page = self._block_page_number(previous)
        current_page = self._block_page_number(current)
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

    def _merge_adjacent_book_paragraph_fragments(
        self,
        previous: MergedRenderBlock,
        current: MergedRenderBlock,
    ) -> MergedRenderBlock:
        merged_metadata = dict(previous.source_metadata)
        previous_regions = self._block_bbox_regions(previous)
        current_regions = self._block_bbox_regions(current)
        if previous_regions or current_regions:
            merged_metadata["source_bbox_json"] = {"regions": [*previous_regions, *current_regions]}
        merged_metadata["source_page_end"] = self._block_page_end(current)
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
        merged_target = self._merge_render_text_fragments(previous.target_text or "", current.target_text or "")
        return replace(
            previous,
            source_text=self._merge_render_text_fragments(previous.source_text, current.source_text),
            target_text=merged_target or previous.target_text,
            source_metadata=merged_metadata,
            source_sentence_ids=list(dict.fromkeys([*previous.source_sentence_ids, *current.source_sentence_ids])),
            target_segment_ids=list(dict.fromkeys([*previous.target_segment_ids, *current.target_segment_ids])),
        )

    def _should_merge_adjacent_prose_artifact_continuations(
        self,
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
        if not self._looks_like_prose_continuation_artifact_text(current.source_text):
            return False
        previous_page = self._block_page_number(previous)
        current_page = self._block_page_number(current)
        if previous_page is not None and current_page is not None and current_page - previous_page > 1:
            return False
        previous_index = previous.source_metadata.get("reading_order_index")
        current_index = current.source_metadata.get("reading_order_index")
        if isinstance(previous_index, int) and isinstance(current_index, int) and current_index - previous_index > 1:
            return False
        return True

    def _merge_adjacent_prose_artifact_continuations(
        self,
        previous: MergedRenderBlock,
        current: MergedRenderBlock,
    ) -> MergedRenderBlock:
        merged_metadata = dict(previous.source_metadata)
        previous_regions = self._block_bbox_regions(previous)
        current_regions = self._block_bbox_regions(current)
        if previous_regions or current_regions:
            merged_metadata["source_bbox_json"] = {"regions": [*previous_regions, *current_regions]}
        merged_metadata["source_page_end"] = self._block_page_end(current)
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
        merged_target = self._merge_render_text_fragments(previous.target_text or "", current.target_text or "")
        return replace(
            previous,
            source_text=self._merge_render_text_fragments(previous.source_text, current.source_text),
            target_text=merged_target or previous.target_text,
            source_metadata=merged_metadata,
            source_sentence_ids=list(dict.fromkeys([*previous.source_sentence_ids, *current.source_sentence_ids])),
            target_segment_ids=list(dict.fromkeys([*previous.target_segment_ids, *current.target_segment_ids])),
        )

    def _source_only_notice(self, block, artifact_kind: str | None, render_mode: str) -> str | None:
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

    def _join_block_target_text(
        self,
        target_texts: list[str],
        *,
        block_type: BlockType | None = None,
        render_mode: str | None = None,
    ) -> str:
        cleaned = [text.strip() for text in target_texts if text and text.strip()]
        if not cleaned:
            return ""
        if self._should_inline_join_target_text(block_type, render_mode):
            return self._inline_join_target_text(cleaned)
        return "\n".join(cleaned)

    def _should_inline_join_target_text(
        self,
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

    def _inline_join_target_text(self, target_texts: list[str]) -> str:
        merged = target_texts[0]
        for segment in target_texts[1:]:
            if self._needs_ascii_sentence_gap(merged, segment):
                merged = f"{merged} {segment}"
            else:
                merged = f"{merged}{segment}"
        return merged

    def _needs_ascii_sentence_gap(self, previous: str, current: str) -> bool:
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

    def _render_block_html(
        self,
        block: MergedRenderBlock,
        asset_path_by_block_id: dict[str, str] | None = None,
    ) -> str:
        source_html = self._format_inline_text(block.source_text)
        target_html = self._format_inline_text(block.target_text or "")
        if block.render_mode == "source_artifact_full_width":
            asset_src = ""
            if asset_path_by_block_id is not None:
                asset_src = str(asset_path_by_block_id.get(block.block_id) or "")
            if asset_src and block.artifact_kind in {"image", "figure"}:
                note = f"<div class='artifact-note'>{html.escape(block.notice or '')}</div>" if block.notice else ""
                image_alt_text = str(block.source_metadata.get("image_alt") or "PDF image")
                source_caption = "" if block.source_text in {"", "[Image]"} else source_html
                body = (
                    "<figure class='artifact-figure'>"
                    f"<img class='artifact-image' src='{html.escape(asset_src)}' alt='{html.escape(image_alt_text)}'/>"
                    f"{f'<figcaption>{source_caption}</figcaption>' if source_caption else ''}"
                    "</figure>"
                )
                return (
                    f"<section class='block artifact {html.escape(block.block_type)}'>"
                    f"{note}<div class='artifact-body'>{body}</div></section>"
                )
            body = (
                f"<pre><code>{self._format_preformatted_text(block.source_text)}</code></pre>"
                if block.artifact_kind == "code"
                else f"<div class='artifact-body'>{source_html}</div>"
            )
            note = f"<div class='artifact-note'>{html.escape(block.notice or '')}</div>" if block.notice else ""
            return f"<section class='block artifact {html.escape(block.block_type)}'>{note}{body}</section>"
        if block.render_mode == "translated_wrapper_with_preserved_artifact":
            note = f"<div class='artifact-note'>{html.escape(block.notice or '')}</div>" if block.notice else ""
            translated = f"<div class='zh'>{target_html}</div>" if block.target_text else ""
            body = f"<div class='artifact-body'>{source_html}</div>"
            return (
                f"<section class='block artifact {html.escape(block.block_type)}'>"
                f"{translated}{note}{body}</section>"
            )
        if block.render_mode == "image_anchor_with_translated_caption":
            note = f"<div class='artifact-note'>{html.escape(block.notice or '')}</div>" if block.notice else ""
            translated = f"<div class='zh'>{target_html}</div>" if block.target_text else ""
            asset_src = ""
            if asset_path_by_block_id is not None:
                asset_src = str(asset_path_by_block_id.get(block.block_id) or "")
            image_alt_text = str(block.source_metadata.get("image_alt") or block.source_text or "Embedded image")
            source_caption = source_html if block.source_text else ""
            if asset_src:
                figure_html = (
                    "<figure class='artifact-figure'>"
                    f"<img class='artifact-image' src='{html.escape(asset_src)}' alt='{html.escape(image_alt_text)}'/>"
                    f"{f'<figcaption>{source_caption}</figcaption>' if source_caption else ''}"
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
            return (
                f"<section class='block artifact image-anchor {html.escape(block.block_type)}'>"
                f"{translated}{note}{body}</section>"
            )
        if block.render_mode == "reference_preserve_with_translated_label":
            note = f"<div class='artifact-note'>{html.escape(block.notice or '')}</div>" if block.notice else ""
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
        source_details = (
            f"<details><summary>Source</summary><div class='source'>{source_html}</div></details>"
            if block.source_text and block.target_text and block.source_text != block.target_text
            else ""
        )
        block_class = "quote" if block.block_type == BlockType.QUOTE.value else block.block_type
        return (
            f"<section class='block {html.escape(block_class)}'>"
            f"<div class='zh'>{target_html or source_html}</div>"
            f"{source_details}"
            "</section>"
        )

    def _format_inline_text(self, text: str) -> str:
        escaped = html.escape(text or "")
        return re.sub(
            r"(&lt;/?[A-Za-z][^&]*?&gt;)",
            r"<code class='inline-token'>\1</code>",
            escaped,
        ).replace("\n", "<br/>")

    def _format_preformatted_text(self, text: str) -> str:
        normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        if self._looks_like_code_artifact_text(normalized):
            normalized = self._reflow_code_artifact_text(normalized)
        return html.escape(normalized)

    def _looks_like_code_artifact_text(self, text: str, *, academic_paper: bool = False) -> bool:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) < 2:
            return False
        if academic_paper and (
            self._looks_like_academic_frontmatter_text(text)
            or self._looks_like_academic_prose_text(text)
        ):
            return False
        prose_sentence_lines = sum(
            1
            for line in lines[:24]
            if len(line.split()) >= 6 and re.search(r"[.!?](?:[\"'\)\]\u201d\u2019])?$", line)
        )
        score = 0
        strong_cues = 0
        for line in lines[:24]:
            if _CODE_BLOCK_KEYWORD_PATTERN.match(line):
                score += 3
                strong_cues += 1
                continue
            if line.startswith(("#", "@")):
                score += 2
            if _CODE_ASSIGNMENT_PATTERN.match(line):
                score += 2
                strong_cues += 1
            if any(token in line for token in ("print(", "->", "ChatPromptTemplate", "Runnable", "llm")):
                score += 1
                strong_cues += 1
            if any(token in line for token in ("(", ")", "[", "]", "{", "}")) and any(
                token in line for token in ("=", ":", ",")
            ):
                score += 1
        if prose_sentence_lines >= 2 and strong_cues == 0:
            return False
        if strong_cues == 0 and not any(line[:1] in {" ", "\t"} for line in lines if line.strip()):
            return False
        return score >= 4 and strong_cues >= 1

    def _looks_like_single_line_codeish_text(self, text: str) -> bool:
        normalized = _normalize_render_text(text)
        if not normalized or len(normalized) > 260:
            return False
        if self._looks_like_book_structural_heading_text(normalized):
            return False
        if self._looks_like_short_book_prose_line(normalized):
            return False
        if _SINGLE_LINE_CODEISH_PATTERN.search(normalized):
            return True
        if _CODE_ASSIGNMENT_PATTERN.match(normalized) or _OCR_TOLERANT_CODE_ASSIGNMENT_PATTERN.match(normalized):
            return True
        if any(token in normalized for token in ("(", ")", "{", "}", "[", "]")) and any(
            token in normalized for token in ("=", ":", "->")
        ):
            return len(normalized.split()) <= 28
        return False

    def _reflow_code_artifact_text(self, text: str) -> str:
        raw_lines = [line.expandtabs(4).rstrip() for line in text.split("\n")]
        coalesced_lines = self._coalesce_wrapped_code_lines(raw_lines)
        if any(line[:1] in {" ", "\t"} for line in coalesced_lines if line.strip()):
            return "\n".join(coalesced_lines).strip("\n")

        formatted_lines: list[str] = []
        indent_level = 0
        continuation_depth = 0
        for raw_line in coalesced_lines:
            stripped = raw_line.strip()
            if not stripped:
                if formatted_lines and formatted_lines[-1] != "":
                    formatted_lines.append("")
                continue
            if self._is_dedent_before_code_line(stripped):
                indent_level = max(indent_level - 1, 0)
            if self._is_top_level_code_reset(stripped):
                indent_level = 0
                continuation_depth = 0
            effective_indent = indent_level + min(continuation_depth, 1)
            formatted_lines.append(f"{'    ' * max(effective_indent, 0)}{stripped}")
            continuation_depth = self._continuation_depth_after_code_line(stripped, continuation_depth)
            if self._opens_python_block(stripped):
                indent_level += 1
        return "\n".join(formatted_lines).strip("\n")

    def _coalesce_wrapped_code_lines(self, lines: list[str]) -> list[str]:
        merged: list[str] = []
        for raw_line in lines:
            if not raw_line.strip():
                if merged and merged[-1] != "":
                    merged.append("")
                continue
            if merged and self._should_join_wrapped_code_line(merged[-1], raw_line):
                merged[-1] = self._join_code_line_fragments(merged[-1], raw_line)
                continue
            merged.append(raw_line)
        return merged

    def _should_join_wrapped_code_line(self, previous: str, current: str) -> bool:
        prev = previous.rstrip()
        curr = current.lstrip()
        if not prev or not curr:
            return False
        if self._opens_python_block(prev.strip()):
            return False
        if curr.startswith((")", "]", "}", ".", ",", ";")):
            return True
        if prev.endswith(("\\", "(", "[", "{", ",", "+", "|", "=")):
            return True
        if self._has_unterminated_quote(prev):
            return True
        if self._delimiter_balance(prev) > 0:
            return True
        return False

    def _join_code_line_fragments(self, previous: str, current: str) -> str:
        prev = previous.rstrip()
        curr = current.lstrip()
        if not prev or not curr:
            return prev + curr
        if prev.endswith(("(", "[", "{")) or curr.startswith((")", "]", "}", ".", ",", ";")):
            return f"{prev}{curr}"
        return f"{prev} {curr}"

    def _has_unterminated_quote(self, line: str) -> bool:
        escaped_double = len(re.findall(r'(?<!\\)"', line))
        escaped_single = len(re.findall(r"(?<!\\)'", line))
        return (escaped_double % 2 == 1) or (escaped_single % 2 == 1)

    def _delimiter_balance(self, line: str) -> int:
        return sum(line.count(token) for token in "([{") - sum(line.count(token) for token in ")]}")

    def _is_dedent_before_code_line(self, stripped: str) -> bool:
        lowered = stripped.lower()
        return lowered.startswith(("elif ", "else:", "except", "finally:"))

    def _is_top_level_code_reset(self, stripped: str) -> bool:
        lowered = stripped.lower()
        return (
            lowered.startswith(("async def ", "def ", "class ", "from ", "import ", "@"))
            or stripped.startswith("# ---")
        )

    def _opens_python_block(self, stripped: str) -> bool:
        lowered = stripped.lower()
        if not stripped.endswith(":"):
            return False
        return lowered.startswith(
            ("async def ", "def ", "class ", "if ", "elif ", "else:", "for ", "while ", "try:", "except", "finally:", "with ")
        )

    def _continuation_depth_after_code_line(self, stripped: str, current_depth: int) -> int:
        next_depth = max(0, current_depth + self._delimiter_balance(stripped))
        if stripped.endswith("\\"):
            next_depth = max(next_depth, 1)
        return min(next_depth, 2)

    def _looks_like_reference_literal(self, text: str) -> bool:
        normalized = (text or "").strip()
        if not normalized:
            return False
        patterns = (
            r"^https?://\S+$",
            r"^www\.\S+$",
            r"^(?:/|\.{1,2}/)[^\s]+$",
            r"^[A-Z][A-Z0-9_]{1,}(?:=[^\s]+)?$",
            r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.\-/]+$",
        )
        return any(re.match(pattern, normalized) for pattern in patterns)

    def _build_bilingual_html(
        self,
        bundle: ChapterExportBundle,
        asset_path_by_block_id: dict[str, str] | None = None,
    ) -> str:
        render_blocks = self._render_blocks_for_chapter(bundle)
        chapter_title_target = next(
            (block.target_text for block in render_blocks if block.block_type == BlockType.HEADING.value and block.target_text),
            None,
        )
        title_text = chapter_title_target or bundle.chapter.title_src or bundle.chapter.id
        source_title = (
            f"<div class='source-title'>{html.escape(bundle.chapter.title_src)}</div>"
            if chapter_title_target and bundle.chapter.title_src
            else ""
        )
        blocks_html = "".join(
            self._render_block_html(block, asset_path_by_block_id)
            for block in render_blocks
        )
        if not blocks_html:
            blocks_html = "<div class='empty-state'>No renderable blocks in this chapter.</div>"
        return (
            "<html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{html.escape(str(title_text))}</title>"
            "<style>"
            ":root{--paper:#f5efe6;--card:#fffdfa;--ink:#1f2933;--muted:#5f6b7a;--accent:#0f6c7a;--border:#d9cdbd;--shadow:0 18px 36px rgba(70,54,34,.08);"
            "--font-body:'Iowan Old Style','Palatino Linotype','Book Antiqua',Georgia,serif;--font-display:'Avenir Next Condensed','Gill Sans','Trebuchet MS',sans-serif;--font-ui:'Helvetica Neue','Segoe UI',sans-serif;}"
            "*{box-sizing:border-box;}body{margin:0;background:linear-gradient(180deg,#f7f1e7 0%,#fbf8f2 38%,#f3ede2 100%);color:var(--ink);font-family:var(--font-body);line-height:1.72;}"
            ".page{max-width:980px;margin:0 auto;padding:28px 18px 52px;}"
            ".hero{margin-bottom:22px;padding:28px 30px 24px;background:radial-gradient(circle at top left,rgba(217,238,240,.9),rgba(255,253,248,.95) 38%,rgba(255,253,248,1) 100%);border:1px solid var(--border);border-radius:26px;box-shadow:var(--shadow);}"
            ".hero-kicker{font-family:var(--font-ui);font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);font-weight:700;}"
            ".hero h1{margin:12px 0 0;font-family:var(--font-display);font-size:clamp(30px,5vw,48px);line-height:1;color:#15313a;}"
            ".source-title{margin-top:10px;font-family:var(--font-ui);font-size:14px;color:var(--muted);}"
            ".chapter{padding:28px 30px;background:var(--card);border:1px solid var(--border);border-radius:24px;box-shadow:var(--shadow);}"
            ".block{margin:22px 0;}"
            ".block .zh{font-size:19px;color:#16202a;max-width:36em;text-wrap:pretty;}"
            ".heading .zh{font-family:var(--font-display);font-size:28px;line-height:1.12;color:#17313a;}"
            ".paragraph .zh,.list_item .zh,.quote .zh{hyphens:auto;}"
            ".block details{margin-top:10px;border-top:1px dashed rgba(163,143,116,.45);padding-top:10px;color:var(--muted);}"
            ".block details summary{cursor:pointer;font-size:12px;font-family:var(--font-ui);letter-spacing:.08em;text-transform:uppercase;color:var(--accent);}"
            ".block .source{margin-top:8px;color:var(--muted);font-family:var(--font-ui);font-size:14px;white-space:pre-wrap;}"
            ".artifact{background:linear-gradient(180deg,#ffffff 0%,#fbfcfe 100%);border:1px solid rgba(184,197,218,.85);border-radius:18px;padding:18px 18px 16px;box-shadow:0 8px 22px rgba(60,74,97,.06);}"
            ".artifact .artifact-note{font-size:12px;font-family:var(--font-ui);letter-spacing:.08em;text-transform:uppercase;color:var(--accent);margin-bottom:10px;}"
            ".artifact pre{margin:0;white-space:pre;overflow-x:auto;tab-size:4;font-family:'SFMono-Regular',Menlo,Monaco,monospace;font-size:14px;line-height:1.68;color:#102033;background:#eef3fa;border-radius:12px;padding:14px;}"
            ".artifact .artifact-body{white-space:pre-wrap;font-family:'SFMono-Regular',Menlo,Monaco,monospace;font-size:14px;line-height:1.68;color:#102033;background:#f5f8fc;border-radius:12px;padding:14px;}"
            ".artifact.image-anchor .artifact-body,.artifact.reference .artifact-body{font-family:var(--font-ui);}"
            ".artifact.image-anchor .artifact-body div{margin:4px 0;}"
            ".artifact-figure{margin:0;display:grid;gap:12px;}"
            ".artifact-image{display:block;max-width:100%;height:auto;border-radius:14px;border:1px solid rgba(184,197,218,.9);background:#fff;box-shadow:0 12px 28px rgba(60,74,97,.08);}"
            ".artifact-figure figcaption{font-family:var(--font-ui);font-size:14px;line-height:1.6;color:var(--muted);}"
            ".artifact.reference .artifact-body{word-break:break-all;}"
            ".quote{border-left:5px solid #8cc4ce;padding-left:18px;margin-left:4px;}"
            ".footnote .zh,.caption .zh{font-size:16px;color:#334155;}"
            ".inline-token{font-family:'SFMono-Regular',Menlo,Monaco,monospace;background:#e3edf4;padding:1px 6px;border-radius:6px;font-size:.92em;}"
            ".empty-state{font-family:var(--font-ui);font-size:14px;color:var(--muted);padding:18px 0;}"
            "@media (max-width: 860px){.page{padding:18px 12px 36px;}.hero,.chapter{padding:22px 18px;}.block .zh{font-size:17px;max-width:none;}}"
            "@media print{body{background:#fff;}.page{max-width:none;padding:0;}.hero,.chapter{box-shadow:none;border:1px solid #d7d7d7;}.artifact{box-shadow:none;}}"
            "</style></head><body>"
            "<main class='page'>"
            "<header class='hero'>"
            "<div class='hero-kicker'>Chapter Export</div>"
            f"<h1>{html.escape(str(title_text))}</h1>"
            f"{source_title}"
            "</header>"
            "<section class='chapter'>"
            f"{blocks_html}"
            "</section>"
            "</main>"
            "</body></html>"
        )

    def _export_epub_assets_for_document_bundle(
        self,
        bundle: DocumentExportBundle,
        output_dir: Path,
    ) -> dict[str, str]:
        document_images = [image for chapter_bundle in bundle.chapters for image in chapter_bundle.document_images]
        persisted_assets = self._export_persisted_document_image_assets(
            document_images,
            output_dir,
        )
        render_blocks = [
            block
            for chapter_bundle in bundle.chapters
            for block in self._render_blocks_for_chapter(chapter_bundle)
        ]
        exported_assets = self._export_epub_assets(
            bundle.document.source_type,
            bundle.document.source_path,
            render_blocks,
            output_dir,
            document_images=document_images,
        )
        exported_assets.update(persisted_assets)
        return exported_assets

    def _export_epub_assets_for_chapter_bundle(
        self,
        bundle: ChapterExportBundle,
        output_dir: Path,
    ) -> dict[str, str]:
        persisted_assets = self._export_persisted_document_image_assets(bundle.document_images, output_dir)
        render_blocks = self._render_blocks_for_chapter(bundle)
        exported_assets = self._export_epub_assets(
            bundle.document.source_type,
            bundle.document.source_path,
            render_blocks,
            output_dir,
            document_images=bundle.document_images,
        )
        exported_assets.update(persisted_assets)
        return exported_assets

    def _export_persisted_document_image_assets(
        self,
        document_images: list[object],
        output_dir: Path,
    ) -> dict[str, str]:
        if not document_images:
            return {}

        asset_root = output_dir / "assets" / "document-images"
        asset_root.mkdir(parents=True, exist_ok=True)
        exported: dict[str, str] = {}
        for image in document_images:
            block_id = getattr(image, "block_id", None)
            storage_path = str(getattr(image, "storage_path", "") or "").strip()
            if not block_id or not storage_path:
                continue
            source_path = Path(storage_path)
            if not source_path.is_file():
                continue
            suffix = source_path.suffix or ".bin"
            target_path = asset_root / f"{block_id}{suffix}"
            if not target_path.exists():
                shutil.copy2(source_path, target_path)
            exported[block_id] = PurePosixPath(
                "assets",
                "document-images",
                f"{block_id}{suffix}",
            ).as_posix()
        return exported

    def _export_epub_assets(
        self,
        source_type: SourceType,
        source_path: str | None,
        render_blocks: list[MergedRenderBlock],
        output_dir: Path,
        *,
        document_images: list[object] | None = None,
    ) -> dict[str, str]:
        if not source_path:
            return {}

        if source_type == SourceType.EPUB:
            return self._export_epub_archive_assets(source_path, render_blocks, output_dir)
        if source_type in {SourceType.PDF_TEXT, SourceType.PDF_MIXED, SourceType.PDF_SCAN}:
            return self._export_pdf_assets(
                source_path,
                render_blocks,
                output_dir,
                document_images=document_images,
            )
        return {}

    def _export_epub_archive_assets(
        self,
        source_path: str,
        render_blocks: list[MergedRenderBlock],
        output_dir: Path,
    ) -> dict[str, str]:
        epub_path = Path(source_path)
        if not epub_path.exists():
            return {}

        archive_path_by_block_id: dict[str, str] = {}
        for block in render_blocks:
            archive_path = self._safe_epub_archive_path(block.source_metadata.get("image_path"))
            if archive_path is None:
                continue
            archive_path_by_block_id[block.block_id] = archive_path
        if not archive_path_by_block_id:
            return {}

        asset_root = output_dir / "assets"
        asset_root.mkdir(parents=True, exist_ok=True)
        relative_path_by_archive_path: dict[str, str] = {}
        with zipfile.ZipFile(epub_path) as archive:
            for archive_path in sorted(set(archive_path_by_block_id.values())):
                try:
                    archive_info = archive.getinfo(archive_path)
                except KeyError:
                    continue
                relative_path = PurePosixPath("assets").joinpath(PurePosixPath(archive_path))
                target_path = asset_root.joinpath(*PurePosixPath(archive_path).parts)
                target_path.parent.mkdir(parents=True, exist_ok=True)
                if not target_path.exists():
                    with archive.open(archive_info) as source_handle, target_path.open("wb") as target_handle:
                        shutil.copyfileobj(source_handle, target_handle)
                relative_path_by_archive_path[archive_path] = relative_path.as_posix()

        return {
            block_id: relative_path_by_archive_path[archive_path]
            for block_id, archive_path in archive_path_by_block_id.items()
            if archive_path in relative_path_by_archive_path
        }

    def _export_pdf_assets(
        self,
        source_path: str,
        render_blocks: list[MergedRenderBlock],
        output_dir: Path,
        *,
        document_images: list[object] | None = None,
    ) -> dict[str, str]:
        pdf_path = Path(source_path)
        if not pdf_path.exists():
            return {}

        document_image_by_block_id = {
            str(getattr(image, "block_id", "")): image
            for image in (document_images or [])
            if getattr(image, "block_id", None)
        }
        pdf_image_specs: dict[str, tuple[str, int, list[float]]] = {}
        for block in render_blocks:
            if block.artifact_kind not in {"image", "figure"}:
                continue
            persisted_image = document_image_by_block_id.get(block.block_id)
            if persisted_image is not None:
                storage_path = Path(str(getattr(persisted_image, "storage_path", "") or "").strip())
                if storage_path.is_file():
                    continue
            crop_spec = self._pdf_asset_crop_spec(block)
            if crop_spec is None:
                continue
            pdf_image_specs[block.block_id] = crop_spec

        if not pdf_image_specs:
            return {}

        try:
            import fitz
        except ImportError:
            return {}

        asset_root = output_dir / "assets" / "pdf-images"
        asset_root.mkdir(parents=True, exist_ok=True)
        relative_path_by_block_id: dict[str, str] = {}
        document = fitz.open(str(pdf_path))
        try:
            for block_id, (spec_kind, page_number, bbox) in pdf_image_specs.items():
                if page_number < 1 or page_number > document.page_count:
                    continue
                page = document.load_page(page_number - 1)
                resolved_bbox = (
                    self._caption_anchored_pdf_crop_bbox(page, bbox)
                    if spec_kind == "caption_anchor"
                    else self._layout_guided_pdf_crop_bbox(page, bbox)
                )
                if resolved_bbox is None:
                    continue
                rect = fitz.Rect(*resolved_bbox)
                if rect.width <= 1 or rect.height <= 1:
                    continue
                target_path = asset_root / f"{block_id}.png"
                persisted_image = document_image_by_block_id.get(block_id)
                if persisted_image is not None:
                    materialized_path = self._materialized_document_image_path(persisted_image)
                    if not materialized_path.exists():
                        self._save_pdf_crop(page, rect, materialized_path)
                    if not target_path.exists():
                        shutil.copy2(materialized_path, target_path)
                    self._mark_document_image_materialized(persisted_image, materialized_path)
                elif not target_path.exists():
                    self._save_pdf_crop(page, rect, target_path)
                relative_path_by_block_id[block_id] = PurePosixPath(
                    "assets",
                    "pdf-images",
                    f"{block_id}.png",
                ).as_posix()
        finally:
            document.close()

        return relative_path_by_block_id

    def _pdf_asset_crop_spec(self, block: MergedRenderBlock) -> tuple[str, int, list[float]] | None:
        source_bbox_json = block.source_metadata.get("source_bbox_json")
        if not isinstance(source_bbox_json, dict):
            return None
        regions = source_bbox_json.get("regions")
        if not isinstance(regions, list) or not regions or not isinstance(regions[0], dict):
            return None
        first_region = regions[0]
        page_number = first_region.get("page_number")
        bbox = first_region.get("bbox")
        if not isinstance(page_number, int) or not isinstance(bbox, list) or len(bbox) != 4:
            return None
        try:
            bbox_values = [float(value) for value in bbox]
        except (TypeError, ValueError):
            return None
        if bbox_values[2] <= bbox_values[0] or bbox_values[3] <= bbox_values[1]:
            return None
        if block.block_type == BlockType.CAPTION.value and block.render_mode == "image_anchor_with_translated_caption":
            return ("caption_anchor", page_number, bbox_values)
        return ("direct", page_number, bbox_values)

    def _caption_anchored_pdf_crop_bbox(self, page: object, caption_bbox: list[float]) -> list[float] | None:
        page_rect = getattr(page, "rect", None)
        if page_rect is None:
            return None
        page_x0 = float(getattr(page_rect, "x0", 0.0))
        page_y0 = float(getattr(page_rect, "y0", 0.0))
        page_x1 = float(getattr(page_rect, "x1", 0.0))
        page_y1 = float(getattr(page_rect, "y1", 0.0))
        if page_x1 <= page_x0 or page_y1 <= page_y0:
            return None
        fallback_bbox = self._default_caption_anchored_pdf_crop_bbox(
            page_x0=page_x0,
            page_y0=page_y0,
            page_x1=page_x1,
            page_y1=page_y1,
            caption_bbox=caption_bbox,
        )
        if fallback_bbox is None:
            return None
        layout_blocks = self._page_layout_blocks(page)
        if not layout_blocks:
            return fallback_bbox
        image_bbox = self._best_caption_aligned_image_bbox(
            layout_blocks=layout_blocks,
            caption_bbox=caption_bbox,
            page_bounds=[page_x0, page_y0, page_x1, page_y1],
        )
        if image_bbox is not None:
            return image_bbox
        return self._trim_caption_crop_bbox_with_text_blocks(
            fallback_bbox=fallback_bbox,
            layout_blocks=layout_blocks,
            caption_bbox=caption_bbox,
            page_bounds=[page_x0, page_y0, page_x1, page_y1],
        )

    def _layout_guided_pdf_crop_bbox(self, page: object, seed_bbox: list[float]) -> list[float] | None:
        page_rect = getattr(page, "rect", None)
        if page_rect is None:
            return seed_bbox
        page_bounds = [
            float(getattr(page_rect, "x0", 0.0)),
            float(getattr(page_rect, "y0", 0.0)),
            float(getattr(page_rect, "x1", 0.0)),
            float(getattr(page_rect, "y1", 0.0)),
        ]
        if page_bounds[2] <= page_bounds[0] or page_bounds[3] <= page_bounds[1]:
            return seed_bbox
        layout_blocks = self._page_layout_blocks(page)
        if not layout_blocks:
            return seed_bbox
        image_bbox = self._best_seed_aligned_image_bbox(
            layout_blocks=layout_blocks,
            seed_bbox=seed_bbox,
            page_bounds=page_bounds,
        )
        if image_bbox is not None:
            return image_bbox
        return self._trim_direct_crop_bbox_with_text_blocks(
            seed_bbox=seed_bbox,
            layout_blocks=layout_blocks,
            page_bounds=page_bounds,
        )

    def _default_caption_anchored_pdf_crop_bbox(
        self,
        *,
        page_x0: float,
        page_y0: float,
        page_x1: float,
        page_y1: float,
        caption_bbox: list[float],
    ) -> list[float] | None:
        caption_x0, caption_y0, caption_x1, _caption_y1 = caption_bbox
        bottom = max(page_y0 + 48.0, caption_y0 - 10.0)
        top = max(page_y0 + 36.0, bottom - min(420.0, max(180.0, (page_y1 - page_y0) * 0.48)))
        left = max(page_x0 + 36.0, caption_x0 - 72.0)
        right = min(page_x1 - 36.0, caption_x1 + 72.0)
        if right - left < 80.0:
            left = page_x0 + 36.0
            right = page_x1 - 36.0
        if bottom - top < 80.0:
            return None
        return [left, top, right, bottom]

    def _page_layout_blocks(self, page: object) -> list[_PdfPageLayoutBlock]:
        getter = getattr(page, "get_text", None)
        if getter is None:
            return []
        try:
            raw_blocks = getter("blocks")
        except Exception:
            return []
        parsed: list[_PdfPageLayoutBlock] = []
        for raw in raw_blocks or []:
            if not isinstance(raw, (list, tuple)) or len(raw) < 4:
                continue
            try:
                bbox = [float(raw[index]) for index in range(4)]
            except (TypeError, ValueError):
                continue
            if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                continue
            text = str(raw[4]).strip() if len(raw) >= 5 and isinstance(raw[4], str) else ""
            block_type = next((value for value in reversed(raw[5:]) if isinstance(value, int)), None)
            parsed.append(_PdfPageLayoutBlock(bbox=bbox, text=text, block_type=block_type))
        return parsed

    def _best_caption_aligned_image_bbox(
        self,
        *,
        layout_blocks: list[_PdfPageLayoutBlock],
        caption_bbox: list[float],
        page_bounds: list[float],
    ) -> list[float] | None:
        caption_x0, caption_y0, caption_x1, _caption_y1 = caption_bbox
        corridor = [
            max(page_bounds[0] + 24.0, caption_x0 - 120.0),
            page_bounds[1],
            min(page_bounds[2] - 24.0, caption_x1 + 120.0),
            caption_y0,
        ]
        best_bbox: list[float] | None = None
        best_score: tuple[float, float, float, float] | None = None
        for block in layout_blocks:
            if not self._looks_like_page_image_block(block):
                continue
            if block.bbox[3] > caption_y0 + 8.0:
                continue
            gap = max(0.0, caption_y0 - block.bbox[3])
            if gap > 120.0:
                continue
            overlap = self._bbox_horizontal_overlap_ratio(block.bbox, corridor)
            center_distance = abs(((block.bbox[0] + block.bbox[2]) / 2.0) - ((caption_x0 + caption_x1) / 2.0))
            if overlap < 0.18 and center_distance > 220.0:
                continue
            area = max(1.0, (block.bbox[2] - block.bbox[0]) * (block.bbox[3] - block.bbox[1]))
            score = (
                gap,
                -overlap,
                center_distance,
                -area,
            )
            if best_score is None or score < best_score:
                best_score = score
                best_bbox = [
                    max(page_bounds[0] + 12.0, block.bbox[0] - 12.0),
                    max(page_bounds[1] + 12.0, block.bbox[1] - 12.0),
                    min(page_bounds[2] - 12.0, block.bbox[2] + 12.0),
                    min(caption_y0 - 6.0, block.bbox[3] + 12.0),
                ]
        if best_bbox is None or best_bbox[2] - best_bbox[0] < 80.0 or best_bbox[3] - best_bbox[1] < 80.0:
            return None
        return best_bbox

    def _trim_caption_crop_bbox_with_text_blocks(
        self,
        *,
        fallback_bbox: list[float],
        layout_blocks: list[_PdfPageLayoutBlock],
        caption_bbox: list[float],
        page_bounds: list[float],
    ) -> list[float] | None:
        left, top, right, bottom = fallback_bbox
        caption_y0 = float(caption_bbox[1])
        interfering_text_blocks = [
            block
            for block in layout_blocks
            if self._looks_like_page_text_block(block)
            and block.bbox[3] <= caption_y0 + 2.0
            and self._bbox_horizontal_overlap_ratio(block.bbox, [left, top, right, bottom]) >= 0.35
            and block.bbox[1] < bottom
        ]
        if interfering_text_blocks:
            trimmed_top = max(block.bbox[3] for block in interfering_text_blocks) + 12.0
            top = max(top, trimmed_top)
        top = max(page_bounds[1] + 36.0, min(top, caption_y0 - 96.0))
        if bottom - top < 80.0:
            return fallback_bbox
        return [left, top, right, bottom]

    def _best_seed_aligned_image_bbox(
        self,
        *,
        layout_blocks: list[_PdfPageLayoutBlock],
        seed_bbox: list[float],
        page_bounds: list[float],
    ) -> list[float] | None:
        seed_center_x = (seed_bbox[0] + seed_bbox[2]) / 2.0
        seed_center_y = (seed_bbox[1] + seed_bbox[3]) / 2.0
        candidates: list[_PdfPageLayoutBlock] = []
        for block in layout_blocks:
            if not self._looks_like_page_image_block(block):
                continue
            overlap = self._bbox_overlap_area(block.bbox, seed_bbox)
            horizontal = self._bbox_horizontal_overlap_ratio(block.bbox, seed_bbox)
            center_distance = abs(((block.bbox[0] + block.bbox[2]) / 2.0) - seed_center_x) + abs(
                ((block.bbox[1] + block.bbox[3]) / 2.0) - seed_center_y
            )
            if overlap <= 0.0 and horizontal < 0.28 and center_distance > 180.0:
                continue
            candidates.append(block)
        if not candidates:
            return None
        primary = max(
            candidates,
            key=lambda block: (
                self._bbox_overlap_area(block.bbox, seed_bbox),
                self._bbox_horizontal_overlap_ratio(block.bbox, seed_bbox),
                -abs(((block.bbox[0] + block.bbox[2]) / 2.0) - seed_center_x),
            ),
        )
        merged_bbox = list(primary.bbox)
        for block in candidates:
            if block is primary:
                continue
            if self._bbox_overlap_area(block.bbox, merged_bbox) > 0.0 or self._bbox_horizontal_overlap_ratio(
                block.bbox,
                merged_bbox,
            ) >= 0.45:
                merged_bbox = [
                    min(merged_bbox[0], block.bbox[0]),
                    min(merged_bbox[1], block.bbox[1]),
                    max(merged_bbox[2], block.bbox[2]),
                    max(merged_bbox[3], block.bbox[3]),
                ]
        padded = [
            max(page_bounds[0] + 10.0, merged_bbox[0] - 10.0),
            max(page_bounds[1] + 10.0, merged_bbox[1] - 10.0),
            min(page_bounds[2] - 10.0, merged_bbox[2] + 10.0),
            min(page_bounds[3] - 10.0, merged_bbox[3] + 10.0),
        ]
        if padded[2] - padded[0] < 72.0 or padded[3] - padded[1] < 72.0:
            return None
        return padded

    def _trim_direct_crop_bbox_with_text_blocks(
        self,
        *,
        seed_bbox: list[float],
        layout_blocks: list[_PdfPageLayoutBlock],
        page_bounds: list[float],
    ) -> list[float] | None:
        left, top, right, bottom = seed_bbox
        top_text_blocks = [
            block
            for block in layout_blocks
            if self._looks_like_page_text_block(block)
            and self._bbox_horizontal_overlap_ratio(block.bbox, [left, top, right, bottom]) >= 0.35
            and block.bbox[1] <= top + (bottom - top) * 0.3
        ]
        bottom_text_blocks = [
            block
            for block in layout_blocks
            if self._looks_like_page_text_block(block)
            and self._bbox_horizontal_overlap_ratio(block.bbox, [left, top, right, bottom]) >= 0.35
            and block.bbox[3] >= bottom - (bottom - top) * 0.3
        ]
        if top_text_blocks:
            top = max(top, max(block.bbox[3] for block in top_text_blocks) + 8.0)
        if bottom_text_blocks:
            bottom = min(bottom, min(block.bbox[1] for block in bottom_text_blocks) - 8.0)
        top = max(page_bounds[1] + 12.0, top)
        bottom = min(page_bounds[3] - 12.0, bottom)
        if bottom - top < 72.0:
            return seed_bbox
        return [left, top, right, bottom]

    def _looks_like_page_image_block(self, block: _PdfPageLayoutBlock) -> bool:
        if block.block_type == 1:
            return True
        return not block.text.strip() and (block.bbox[2] - block.bbox[0]) >= 96.0 and (block.bbox[3] - block.bbox[1]) >= 96.0

    def _looks_like_page_text_block(self, block: _PdfPageLayoutBlock) -> bool:
        if block.block_type == 1:
            return False
        return bool(re.search(r"[A-Za-z0-9\u4e00-\u9fff]", block.text))

    def _bbox_horizontal_overlap_ratio(self, left: list[float], right: list[float]) -> float:
        overlap = min(left[2], right[2]) - max(left[0], right[0])
        if overlap <= 0:
            return 0.0
        left_width = max(left[2] - left[0], 1.0)
        right_width = max(right[2] - right[0], 1.0)
        return overlap / min(left_width, right_width)

    def _bbox_overlap_area(self, left_bbox: list[float], right_bbox: list[float]) -> float:
        left = max(float(left_bbox[0]), float(right_bbox[0]))
        top = max(float(left_bbox[1]), float(right_bbox[1]))
        right = min(float(left_bbox[2]), float(right_bbox[2]))
        bottom = min(float(left_bbox[3]), float(right_bbox[3]))
        if right <= left or bottom <= top:
            return 0.0
        return (right - left) * (bottom - top)

    def _save_pdf_crop(self, page: object, rect: object, target_path: Path) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        pixmap = page.get_pixmap(clip=rect, alpha=False)
        try:
            pixmap.save(str(target_path))
        finally:
            pixmap = None

    def _materialized_document_image_path(self, document_image: object) -> Path:
        document_id = str(getattr(document_image, "document_id"))
        block_id = str(getattr(document_image, "block_id"))
        return (self.output_root.parent / "document-images" / document_id / f"{block_id}.png").resolve()

    def _mark_document_image_materialized(self, document_image: object, materialized_path: Path) -> None:
        storage_path = str(materialized_path)
        if getattr(document_image, "storage_path", None) != storage_path:
            setattr(document_image, "storage_path", storage_path)
        metadata = dict(getattr(document_image, "metadata_json", {}) or {})
        metadata.update(
            {
                "storage_status": "materialized",
                "materialized_via": "pdf_export_crop",
                "materialized_at": _utcnow().isoformat(),
            }
        )
        setattr(document_image, "metadata_json", metadata)

    def _safe_epub_archive_path(self, candidate: object) -> str | None:
        if not isinstance(candidate, str):
            return None
        normalized = posixpath.normpath(candidate.replace("\\", "/").strip())
        if not normalized or normalized == "." or normalized.startswith("/"):
            return None
        normalized_path = PurePosixPath(normalized)
        if any(part == ".." for part in normalized_path.parts):
            return None
        return normalized_path.as_posix()
