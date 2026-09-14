from __future__ import annotations

import functools
import hashlib
import html
import json
import mimetypes
import re
import shutil
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

from book_agent.core.ids import stable_id
from book_agent.domain.document_titles import (
    compose_document_title,
    document_display_title,
    document_source_title,
    safe_title_for_filename,
)
from book_agent.domain.enums import (
    ActionType,
    BlockType,
    ChapterStatus,
    Detector,
    DocumentStatus,
    ExportStatus,
    ExportType,
    IssueStatus,
    RootCauseLayer,
    SourceType,
)
from book_agent.domain.models.review import Export, ReviewIssue
from book_agent.domain.structure.artifact_grouping import resolve_artifact_group_context_ids
from book_agent.domain.structure.epub import (
    _parse_xml_document,
)
from book_agent.export import (
    alignment,
    code_text,
    epub_assets,
    evidence,
    markup,
    pdf_crop,
    render_repair,
    stylesheets,
    titles,
)
from book_agent.export.common import (
    _SEVERITY_RANK,
    _SPECIAL_PDF_PAGE_FAMILIES,
    _display_author_value,
    _document_export_label,
    _excerpt_text,
    _is_academic_paper_document,
    _is_pdf_document,
    _normalize_render_text,
    _normalize_signature_text,
    _utcnow,
)
from book_agent.export.models import (
    DocumentImageMaterialization,
    ExportArtifacts,
    ExportFollowupAction,
    ExportIssueSyncArtifacts,
    ExportMisalignmentEvidence,
    MergedRenderBlock,
)
from book_agent.export.pdf_crop import (
    apply_document_image_materializations,
    plan_document_image_materialization,
)
from book_agent.infra.repositories.export import (
    ChapterExportBundle,
    DocumentExportBundle,
    ExportRepository,
)
from book_agent.orchestrator.rule_engine import build_issue_action
from book_agent.services.layout_validate import LayoutValidationService


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


_REVIEW_PACKAGE_CHAPTER_STATUSES = {
    ChapterStatus.TRANSLATED,
    ChapterStatus.QA_CHECKED,
    ChapterStatus.REVIEW_REQUIRED,
    ChapterStatus.APPROVED,
    ChapterStatus.EXPORTED,
}
_FINAL_EXPORT_CHAPTER_STATUSES = {ChapterStatus.QA_CHECKED, ChapterStatus.APPROVED, ChapterStatus.EXPORTED}
_GATED_EXPORT_TYPES = {
    ExportType.BILINGUAL_HTML,
    ExportType.MERGED_HTML,
    ExportType.MERGED_MARKDOWN,
    ExportType.ZH_EPUB,
    ExportType.REBUILT_EPUB,
    ExportType.REBUILT_PDF,
}


@dataclass(slots=True)
class ExportIssuePlan:
    """Export-time issues a check found, and the earlier issues of that kind it compared against."""

    issues: list[ReviewIssue]
    existing: list[ReviewIssue]
    now: datetime


@dataclass(slots=True)
class ChapterGateEvaluation:
    export_type: ExportType
    unsupported: bool = False
    status_blocked: bool = False
    alignment: ExportIssuePlan | None = None
    layout: ExportIssuePlan | None = None
    # Filled in by ExportService.sync_gate_issues.
    alignment_artifacts: ExportIssueSyncArtifacts | None = None
    layout_artifacts: ExportIssueSyncArtifacts | None = None


def _within_render_model_scope(method):
    """Build each chapter's render blocks at most once per export call.

    The gate (layout validation), manifests and every renderer ask for the same
    chapter's render blocks; inside one export they read the same bundle, so
    the blocks are cached by bundle object for the duration of the call.
    """

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        if self._render_block_cache is not None:
            return method(self, *args, **kwargs)
        self._render_block_cache = {}
        try:
            return method(self, *args, **kwargs)
        finally:
            self._render_block_cache = None

    return wrapper


class ExportService:
    def __init__(
        self,
        repository: ExportRepository,
        output_root: str | Path = "artifacts/exports",
        layout_validation_service: LayoutValidationService | None = None,
    ):
        self.repository = repository
        self.output_root = Path(output_root)
        self.layout_validation_service = layout_validation_service or LayoutValidationService()
        self._render_block_cache: dict[int, tuple[ChapterExportBundle, list[MergedRenderBlock]]] | None = None

    def export_review_package(self, chapter_id: str) -> ExportArtifacts:
        return self.export_chapter(chapter_id, ExportType.REVIEW_PACKAGE)

    def export_bilingual_html(self, chapter_id: str) -> ExportArtifacts:
        return self.export_chapter(chapter_id, ExportType.BILINGUAL_HTML)

    def export_bilingual_markdown(self, chapter_id: str) -> ExportArtifacts:
        return self.export_chapter(chapter_id, ExportType.BILINGUAL_MARKDOWN)

    def export_document_merged_html(self, document_id: str) -> ExportArtifacts:
        return self._export_document(document_id, _DOCUMENT_RENDERERS[ExportType.MERGED_HTML])

    def export_document_merged_markdown(self, document_id: str) -> ExportArtifacts:
        return self._export_document(document_id, _DOCUMENT_RENDERERS[ExportType.MERGED_MARKDOWN])

    def export_document_rebuilt_epub(self, document_id: str) -> ExportArtifacts:
        return self._export_document(document_id, _DOCUMENT_RENDERERS[ExportType.REBUILT_EPUB])

    def export_document_zh_epub(self, document_id: str) -> ExportArtifacts:
        return self._export_document(document_id, _DOCUMENT_RENDERERS[ExportType.ZH_EPUB])

    def export_document_rebuilt_pdf(self, document_id: str) -> ExportArtifacts:
        return self._export_document(document_id, _DOCUMENT_RENDERERS[ExportType.REBUILT_PDF])

    @_within_render_model_scope
    def _export_document(self, document_id: str, renderer: DocumentRenderer) -> ExportArtifacts:
        bundle = self.repository.load_document_bundle(document_id)
        if renderer.epub_source_only_error is not None and bundle.document.source_type != SourceType.EPUB:
            raise ExportGateError(renderer.epub_source_only_error)
        for chapter_bundle in bundle.chapters:
            self._enforce_gate(chapter_bundle, renderer.export_type)
        upstream_exports: dict[ExportType, ExportArtifacts] = {}
        if renderer.uses_upstream_exports:
            upstream_exports = self._ensure_rebuilt_upstream_exports(document_id)
            bundle = self.repository.load_document_bundle(document_id)
        if renderer.syncs_document_title:
            self._sync_document_title_tgt(bundle)
        output_dir = self.output_root / bundle.document.id
        output_dir.mkdir(parents=True, exist_ok=True)
        file_path = output_dir / renderer.file_name
        manifest_path = output_dir / renderer.manifest_name
        rendered_text = renderer.render(self, bundle, output_dir, file_path, upstream_exports)
        if rendered_text is not None:
            self._write_document_export_alias(output_dir, bundle.document, renderer.export_type, rendered_text)
        manifest_path.write_text(
            json.dumps(renderer.manifest(self, bundle, file_path, upstream_exports), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        export = self._record_document_export(bundle, renderer.export_type, file_path, manifest_path)
        self.repository.save_export(export)
        if renderer.apply_status_updates is not None:
            renderer.apply_status_updates(self, bundle)
        self.repository.session.flush()
        return ExportArtifacts(
            export_record=export,
            file_path=file_path,
            manifest_path=manifest_path,
        )

    def _sync_document_title_tgt(self, bundle: DocumentExportBundle) -> None:
        current_title_tgt = _normalize_render_text(bundle.document.title_tgt)
        if current_title_tgt:
            return
        derived_title_tgt = self._derive_document_title_tgt(bundle)
        if not derived_title_tgt:
            return
        if _normalize_render_text(derived_title_tgt).casefold() == _normalize_render_text(
            document_source_title(bundle.document)
        ).casefold():
            return
        metadata = dict(bundle.document.metadata_json or {})
        metadata["document_title_tgt"] = derived_title_tgt
        metadata["document_title_tgt_resolution_source"] = "translated_frontmatter_heading"
        document_title_metadata = dict(metadata.get("document_title") or {})
        document_title_metadata.setdefault("title", document_source_title(bundle.document) or bundle.document.title)
        document_title_metadata.setdefault("src", document_source_title(bundle.document) or bundle.document.title_src)
        document_title_metadata["tgt"] = derived_title_tgt
        document_title_metadata.setdefault("resolution_source", "translated_frontmatter_heading")
        metadata["document_title"] = document_title_metadata
        bundle.document.title_tgt = derived_title_tgt
        bundle.document.metadata_json = metadata
        bundle.document.updated_at = _utcnow()
        self.repository.session.merge(bundle.document)

    def _derive_document_title_tgt(self, bundle: DocumentExportBundle) -> str | None:
        if bundle.document.source_type != SourceType.EPUB:
            return None
        metadata = dict(bundle.document.metadata_json or {})
        source_title = _normalize_render_text(metadata.get("title") or bundle.document.title or bundle.document.title_src)
        source_subtitle = _normalize_render_text(metadata.get("subtitle"))
        if not source_title:
            return None

        title_target: str | None = None
        subtitle_target: str | None = None
        for chapter_bundle in bundle.chapters[:2]:
            render_blocks = self._render_blocks_for_chapter(chapter_bundle)
            for render_block in render_blocks:
                if render_block.block_type != BlockType.HEADING.value:
                    continue
                source_heading = _normalize_render_text(render_block.source_text)
                target_heading = _normalize_render_text(render_block.target_text)
                if not source_heading or not target_heading:
                    continue
                if render_repair.looks_like_prose_title_text(
                    target_heading,
                    source_heading_text=source_heading,
                    fallback_title=source_title,
                ):
                    continue
                if title_target is None and source_heading.casefold() == source_title.casefold():
                    title_target = target_heading
                    continue
                if (
                    subtitle_target is None
                    and source_subtitle
                    and source_heading.casefold() == source_subtitle.casefold()
                ):
                    subtitle_target = target_heading
            if title_target and (subtitle_target or not source_subtitle):
                break

        if title_target is None:
            return None
        return compose_document_title(title_target, subtitle_target, separator="：")

    def _write_document_export_alias(
        self,
        output_dir: Path,
        document,
        export_type: ExportType,
        content: str,
    ) -> None:
        suffix = ".html" if export_type == ExportType.MERGED_HTML else ".md"
        alias_path = output_dir / (
            f"{safe_title_for_filename(document_display_title(document), wrap_book_quotes=True)}"
            f"-{_document_export_label(export_type)}{suffix}"
        )
        for candidate in output_dir.glob(f"《*》-{_document_export_label(export_type)}{suffix}"):
            if candidate == alias_path:
                continue
            candidate.unlink(missing_ok=True)
        alias_path.write_text(content, encoding="utf-8")

    def _manifest_path_from_export_record(self, export: Export) -> Path | None:
        raw_path = str((export.input_version_bundle_json or {}).get("sidecar_manifest_path") or "").strip()
        if not raw_path:
            return None
        manifest_path = Path(raw_path)
        return manifest_path if manifest_path.exists() else None

    def _ensure_upstream_document_export(
        self,
        document_id: str,
        export_type: ExportType,
    ) -> ExportArtifacts:
        records = self.repository.list_document_exports_filtered(
            document_id,
            export_type=export_type,
            status=ExportStatus.SUCCEEDED,
            limit=1,
        )
        if records:
            export_record = records[0]
            file_path = Path(export_record.file_path)
            if file_path.exists():
                return ExportArtifacts(
                    export_record=export_record,
                    file_path=file_path,
                    manifest_path=self._manifest_path_from_export_record(export_record),
                )
        if export_type == ExportType.MERGED_HTML:
            return self.export_document_merged_html(document_id)
        if export_type == ExportType.MERGED_MARKDOWN:
            return self.export_document_merged_markdown(document_id)
        raise ExportGateError(f"Unsupported rebuilt upstream export type: {export_type.value}")

    def _ensure_rebuilt_upstream_exports(self, document_id: str) -> dict[ExportType, ExportArtifacts]:
        return {
            ExportType.MERGED_HTML: self._ensure_upstream_document_export(document_id, ExportType.MERGED_HTML),
            ExportType.MERGED_MARKDOWN: self._ensure_upstream_document_export(document_id, ExportType.MERGED_MARKDOWN),
        }

    @_within_render_model_scope
    def assert_chapter_exportable(self, chapter_id: str, export_type: ExportType) -> None:
        bundle = self.repository.load_chapter_bundle(chapter_id)
        self._enforce_gate(bundle, export_type)

    @_within_render_model_scope
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
        misalignment_evidence = alignment.build_export_misalignment_evidence(bundle)
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
                "active_snapshot_versions": alignment.snapshot_version_map(bundle),
                "translation_usage_summary": evidence.translation_usage_summary(bundle),
                "translation_usage_breakdown": evidence.translation_usage_breakdown(bundle),
                "translation_usage_timeline": evidence.translation_usage_timeline(bundle),
                "translation_usage_highlights": evidence.translation_usage_highlights(bundle),
                "issue_status_summary": evidence.issue_status_summary(bundle),
                "sidecar_manifest_path": str(manifest_path) if manifest_path is not None else None,
                "export_auto_followup_summary": evidence.export_auto_followup_summary(bundle),
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
                "translation_usage_summary": evidence.translation_usage_summary_from_runs(
                    [run for chapter in bundle.chapters for run in chapter.translation_runs]
                ),
                "translation_usage_breakdown": evidence.translation_usage_breakdown_from_runs(
                    [run for chapter in bundle.chapters for run in chapter.translation_runs]
                ),
                "translation_usage_timeline": evidence.translation_usage_timeline_from_runs(
                    [run for chapter in bundle.chapters for run in chapter.translation_runs]
                ),
                "translation_usage_highlights": evidence.translation_usage_highlights_from_runs(
                    [run for chapter in bundle.chapters for run in chapter.translation_runs]
                ),
                "issue_status_summary": evidence.document_issue_status_summary(bundle),
                "sidecar_manifest_path": str(manifest_path) if manifest_path is not None else None,
                "merged_render_summary": evidence.merged_render_summary(visible_chapters),
            },
            file_path=str(file_path),
            status=ExportStatus.SUCCEEDED,
            created_at=now,
            updated_at=now,
        )

    _MODEL_COST_TABLE_PER_MILLION: dict[str, tuple[float, float]] = {
        # (input_per_million_usd, output_per_million_usd) — public list prices.
        "deepseek-chat": (0.27, 1.10),
        "deepseek-reasoner": (0.55, 2.19),
        "gpt-4o": (2.50, 10.00),
        "gpt-4o-mini": (0.15, 0.60),
        "gpt-4.1": (2.00, 8.00),
        "gpt-4.1-mini": (0.40, 1.60),
        "gpt-4.1-nano": (0.10, 0.40),
        "claude-3-5-sonnet": (3.00, 15.00),
        "claude-3-5-haiku": (0.80, 4.00),
        "claude-opus-4": (15.00, 75.00),
        "claude-sonnet-4": (3.00, 15.00),
        "claude-haiku-4": (1.00, 5.00),
    }

    def _estimate_run_cost_usd(self, run: object) -> float | None:
        stored = getattr(run, "cost_usd", None)
        if stored is not None:
            try:
                return float(stored)
            except (TypeError, ValueError):
                pass
        token_in = int(getattr(run, "token_in", 0) or 0)
        token_out = int(getattr(run, "token_out", 0) or 0)
        if token_in == 0 and token_out == 0:
            return None
        model_name = str(getattr(run, "model_name", "") or "").strip().lower()
        if not model_name:
            return None
        prices = self._MODEL_COST_TABLE_PER_MILLION.get(model_name)
        if prices is None:
            for key, candidate in self._MODEL_COST_TABLE_PER_MILLION.items():
                if model_name.startswith(key):
                    prices = candidate
                    break
        if prices is None:
            return None
        input_price, output_price = prices
        return round(
            (token_in / 1_000_000.0) * input_price
            + (token_out / 1_000_000.0) * output_price,
            6,
        )

    def _sum_run_cost_usd(self, runs: list[object]) -> float:
        total = 0.0
        for run in runs:
            estimate = self._estimate_run_cost_usd(run)
            if estimate is not None:
                total += estimate
        return round(total, 6)

    def _format_usage_summary_lines(self, runs: list[object]) -> list[tuple[str, str]]:
        summary = evidence.translation_usage_summary_from_runs(runs)
        run_count = int(summary.get("run_count") or 0)
        if run_count == 0:
            return []
        succeeded = int(summary.get("succeeded_run_count") or 0)
        token_in = int(summary.get("total_token_in") or 0)
        token_out = int(summary.get("total_token_out") or 0)
        total_tokens = token_in + token_out
        priced_runs = sum(1 for run in runs if self._estimate_run_cost_usd(run) is not None)
        estimated_cost = self._sum_run_cost_usd(runs)
        any_stored_cost = any(getattr(run, "cost_usd", None) is not None for run in runs)
        avg_latency = summary.get("avg_latency_ms")
        total_latency_ms = int(summary.get("total_latency_ms") or 0)
        lines: list[tuple[str, str]] = []
        lines.append(("翻译调用", f"{run_count:,} 次（成功 {succeeded:,}）"))
        lines.append((
            "Token 消耗",
            f"输入 {token_in:,} · 输出 {token_out:,} · 合计 {total_tokens:,}",
        ))
        if priced_runs == 0:
            lines.append(("调用费用", "未配置单价，无法估算"))
        else:
            suffix = ""
            if not any_stored_cost:
                suffix = "（按公开单价估算）"
            elif priced_runs < run_count:
                suffix = f"（{priced_runs:,}/{run_count:,} 次已计费）"
            lines.append(("调用费用", f"US$ {estimated_cost:,.4f}{suffix}"))
        if avg_latency is not None:
            lines.append((
                "延迟",
                f"平均 {float(avg_latency):,.0f} ms · 累计 {total_latency_ms:,} ms",
            ))
        latest_run_at = summary.get("latest_run_at")
        if latest_run_at:
            lines.append(("最近一次调用", str(latest_run_at)))
        return lines

    def _build_usage_summary_markdown(self, runs: list[object]) -> list[str]:
        rows = self._format_usage_summary_lines(runs)
        if not rows:
            return []
        lines: list[str] = ["> **翻译统计**  "]
        for index, (label, value) in enumerate(rows):
            suffix = "  " if index < len(rows) - 1 else ""
            lines.append(f"> - {label}: {value}{suffix}")
        lines.append("")
        return lines

    def _build_usage_summary_html(self, runs: list[object]) -> str:
        rows = self._format_usage_summary_lines(runs)
        if not rows:
            return ""
        items = "".join(
            "<li class='usage-row'>"
            f"<span class='usage-label'>{html.escape(label)}</span>"
            f"<span class='usage-value'>{html.escape(value)}</span>"
            "</li>"
            for label, value in rows
        )
        return (
            "<section class='usage-summary' aria-label='Translation usage summary'>"
            "<div class='usage-kicker'>翻译统计</div>"
            f"<ul class='usage-list'>{items}</ul>"
            "</section>"
        )

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
        evaluation = self.evaluate_chapter_gate(bundle, export_type)
        self.sync_gate_issues(bundle, evaluation)
        self._raise_for_gate(bundle, evaluation)

    def evaluate_chapter_gate(self, bundle: ChapterExportBundle, export_type: ExportType) -> ChapterGateEvaluation:
        """Decide whether a chapter may be exported, without writing anything.

        Alignment issues are only planned when the chapter status allows the
        export, and layout issues only when alignment is clean, mirroring the
        order in which the gate reports problems.
        """
        if export_type not in _GATED_EXPORT_TYPES and export_type != ExportType.REVIEW_PACKAGE:
            return ChapterGateEvaluation(export_type=export_type, unsupported=True)
        allowed_statuses = (
            _REVIEW_PACKAGE_CHAPTER_STATUSES if export_type == ExportType.REVIEW_PACKAGE else _FINAL_EXPORT_CHAPTER_STATUSES
        )
        if bundle.chapter.status not in allowed_statuses:
            return ChapterGateEvaluation(export_type=export_type, status_blocked=True)
        now = _utcnow()
        alignment_plan = self._plan_export_alignment_issues(bundle, now)
        if export_type == ExportType.REVIEW_PACKAGE or alignment_plan.issues:
            return ChapterGateEvaluation(export_type=export_type, alignment=alignment_plan)
        layout_plan = None
        if _is_pdf_document(bundle.document):
            layout_plan = self._plan_export_layout_issues(bundle, now, self._render_blocks_for_chapter(bundle))
        return ChapterGateEvaluation(export_type=export_type, alignment=alignment_plan, layout=layout_plan)

    def sync_gate_issues(
        self,
        bundle: ChapterExportBundle,
        evaluation: ChapterGateEvaluation,
    ) -> None:
        """Persist the export-time issues an evaluation found and resolve the ones it no longer sees."""
        if evaluation.alignment is not None:
            evaluation.alignment_artifacts = self._apply_export_issue_plan(
                bundle,
                evaluation.alignment,
                resolution_note="Resolved by latest export-time alignment check.",
            )
        if evaluation.layout is not None:
            evaluation.layout_artifacts = self._apply_export_issue_plan(
                bundle,
                evaluation.layout,
                resolution_note="Resolved by latest export-time layout validation check.",
            )

    def _raise_for_gate(self, bundle: ChapterExportBundle, evaluation: ChapterGateEvaluation) -> None:
        chapter_id = bundle.chapter.id
        chapter_status = bundle.chapter.status
        if evaluation.unsupported:
            raise ExportGateError(f"Unsupported export type in P0: {evaluation.export_type.value}")
        if evaluation.export_type == ExportType.REVIEW_PACKAGE:
            if evaluation.status_blocked:
                raise ExportGateError(
                    f"Chapter {chapter_id} is not ready for review export from status {chapter_status.value}."
                )
            return
        if evaluation.status_blocked:
            blocking_issues, followup_actions = self._open_blocking_followup_actions(chapter_id)
            raise ExportGateError(
                f"Chapter {chapter_id} must pass review before final export; current status is {chapter_status.value}.",
                chapter_id=chapter_id,
                issue_ids=[issue.id for issue in blocking_issues],
                followup_actions=followup_actions,
            )
        for artifacts, problem in (
            (evaluation.alignment_artifacts, "export-time misalignment anomalies"),
            (evaluation.layout_artifacts, "export-time layout validation issues"),
        ):
            if artifacts is not None and artifacts.issues:
                raise ExportGateError(
                    f"Chapter {chapter_id} has {problem} and cannot be exported. "
                    f"Review issues created: {', '.join(issue.id for issue in artifacts.issues)}.",
                    chapter_id=chapter_id,
                    issue_ids=[issue.id for issue in artifacts.issues],
                    followup_actions=[
                        ExportFollowupAction(
                            action_id=action.id,
                            issue_id=action.issue_id,
                            action_type=action.action_type.value,
                            scope_type=action.scope_type.value,
                            scope_id=action.scope_id,
                        )
                        for action in artifacts.actions
                    ],
                )
        if self.repository.has_open_blocking_issues(chapter_id):
            blocking_issues, followup_actions = self._open_blocking_followup_actions(chapter_id)
            raise ExportGateError(
                f"Chapter {chapter_id} still has open blocking review issues and cannot be exported.",
                chapter_id=chapter_id,
                issue_ids=[issue.id for issue in blocking_issues],
                followup_actions=followup_actions,
            )

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
        target_map = alignment.build_target_map(bundle)
        sentence_targets = alignment.sentence_target_map(bundle)
        misalignment_evidence = alignment.build_export_misalignment_evidence(bundle)
        render_blocks = self._render_blocks_for_chapter(bundle)

        return {
            "chapter_id": bundle.chapter.id,
            "chapter_title": bundle.chapter.title_src,
            "quality_summary": evidence.quality_summary_payload(bundle),
            "pdf_page_evidence": evidence.pdf_page_evidence_payload(bundle),
            "pdf_image_evidence": evidence.pdf_image_evidence_payload(bundle),
            "pdf_preserve_evidence": self._pdf_preserve_evidence_payload(bundle, render_blocks),
            "pdf_page_debug_evidence": self._pdf_page_debug_evidence_payload(bundle, render_blocks),
            "version_evidence": evidence.version_evidence_payload(bundle),
            "recent_repair_events": evidence.recent_repair_events_payload(bundle),
            "export_auto_followup_evidence": evidence.export_auto_followup_evidence_payload(bundle),
            "export_time_misalignment_evidence": evidence.misalignment_evidence_payload(misalignment_evidence),
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
        target_map = alignment.build_target_map(bundle)
        sentence_targets = alignment.sentence_target_map(bundle)
        misalignment_evidence = alignment.build_export_misalignment_evidence(bundle)
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
            "quality_summary": evidence.quality_summary_payload(bundle),
            "pdf_page_evidence": evidence.pdf_page_evidence_payload(bundle),
            "pdf_image_evidence": evidence.pdf_image_evidence_payload(bundle),
            "pdf_preserve_evidence": self._pdf_preserve_evidence_payload(bundle, render_blocks),
            "version_evidence": evidence.version_evidence_payload(bundle),
            "recent_repair_events": evidence.recent_repair_events_payload(bundle),
            "export_auto_followup_evidence": evidence.export_auto_followup_evidence_payload(bundle),
            "export_time_misalignment_evidence": evidence.misalignment_evidence_payload(misalignment_evidence),
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

    def _build_export_misalignment_evidence(self, bundle: ChapterExportBundle) -> ExportMisalignmentEvidence:
        return alignment.build_export_misalignment_evidence(bundle)

    def _sync_export_alignment_issues(self, bundle: ChapterExportBundle) -> ExportIssueSyncArtifacts:
        return self._apply_export_issue_plan(
            bundle,
            self._plan_export_alignment_issues(bundle, _utcnow()),
            resolution_note="Resolved by latest export-time alignment check.",
        )

    def _sync_export_layout_issues(
        self,
        bundle: ChapterExportBundle,
        render_blocks: list[MergedRenderBlock] | None = None,
    ) -> ExportIssueSyncArtifacts:
        return self._apply_export_issue_plan(
            bundle,
            self._plan_export_layout_issues(bundle, _utcnow(), render_blocks),
            resolution_note="Resolved by latest export-time layout validation check.",
        )

    def _plan_export_alignment_issues(self, bundle: ChapterExportBundle, now: datetime) -> ExportIssuePlan:
        return ExportIssuePlan(
            issues=alignment.build_export_alignment_issues(bundle, now),
            existing=[
                issue
                for issue in bundle.review_issues
                if issue.issue_type == "ALIGNMENT_FAILURE" and issue.root_cause_layer == RootCauseLayer.EXPORT
            ],
            now=now,
        )

    def _plan_export_layout_issues(
        self,
        bundle: ChapterExportBundle,
        now: datetime,
        render_blocks: list[MergedRenderBlock] | None,
    ) -> ExportIssuePlan:
        issue = self._build_export_layout_issue(bundle, now, render_blocks=render_blocks)
        return ExportIssuePlan(
            issues=[issue] if issue is not None else [],
            existing=[
                current
                for current in bundle.review_issues
                if current.issue_type == "LAYOUT_VALIDATION_FAILURE"
                and current.root_cause_layer == RootCauseLayer.STRUCTURE
                and (current.evidence_json or {}).get("reason") == "export_layout_validation"
            ],
            now=now,
        )

    def _apply_export_issue_plan(
        self,
        bundle: ChapterExportBundle,
        plan: ExportIssuePlan,
        *,
        resolution_note: str,
    ) -> ExportIssueSyncArtifacts:
        active_issue_ids = {issue.id for issue in plan.issues}
        for issue in plan.existing:
            if issue.id in active_issue_ids:
                continue
            if issue.status in {IssueStatus.OPEN, IssueStatus.TRIAGED}:
                issue.status = IssueStatus.RESOLVED
                issue.resolution_note = resolution_note
                issue.updated_at = plan.now
                self.repository.session.merge(issue)

        for issue in plan.issues:
            self.repository.session.merge(issue)
        self.repository.session.flush()

        actions = [build_issue_action(issue) for issue in plan.issues]
        for action in actions:
            self.repository.session.merge(action)
        self.repository.session.flush()

        retained_issue_ids = {issue.id for issue in plan.existing if issue.status != IssueStatus.RESOLVED}
        bundle.review_issues = [
            issue for issue in bundle.review_issues if issue.id not in retained_issue_ids
        ] + plan.issues
        return ExportIssueSyncArtifacts(issues=plan.issues, actions=actions)

    def _build_export_alignment_issues(
        self,
        bundle: ChapterExportBundle,
        now: datetime,
    ) -> list[ReviewIssue]:
        return alignment.build_export_alignment_issues(bundle, now)

    def _build_export_layout_issue(
        self,
        bundle: ChapterExportBundle,
        now: datetime,
        *,
        render_blocks: list[MergedRenderBlock] | None = None,
    ) -> ReviewIssue | None:
        current_render_blocks = render_blocks if render_blocks is not None else self._render_blocks_for_chapter(bundle)
        validation_result = self.layout_validation_service.validate_chapter(bundle, current_render_blocks)
        if not validation_result.issues:
            return None

        highest_severity = max(
            (issue.severity for issue in validation_result.issues),
            key=lambda severity: _SEVERITY_RANK.get(severity, 0),
        )
        representative_issue = validation_result.issues[0]
        return ReviewIssue(
            id=stable_id(
                "review-issue",
                bundle.chapter.document_id,
                bundle.chapter.id,
                "LAYOUT_VALIDATION_FAILURE",
                "export-layout",
            ),
            document_id=bundle.chapter.document_id,
            chapter_id=bundle.chapter.id,
            block_id=representative_issue.block_id,
            sentence_id=None,
            packet_id=None,
            issue_type="LAYOUT_VALIDATION_FAILURE",
            root_cause_layer=RootCauseLayer.STRUCTURE,
            severity=highest_severity,
            blocking=True,
            detector=Detector.RULE,
            confidence=1.0,
            evidence_json={
                "reason": "export_layout_validation",
                "layout_issue_count": len(validation_result.issues),
                "layout_issue_codes": [issue.issue_code for issue in validation_result.issues],
                "layout_issues": [
                    {
                        "issue_code": issue.issue_code,
                        "message": issue.message,
                        "block_id": issue.block_id,
                        "block_type": issue.block_type,
                        "severity": issue.severity.value,
                        "blocking": issue.blocking,
                        "evidence": issue.evidence,
                    }
                    for issue in validation_result.issues
                ],
            },
            status=IssueStatus.OPEN,
            suggested_action=ActionType.REPARSE_CHAPTER.value,
            created_at=now,
            updated_at=now,
        )

    def _packet_current_sentence_ids(self, packet) -> list[str]:
        return alignment.packet_current_sentence_ids(packet)

    def _pdf_preserve_evidence_payload(
        self,
        bundle: ChapterExportBundle,
        render_blocks: list[MergedRenderBlock] | None = None,
    ) -> dict | None:
        page_evidence = evidence.pdf_page_evidence_payload(bundle)
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
            contract["preserve_policy"] = evidence.pdf_page_preserve_policy(contract)

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

    def _pdf_page_debug_evidence_payload(
        self,
        bundle: ChapterExportBundle,
        render_blocks: list[MergedRenderBlock] | None = None,
    ) -> dict | None:
        page_evidence = evidence.pdf_page_evidence_payload(bundle)
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

    def _build_merged_document_html(
        self,
        bundle: DocumentExportBundle,
        asset_path_by_block_id: dict[str, str] | None = None,
    ) -> str:
        visible_chapters = self._visible_merged_chapters(bundle)
        chapters_html = "".join(
            self._render_chapter_for_merged_html(chapter_bundle, visible_ordinal, render_blocks, title_text, asset_path_by_block_id)
            for visible_ordinal, chapter_bundle, render_blocks, title_text in visible_chapters
        )
        title = html.escape(document_display_title(bundle.document) or bundle.document.id)
        author_value = _display_author_value(bundle.document.author)
        author = html.escape(author_value) if author_value is not None else ""
        author_html = f"<div class='meta'>{author}</div>" if author else ""
        usage_html = self._build_usage_summary_html(
            evidence.collect_document_translation_runs(bundle)
        )
        return (
            "<html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{title}</title>"
            "<style>"
            f"{stylesheets.load('merged_document.css')}"
            f"{markup.usage_summary_css()}"
            "</style>"
            "<link rel='stylesheet' href='https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css'>"
            "</head><body>"
            "<div class='page-shell'>"
            "<main class='book-main'>"
            "<header class='hero' id='top'>"
            f"<h1>{title}</h1>"
            f"{author_html}"
            "</header>"
            f"{usage_html}"
            f"{chapters_html}"
            "</main>"
            "</div>"
            "<script src='https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js'></script>"
            "<script>"
            "document.querySelectorAll('.katex-source').forEach(el => {"
            "try { katex.render(el.textContent, el, {displayMode: true, throwOnError: false}); } catch(e) {}"
            "});"
            "</script>"
            "</body></html>"
        )

    def _build_merged_document_markdown(
        self,
        bundle: DocumentExportBundle,
        asset_path_by_block_id: dict[str, str] | None = None,
    ) -> str:
        visible_chapters = self._visible_merged_chapters(bundle)
        title = (document_display_title(bundle.document) or bundle.document.id or "Merged Reading Edition").strip()
        author = _display_author_value(bundle.document.author)

        # Chinese reading edition — only the book's own front matter
        # (title + author). No "Merged Reading Edition" kicker, no
        # "Document Summary" bullet list, no chapter TOC. Synthetic
        # export metadata doesn't belong in a reader-facing file.
        lines: list[str] = [f"# {title}", ""]
        if author:
            lines.extend([f"_Author: {author}_", ""])
        usage_lines = self._build_usage_summary_markdown(
            evidence.collect_document_translation_runs(bundle)
        )
        if usage_lines:
            lines.extend(usage_lines)

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
        # Use the chapter's own heading verbatim — re-prefixing with
        # "Chapter N: " on top of an already-numbered source title
        # (e.g. "第一章") just yields the redundant "Chapter 1: 第一章"
        # that confuses readers. Fall back to an ordinal-only heading
        # only when the chapter has no title at all.
        heading = f"## {title}" if title else f"## Chapter {visible_ordinal}"
        body_blocks = self._merged_body_blocks(render_blocks, title)
        lines = [heading, ""]
        for block in body_blocks:
            block_markdown = markup.render_block_markdown(
                block,
                asset_path_by_block_id,
                include_notice=False,
                include_source_caption=False,
            )
            if not block_markdown:
                continue
            lines.append(block_markdown)
            lines.append("")
        return lines

    def _merged_body_blocks(
        self,
        render_blocks: list[MergedRenderBlock],
        chapter_title: str,
    ) -> list[MergedRenderBlock]:
        normalized_title = _normalize_render_text(chapter_title) if chapter_title else ""
        body: list[MergedRenderBlock] = []
        dropped_title_heading = not normalized_title
        for block in render_blocks:
            if (
                not dropped_title_heading
                and block.block_type == BlockType.HEADING.value
            ):
                candidate = _normalize_render_text(
                    block.target_text or block.source_text or ""
                )
                if candidate and candidate == normalized_title:
                    dropped_title_heading = True
                    continue
            if self._is_code_language_label_block(block):
                continue
            body.append(block)
        return body

    _CODE_LANGUAGE_LABELS: frozenset[str] = frozenset(
        {
            "python", "javascript", "typescript", "js", "ts", "java", "c", "c++",
            "cpp", "c#", "csharp", "go", "golang", "rust", "ruby", "php", "swift",
            "kotlin", "scala", "r", "shell", "bash", "zsh", "sh", "sql", "html",
            "css", "scss", "sass", "less", "json", "jsonc", "yaml", "yml", "toml",
            "xml", "perl", "lua", "dart", "julia", "haskell", "clojure", "elixir",
            "erlang", "f#", "fsharp", "ocaml", "objective-c", "objectivec", "cobol",
            "fortran", "nim", "zig", "pascal", "lisp", "scheme", "groovy", "matlab",
            "vb", "vba", "powershell", "pwsh", "makefile", "dockerfile", "ini",
            "nginx", "apache", "markdown", "md", "tex", "latex", "graphql", "gql",
            "protobuf", "proto", "solidity", "asm", "assembly",
        }
    )

    def _is_code_language_label_block(self, block: MergedRenderBlock) -> bool:
        if block.block_type not in {
            BlockType.PARAGRAPH.value,
            BlockType.CAPTION.value,
            BlockType.QUOTE.value,
        }:
            return False
        text = str(block.target_text or block.source_text or "").strip()
        if not text or len(text) > 32:
            return False
        candidate = text.strip("*_`\"'()[]<> 　\t").strip().casefold()
        return bool(candidate) and candidate in self._CODE_LANGUAGE_LABELS

    def _render_block_markdown(
        self,
        block: MergedRenderBlock,
        asset_path_by_block_id: dict[str, str] | None = None,
        *,
        include_notice: bool = True,
        include_source_caption: bool = True,
    ) -> str:
        return markup.render_block_markdown(
            block,
            asset_path_by_block_id,
            include_notice=include_notice,
            include_source_caption=include_source_caption,
        )

    def _normalize_markdown_code_artifact_text(
        self,
        text: str,
        *,
        block: MergedRenderBlock | None = None,
    ) -> str:
        return code_text.normalize_markdown_code_artifact_text(text, block=block)

    def _markdown_details_source(self, text: str) -> str:
        return markup.markdown_details_source(text)

    def _split_table_candidate_line(self, line: str) -> list[str] | None:
        return markup.split_table_candidate_line(line)

    def _is_table_separator_row(self, row: list[str]) -> bool:
        return markup.is_table_separator_row(row)

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
                    "quality_summary": evidence.quality_summary_payload(chapter_bundle),
                }
            )
        manifest = {
            "document_id": bundle.document.id,
            "title": document_display_title(bundle.document),
            "title_src": document_source_title(bundle.document),
            "title_tgt": bundle.document.title_tgt,
            "author": _display_author_value(bundle.document.author),
            "export_type": export_type.value,
            "output_path": str(output_path),
            "chapter_count": len(visible_chapters),
            "pdf_image_summary": evidence.pdf_image_summary_payload(bundle),
            "translation_usage_summary": evidence.translation_usage_summary_from_runs(runs),
            "translation_usage_breakdown": evidence.translation_usage_breakdown_from_runs(runs),
            "translation_usage_timeline": evidence.translation_usage_timeline_from_runs(runs),
            "translation_usage_highlights": evidence.translation_usage_highlights_from_runs(runs),
            "issue_status_summary": evidence.document_issue_status_summary(bundle),
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

    def _build_rebuilt_document_manifest(
        self,
        bundle: DocumentExportBundle,
        output_path: Path,
        *,
        export_type: ExportType,
        renderer_kind: str,
        derived_from_exports: dict[ExportType, ExportArtifacts],
        expected_limitations: list[str],
    ) -> dict[str, object]:
        manifest = self._build_merged_document_manifest(
            bundle,
            output_path,
            export_type=export_type,
        )
        manifest.update(
            {
                "source_type": bundle.document.source_type.value,
                "contract_version": 1,
                "renderer_kind": renderer_kind,
                "derived_from_exports": [kind.value for kind in derived_from_exports],
                "derived_export_artifacts": {
                    kind.value: {
                        "file_path": str(artifacts.file_path),
                        "manifest_path": (
                            str(artifacts.manifest_path)
                            if artifacts.manifest_path is not None
                            else None
                        ),
                    }
                    for kind, artifacts in derived_from_exports.items()
                },
                "expected_limitations": expected_limitations,
            }
        )
        if export_type == ExportType.REBUILT_EPUB:
            manifest["epub_path"] = str(output_path)
        elif export_type == ExportType.ZH_EPUB:
            manifest["epub_path"] = str(output_path)
        elif export_type == ExportType.REBUILT_PDF:
            manifest["pdf_path"] = str(output_path)
        return manifest

    def _pdf_image_summary_payload(self, bundle: DocumentExportBundle) -> dict:
        return evidence.pdf_image_summary_payload(bundle)

    def _render_chapter_for_merged_html(
        self,
        bundle: ChapterExportBundle,
        visible_ordinal: int,
        render_blocks: list[MergedRenderBlock],
        title_text: str | None,
        asset_path_by_block_id: dict[str, str] | None = None,
    ) -> str:
        body_blocks = self._merged_body_blocks(
            list(render_blocks), str(title_text or "").strip()
        )
        blocks_html = "".join(
            markup.render_block_html(
                block,
                asset_path_by_block_id,
                include_source_toggle=False,
                include_notice=False,
                include_source_caption=False,
            )
            for block in body_blocks
        )
        if not title_text and not blocks_html:
            return ""
        title = html.escape(title_text) if title_text else ""
        # No "Chapter N" kicker and no English source-title subline —
        # the merged reading edition is Chinese-only.
        chapter_head = (
            "<div class='chapter-head'>"
            f"<h2>{title}</h2>"
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
            title_text = titles.resolved_chapter_title_text(chapter_bundle, render_blocks)
            if not title_text and not render_blocks:
                continue
            if titles.should_skip_merged_frontmatter_chapter(chapter_bundle, render_blocks, title_text):
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
            source_title = titles.source_title_for_chapter(chapter_bundle, title_text)
            main_chapter_number = titles.extract_main_chapter_number(source_title)
            starts_appendix = titles.looks_like_appendix_title(source_title)
            starts_frontmatter = titles.looks_like_frontmatter_title(source_title)

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
        cache = self._render_block_cache
        if cache is None:
            return self._build_render_blocks_for_chapter(bundle)
        cached = cache.get(id(bundle))
        if cached is None or cached[0] is not bundle:
            cached = (bundle, self._build_render_blocks_for_chapter(bundle))
            cache[id(bundle)] = cached
        return list(cached[1])

    def _build_render_blocks_for_chapter(self, bundle: ChapterExportBundle) -> list[MergedRenderBlock]:
        target_map = alignment.build_target_map(bundle)
        sentence_targets = alignment.sentence_target_map(bundle)
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
            target_ids = alignment.target_ids_for_block_sentences(block_sentences, sentence_targets, target_map)
            source_metadata = dict(block.source_span_json or {})
            if bool(source_metadata.get("repair_hidden_from_export")):
                continue
            if source_metadata.get("pdf_block_role") in {"header", "footer", "toc_entry"}:
                continue
            if block.block_type == BlockType.CAPTION and source_metadata.get("caption_for_block_id"):
                continue
            effective_block_type = render_repair.effective_export_block_type(block, source_metadata)

            render_mode = render_repair.render_mode_for_block(
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
            if effective_block_type in {BlockType.IMAGE, BlockType.FIGURE} and linked_caption_block is not None:
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
                                *alignment.target_ids_for_block_sentences(
                                    caption_sentences,
                                    sentence_targets,
                                    target_map,
                                ),
                                *alignment.target_ids_for_block_sentences(
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
                                *alignment.target_ids_for_block_sentences(
                                    caption_sentences,
                                    sentence_targets,
                                    target_map,
                                ),
                                *alignment.target_ids_for_block_sentences(
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

            normalized_target_text_override: str | None = None
            render_source_text, target_ids, normalized_target_text_override = render_repair.normalize_pdf_body_render_texts(
                document=bundle.document,
                block_type=effective_block_type,
                render_mode=render_mode,
                source_text=render_source_text,
                block_sentences=block_sentences,
                sentence_targets=sentence_targets,
                target_ids=target_ids,
                target_map=target_map,
                source_metadata=source_metadata,
            )
            target_text = markup.join_block_target_text(
                [target_map[target_id].text_zh for target_id in target_ids],
                block_type=target_block_type,
                render_mode=render_mode,
                source_text=render_source_text,
            )
            if normalized_target_text_override:
                target_text = normalized_target_text_override
            repair_target_text = str(source_metadata.get("repair_target_text") or "").strip()
            if repair_target_text:
                target_text = repair_target_text
            artifact_kind = render_repair.artifact_kind_for_block(
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
                    notice=markup.source_only_notice(block, artifact_kind, render_mode),
                )
            )
            repair_skip_ids = source_metadata.get("repair_skip_block_ids")
            if isinstance(repair_skip_ids, list):
                skipped_block_ids.update(
                    str(candidate)
                    for candidate in repair_skip_ids
                    if isinstance(candidate, str) and candidate.strip()
                )
            if len(render_blocks) >= 2 and render_repair.should_merge_adjacent_heading_render_blocks(render_blocks[-2], render_blocks[-1]):
                render_blocks[-2] = render_repair.merge_adjacent_heading_render_blocks(render_blocks[-2], render_blocks[-1])
                render_blocks.pop()
                continue
            if len(render_blocks) >= 2 and render_repair.should_merge_adjacent_prose_artifact_continuations(
                render_blocks[-2],
                render_blocks[-1],
            ):
                render_blocks[-2] = render_repair.merge_adjacent_prose_artifact_continuations(
                    render_blocks[-2],
                    render_blocks[-1],
                )
                render_blocks.pop()
                continue
            if (
                len(render_blocks) >= 2
                and not render_repair.has_refresh_split_render_fragments(render_blocks[-2], render_blocks[-1])
                and render_repair.should_merge_adjacent_code_blocks(render_blocks[-2], render_blocks[-1])
            ):
                render_blocks[-2] = render_repair.merge_adjacent_code_render_blocks(render_blocks[-2], render_blocks[-1])
                render_blocks.pop()
        if _is_academic_paper_document(bundle.document):
            return render_repair.normalize_academic_paper_render_blocks(bundle, render_blocks)
        if _is_pdf_document(bundle.document):
            return self._normalize_book_pdf_render_blocks(bundle, render_blocks)
        return render_blocks

    def _extract_main_chapter_number(self, title: str | None) -> int | None:
        return titles.extract_main_chapter_number(title)

    def _looks_like_prose_artifact_text(self, text: str, *, academic_paper: bool = False) -> bool:
        return code_text.looks_like_prose_artifact_text(text, academic_paper=academic_paper)

    def _looks_like_prose_continuation_artifact_text(self, text: str) -> bool:
        return code_text.looks_like_prose_continuation_artifact_text(text)

    def _normalize_book_pdf_render_blocks(
        self,
        bundle: ChapterExportBundle,
        render_blocks: list[MergedRenderBlock],
    ) -> list[MergedRenderBlock]:
        repaired_blocks: list[MergedRenderBlock] = []
        for index, block in enumerate(render_blocks):
            repaired_blocks.extend(self._repair_book_pdf_render_blocks(bundle, index, block))

        normalized: list[MergedRenderBlock] = []
        index = 0
        while index < len(repaired_blocks):
            current = repaired_blocks[index]
            if index + 2 < len(repaired_blocks) and render_repair.should_bridge_code_blocks_across_inline_artifact(
                current,
                repaired_blocks[index + 1],
                repaired_blocks[index + 2],
            ):
                current = render_repair.merge_code_blocks_across_inline_artifact(
                    current,
                    repaired_blocks[index + 1],
                    repaired_blocks[index + 2],
                )
                index += 3
            else:
                index += 1
            if (
                normalized
                and not render_repair.has_refresh_split_render_fragments(normalized[-1], current)
                and render_repair.should_merge_adjacent_code_blocks(normalized[-1], current)
            ):
                normalized[-1] = render_repair.merge_adjacent_code_render_blocks(normalized[-1], current)
                continue
            if normalized and render_repair.should_merge_adjacent_book_paragraph_fragments(normalized[-1], current):
                normalized[-1] = render_repair.merge_adjacent_book_paragraph_fragments(normalized[-1], current)
                continue
            normalized.append(current)
        if any(
            str(block.source_metadata.get("pdf_page_family") or "").strip().casefold() == "references"
            for block in normalized
        ):
            normalized = render_repair.normalize_reference_listing_render_blocks(normalized)
        return normalized

    def _repair_book_pdf_render_blocks(
        self,
        bundle: ChapterExportBundle,
        index: int,
        block: MergedRenderBlock,
    ) -> list[MergedRenderBlock]:
        if block.block_type == BlockType.HEADING.value and render_repair.should_drop_book_heading_label(bundle, index, block):
            return []
        if render_repair.should_clear_suspicious_short_source_target_text(block):
            block = render_repair.drop_render_block_target_text(block, flag="export_book_short_source_target_cleared")
        if render_repair.should_promote_book_block_to_code(block):
            block = render_repair.replace_render_block_as_code(block, flag="export_book_code_promoted")
        elif render_repair.should_demote_book_code_block_to_paragraph(block):
            demoted = render_repair.replace_render_block_as_paragraph(
                block,
                flag="export_book_code_demoted",
                drop_target_text=render_repair.should_drop_demoted_book_code_target_text(block),
            )
            # A reference listing rendered as code joined its entries with bare
            # newlines; restore the entry breaks once it becomes a paragraph.
            page_family = str(block.source_metadata.get("pdf_page_family") or "").strip().casefold()
            if page_family == "references" and demoted.target_text:
                joined = markup.join_reference_target_segments(
                    demoted.target_text.split("\n"),
                    source_text=block.source_text,
                )
                if joined:
                    demoted = replace(demoted, target_text=joined)
            block = demoted
        elif render_repair.should_demote_book_heading_with_prose_target(bundle, block):
            block = render_repair.replace_render_block_as_paragraph(
                block,
                flag="export_book_heading_target_demoted",
            )
        elif (
            block.block_type == BlockType.HEADING.value
            and render_repair.should_demote_book_heading_to_paragraph(bundle, index, block)
        ):
            block = render_repair.replace_render_block_as_paragraph(block, flag="export_book_heading_demoted")
        block = render_repair.repair_collapsed_list_target_text(block)
        split_blocks = render_repair.split_mixed_book_code_prose_render_block(block)
        if len(split_blocks) != 1 or split_blocks[0].block_id != block.block_id:
            return split_blocks
        return self._append_refreshed_split_render_fragments(split_blocks[0])

    def _extract_refresh_split_fragment_prose_text(
        self,
        block: MergedRenderBlock,
        fragment: dict[str, object],
    ) -> str | None:
        return render_repair.extract_refresh_split_fragment_prose_text(block, fragment)

    def _restore_bad_refresh_split_render_block(
        self,
        block: MergedRenderBlock,
        raw_fragments: list[object],
    ) -> list[MergedRenderBlock] | None:
        if block.render_mode != "source_artifact_full_width" or block.artifact_kind != "code":
            return None
        if not raw_fragments or not isinstance(raw_fragments[0], dict):
            return None

        first_fragment = raw_fragments[0]
        fragment_source = str(first_fragment.get("source_text") or "")
        if not fragment_source.strip():
            return None

        if render_repair.should_restore_labeled_prose_refresh_split(block, first_fragment):
            return [render_repair.restore_labeled_prose_refresh_split(block, first_fragment)]

        split_prefix = render_repair.split_leading_code_prefix_from_refresh_fragment(block, first_fragment)
        if split_prefix is not None:
            code_prefix, prose_suffix = split_prefix
            restored_block = render_repair.restore_code_refresh_split(
                block,
                {**first_fragment, "source_text": code_prefix},
            )
            remaining_fragments: list[object] = []
            refreshed_fragment = dict(first_fragment)
            refreshed_fragment["source_text"] = prose_suffix
            refreshed_fragment["block_type"] = BlockType.PARAGRAPH.value
            refreshed_fragment_metadata = dict(refreshed_fragment.get("source_metadata") or {})
            refreshed_recovery_flags = list(refreshed_fragment_metadata.get("recovery_flags") or [])
            refreshed_fragment_metadata["recovery_flags"] = list(
                dict.fromkeys([*refreshed_recovery_flags, "export_refresh_split_code_prefix_trimmed"])
            )
            refreshed_fragment_metadata["pdf_block_role"] = "body"
            refreshed_fragment["source_metadata"] = refreshed_fragment_metadata
            if render_repair.refresh_split_fragment_target_matches_prose_text(first_fragment, prose_suffix):
                refreshed_fragment["repair_source_signature"] = _normalize_signature_text(prose_suffix)
            else:
                refreshed_fragment.pop("target_text", None)
                refreshed_fragment.pop("repair_target_text", None)
                refreshed_fragment.pop("repair_source_signature", None)
            remaining_fragments.append(refreshed_fragment)
            remaining_fragments.extend(raw_fragments[1:])
            restored_metadata = dict(restored_block.source_metadata)
            restored_metadata["refresh_split_render_fragments"] = remaining_fragments
            refreshed_block = replace(restored_block, source_metadata=restored_metadata)
            resplit_refreshed = render_repair.split_mixed_book_code_prose_render_block(refreshed_block)
            if len(resplit_refreshed) != 1 or resplit_refreshed[0].block_id != refreshed_block.block_id:
                return resplit_refreshed
            return self._append_refreshed_split_render_fragments(refreshed_block)

        if not render_repair.should_restore_code_refresh_split(block, first_fragment):
            return None

        restored_block = render_repair.restore_code_refresh_split(block, first_fragment)
        restored_prose_text = render_repair.extract_refresh_split_fragment_prose_text(block, first_fragment) or ""
        restored_target_text = None
        if render_repair.refresh_split_fragment_target_matches_prose_text(first_fragment, restored_prose_text):
            restored_target_text = (
                str(first_fragment.get("target_text") or "").strip()
                or str(first_fragment.get("repair_target_text") or "").strip()
                or None
            )
        if restored_target_text:
            restored_block = replace(restored_block, target_text=restored_target_text, target_segment_ids=[])
        resplit_restored = render_repair.split_mixed_book_code_prose_render_block(restored_block)
        if len(raw_fragments) == 1:
            if len(resplit_restored) != 1 or resplit_restored[0].block_id != restored_block.block_id:
                return resplit_restored
            return [restored_block]

        restored_metadata = dict(restored_block.source_metadata)
        restored_metadata["refresh_split_render_fragments"] = list(raw_fragments[1:])
        refreshed_block = replace(restored_block, source_metadata=restored_metadata)
        resplit_refreshed = render_repair.split_mixed_book_code_prose_render_block(refreshed_block)
        if len(resplit_refreshed) != 1 or resplit_refreshed[0].block_id != refreshed_block.block_id:
            return resplit_refreshed
        return self._append_refreshed_split_render_fragments(refreshed_block)

    def _append_refreshed_split_render_fragments(
        self,
        block: MergedRenderBlock,
    ) -> list[MergedRenderBlock]:
        raw_fragments = block.source_metadata.get("refresh_split_render_fragments")
        if not isinstance(raw_fragments, list):
            return [block]
        restored_blocks = self._restore_bad_refresh_split_render_block(block, raw_fragments)
        if restored_blocks is not None:
            return restored_blocks

        rendered: list[MergedRenderBlock] = [block]
        for index, fragment in enumerate(raw_fragments):
            if not isinstance(fragment, dict):
                continue
            source_text = str(fragment.get("source_text") or "")
            if not source_text.strip():
                continue
            raw_block_type = str(fragment.get("block_type") or BlockType.PARAGRAPH.value).strip().casefold()
            try:
                fragment_block_type = BlockType(raw_block_type)
            except ValueError:
                fragment_block_type = BlockType.PARAGRAPH
            fragment_metadata = dict(fragment.get("source_metadata") or {})
            recovery_flags = list(fragment_metadata.get("recovery_flags") or [])
            fragment_metadata["recovery_flags"] = list(
                dict.fromkeys([*recovery_flags, "export_refresh_split_render_fragment"])
            )
            target_text = str(fragment.get("target_text") or "").strip() or None
            if target_text is None:
                target_text = render_repair.infer_refresh_split_fragment_target_text(
                    block,
                    fragment,
                    fragment_block_type=fragment_block_type,
                )

            render_mode = "zh_primary_with_optional_source"
            artifact_kind = None
            is_expected_source_only = False
            notice = None
            if fragment_block_type == BlockType.CODE:
                render_mode = "source_artifact_full_width"
                artifact_kind = "code"
                is_expected_source_only = True
                notice = "代码保持原样"

            fragment_block = replace(
                block,
                block_id=f"{block.block_id}::refresh-split::{index}",
                block_type=fragment_block_type.value,
                render_mode=render_mode,
                artifact_kind=artifact_kind,
                title=None,
                source_text=source_text,
                target_text=target_text,
                source_metadata=fragment_metadata,
                source_sentence_ids=[],
                target_segment_ids=[],
                is_expected_source_only=is_expected_source_only,
                notice=notice,
            )
            split_blocks = render_repair.split_mixed_book_code_prose_render_block(fragment_block)
            if len(split_blocks) != 1 or split_blocks[0].block_id != fragment_block.block_id:
                rendered.extend(split_blocks)
            else:
                rendered.append(fragment_block)
        return rendered

    def _should_clear_suspicious_short_source_target_text(self, block: MergedRenderBlock) -> bool:
        return render_repair.should_clear_suspicious_short_source_target_text(block)

    def _drop_render_block_target_text(
        self,
        block: MergedRenderBlock,
        *,
        flag: str,
    ) -> MergedRenderBlock:
        return render_repair.drop_render_block_target_text(block, flag=flag)

    def _split_academic_paper_frontmatter_block(self, block: MergedRenderBlock) -> list[MergedRenderBlock]:
        return render_repair.split_academic_paper_frontmatter_block(block)

    def _merge_render_text_fragments(self, previous_text: str, current_text: str) -> str:
        return render_repair.merge_render_text_fragments(previous_text, current_text)

    def _merge_adjacent_code_render_blocks(
        self,
        previous: MergedRenderBlock,
        current: MergedRenderBlock,
    ) -> MergedRenderBlock:
        return render_repair.merge_adjacent_code_render_blocks(previous, current)

    def _should_merge_adjacent_code_blocks(self, previous: MergedRenderBlock, current: MergedRenderBlock) -> bool:
        return render_repair.should_merge_adjacent_code_blocks(previous, current)

    def _join_block_target_text(
        self,
        target_texts: list[str],
        *,
        block_type: BlockType | None = None,
        render_mode: str | None = None,
        source_text: str | None = None,
    ) -> str:
        return markup.join_block_target_text(
            target_texts,
            block_type=block_type,
            render_mode=render_mode,
            source_text=source_text,
        )

    def _render_block_html(
        self,
        block: MergedRenderBlock,
        asset_path_by_block_id: dict[str, str] | None = None,
        *,
        include_source_toggle: bool = True,
        include_notice: bool = True,
        include_source_caption: bool = True,
    ) -> str:
        return markup.render_block_html(
            block,
            asset_path_by_block_id,
            include_source_toggle=include_source_toggle,
            include_notice=include_notice,
            include_source_caption=include_source_caption,
        )

    def _format_preformatted_text(
        self,
        text: str,
        *,
        block: MergedRenderBlock | None = None,
    ) -> str:
        return markup.format_preformatted_text(text, block=block)

    def _looks_like_stable_structured_code_layout(self, lines: list[str]) -> bool:
        return code_text.looks_like_stable_structured_code_layout(lines)

    def _render_equation_html_metadata_aware(
        self,
        source_metadata: dict[str, object] | None,
        source_text: str,
    ) -> str:
        return markup.render_equation_html_metadata_aware(source_metadata, source_text)

    def _render_table_html_metadata_aware(
        self,
        source_metadata: dict[str, object] | None,
        source_text: str,
    ) -> str | None:
        return markup.render_table_html_metadata_aware(source_metadata, source_text)

    def _markdown_table_to_html(self, markdown: str) -> str | None:
        return markup.markdown_table_to_html(markdown)

    def _render_equation_markdown_metadata_aware(
        self,
        source_metadata: dict[str, object] | None,
        source_text: str,
    ) -> str:
        return markup.render_equation_markdown_metadata_aware(source_metadata, source_text)

    def _render_table_markdown_metadata_aware(
        self,
        source_metadata: dict[str, object] | None,
        source_text: str,
    ) -> str | None:
        return markup.render_table_markdown_metadata_aware(source_metadata, source_text)

    def _looks_like_code_artifact_text(self, text: str, *, academic_paper: bool = False) -> bool:
        return code_text.looks_like_code_artifact_text(text, academic_paper=academic_paper)

    def _looks_like_single_line_codeish_text(self, text: str) -> bool:
        return code_text.looks_like_single_line_codeish_text(text)

    def _reflow_code_artifact_text(self, text: str) -> str:
        return code_text.reflow_code_artifact_text(text)

    def _build_bilingual_html(
        self,
        bundle: ChapterExportBundle,
        asset_path_by_block_id: dict[str, str] | None = None,
    ) -> str:
        render_blocks = self._render_blocks_for_chapter(bundle)
        chapter_title_target = titles.resolved_chapter_title_text(bundle, render_blocks)
        title_text = chapter_title_target or bundle.chapter.title_src or bundle.chapter.id
        source_title = (
            f"<div class='source-title'>{html.escape(bundle.chapter.title_src)}</div>"
            if chapter_title_target and bundle.chapter.title_src and chapter_title_target != bundle.chapter.title_src
            else ""
        )
        body_blocks = self._merged_body_blocks(
            list(render_blocks), str(chapter_title_target or "").strip()
        )
        blocks_html = "".join(
            markup.render_block_html(block, asset_path_by_block_id)
            for block in body_blocks
        )
        usage_html = self._build_usage_summary_html(list(bundle.translation_runs))
        if not blocks_html:
            blocks_html = "<div class='empty-state'>No renderable blocks in this chapter.</div>"
        return (
            "<html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{html.escape(str(title_text))}</title>"
            "<style>"
            f"{stylesheets.load('bilingual_chapter.css')}"
            f"{markup.usage_summary_css()}"
            "</style>"
            "<link rel='stylesheet' href='https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css'>"
            "</head><body>"
            "<main class='page'>"
            "<header class='hero'>"
            "<div class='hero-kicker'>Chapter Export</div>"
            f"<h1>{html.escape(str(title_text))}</h1>"
            f"{source_title}"
            "</header>"
            f"{usage_html}"
            "<section class='chapter'>"
            f"{blocks_html}"
            "</section>"
            "</main>"
            "<script src='https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js'></script>"
            "<script>"
            "document.querySelectorAll('.katex-source').forEach(el => {"
            "try { katex.render(el.textContent, el, {displayMode: true, throwOnError: false}); } catch(e) {}"
            "});"
            "</script>"
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
        materializations: list[DocumentImageMaterialization] = []
        exported_assets = self._export_epub_assets(
            bundle.document.source_type,
            bundle.document.source_path,
            render_blocks,
            output_dir,
            document_images=document_images,
            materializations=materializations,
        )
        apply_document_image_materializations(materializations)
        merged_assets = dict(persisted_assets)
        merged_assets.update(exported_assets)
        return merged_assets

    def _write_rebuilt_epub(
        self,
        bundle: DocumentExportBundle,
        file_path: Path,
        asset_path_by_block_id: dict[str, str] | None = None,
    ) -> None:
        visible_chapters = self._visible_merged_chapters(bundle)
        if not visible_chapters:
            raise ExportGateError("Rebuilt EPUB requires at least one visible chapter.")
        file_path.parent.mkdir(parents=True, exist_ok=True)

        chapter_entries: list[tuple[str, str, str]] = []
        for visible_ordinal, chapter_bundle, render_blocks, title_text in visible_chapters:
            chapter_name = f"text/chapter-{visible_ordinal:03d}.xhtml"
            chapter_entries.append(
                (
                    chapter_name,
                    str(title_text or chapter_bundle.chapter.title_tgt or chapter_bundle.chapter.title_src or f"Chapter {visible_ordinal}"),
                    markup.build_rebuilt_epub_chapter_xhtml(
                        chapter_bundle,
                        visible_ordinal=visible_ordinal,
                        title_text=title_text,
                        render_blocks=render_blocks,
                        asset_path_by_block_id=asset_path_by_block_id,
                    ),
                )
            )

        nav_items = "".join(
            f"<li><a href='{html.escape(filename)}'>{html.escape(title)}</a></li>"
            for filename, title, _content in chapter_entries
        )
        nav_xhtml = (
            "<?xml version='1.0' encoding='utf-8'?>"
            "<html xmlns='http://www.w3.org/1999/xhtml' xmlns:epub='http://www.idpf.org/2007/ops' xml:lang='zh-CN'>"
            "<head><title>Contents</title><meta charset='utf-8' />"
            "<link rel='stylesheet' type='text/css' href='styles/book.css' /></head>"
            f"<body><nav epub:type='toc' class='toc'><h1>Contents</h1><ol>{nav_items}</ol></nav></body></html>"
        )

        metadata_title = html.escape(document_display_title(bundle.document) or bundle.document.id)
        metadata_author = html.escape(_display_author_value(bundle.document.author) or "Unknown")
        modified_at = _utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

        asset_root = file_path.parent / "assets"
        asset_files = sorted(path for path in asset_root.rglob("*") if path.is_file()) if asset_root.exists() else []
        manifest_items = [
            ("nav", "nav.xhtml", "application/xhtml+xml", " properties='nav'"),
            ("css", "styles/book.css", "text/css", ""),
        ]
        manifest_items.extend(
            (f"chap-{index}", filename, "application/xhtml+xml", "")
            for index, (filename, _title, _content) in enumerate(chapter_entries, start=1)
        )
        for index, asset_file in enumerate(asset_files, start=1):
            relative_asset = asset_file.relative_to(asset_root).as_posix()
            media_type = mimetypes.guess_type(asset_file.name)[0] or "application/octet-stream"
            manifest_items.append((f"asset-{index}", f"assets/{relative_asset}", media_type, ""))

        content_opf = (
            "<?xml version='1.0' encoding='utf-8'?>"
            "<package xmlns='http://www.idpf.org/2007/opf' unique-identifier='BookId' version='3.0' xml:lang='zh-CN'>"
            "<metadata xmlns:dc='http://purl.org/dc/elements/1.1/'>"
            f"<dc:identifier id='BookId'>{html.escape(bundle.document.id)}</dc:identifier>"
            f"<dc:title>{metadata_title}</dc:title>"
            f"<dc:creator>{metadata_author}</dc:creator>"
            "<dc:language>zh-CN</dc:language>"
            f"<meta property='dcterms:modified'>{modified_at}</meta>"
            "</metadata>"
            "<manifest>"
            + "".join(
                f"<item id='{item_id}' href='{html.escape(href)}' media-type='{html.escape(media_type)}'{properties} />"
                for item_id, href, media_type, properties in manifest_items
            )
            + "</manifest>"
            "<spine>"
            + "".join(f"<itemref idref='chap-{index}' />" for index, _entry in enumerate(chapter_entries, start=1))
            + "</spine>"
            "</package>"
        )
        container_xml = (
            "<?xml version='1.0' encoding='utf-8'?>"
            "<container version='1.0' xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>"
            "<rootfiles><rootfile full-path='OEBPS/content.opf' media-type='application/oebps-package+xml' />"
            "</rootfiles></container>"
        )

        with zipfile.ZipFile(file_path, mode="w") as archive:
            archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            archive.writestr("META-INF/container.xml", container_xml, compress_type=zipfile.ZIP_DEFLATED)
            archive.writestr("OEBPS/content.opf", content_opf, compress_type=zipfile.ZIP_DEFLATED)
            archive.writestr("OEBPS/nav.xhtml", nav_xhtml, compress_type=zipfile.ZIP_DEFLATED)
            archive.writestr("OEBPS/styles/book.css", markup.build_rebuilt_epub_stylesheet(), compress_type=zipfile.ZIP_DEFLATED)
            for filename, _title, content in chapter_entries:
                archive.writestr(f"OEBPS/{filename}", content, compress_type=zipfile.ZIP_DEFLATED)
            for asset_file in asset_files:
                archive.write(asset_file, arcname=f"OEBPS/assets/{asset_file.relative_to(asset_root).as_posix()}")

    def _write_source_preserving_epub(
        self,
        bundle: DocumentExportBundle,
        file_path: Path,
    ) -> None:
        source_path = Path(bundle.document.source_path or "")
        if not source_path.exists():
            raise ExportGateError("Source-preserving EPUB export requires the original EPUB source file.")

        chapter_render_blocks: dict[str, list[MergedRenderBlock]] = {
            str((chapter_bundle.chapter.metadata_json or {}).get("href") or ""): self._render_blocks_for_chapter(
                chapter_bundle
            )
            for chapter_bundle in bundle.chapters
        }
        patch_sources: dict[str, dict[str, str]] = {}
        for chapter_href, render_blocks in chapter_render_blocks.items():
            for block in render_blocks:
                source_metadata = dict(block.source_metadata or {})
                if not block.target_text:
                    continue
                source_path_value = str(source_metadata.get("source_path") or chapter_href or "").strip()
                if source_path_value != chapter_href:
                    continue
                anchor = str(source_metadata.get("anchor") or "").strip()
                if not anchor:
                    continue
                patch_sources.setdefault(chapter_href, {})[anchor] = block.target_text

        file_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(source_path) as source_archive, zipfile.ZipFile(file_path, mode="w") as target_archive:
            for info in source_archive.infolist():
                raw = source_archive.read(info.filename)
                if info.filename.endswith((".xhtml", ".html", ".htm")):
                    raw = self._patch_source_preserving_epub_xhtml(
                        raw,
                        patch_sources.get(info.filename, {}),
                    )
                target_archive.writestr(info, raw)

    def _patch_source_preserving_epub_xhtml(
        self,
        raw: bytes,
        anchor_to_translation: dict[str, str],
    ) -> bytes:
        if not anchor_to_translation:
            return raw
        try:
            root = _parse_xml_document(raw)
        except ET.ParseError:
            return raw

        patched = False
        for anchor, translation in anchor_to_translation.items():
            element = epub_assets.find_epub_element_by_id(root, anchor)
            if element is None:
                continue
            if not _normalize_render_text("".join(element.itertext())):
                continue
            self._patch_epub_element_text(element, translation)
            patched = True

        if not patched:
            return raw
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)

    def _patch_epub_element_text(self, element: ET.Element, translation: str) -> None:
        element.text = translation
        for child in list(element):
            if child.attrib.get("href") or child.attrib.get("id"):
                self._clear_epub_descendant_text(child)
                child.tail = ""
                continue
            self._clear_epub_text_recursively(child)
            child.text = ""
            child.tail = ""
        for child in list(element):
            if child.attrib.get("href") or child.attrib.get("id"):
                child.tail = ""

    def _clear_epub_descendant_text(self, element: ET.Element) -> None:
        for child in list(element):
            self._clear_epub_text_recursively(child)
            child.tail = ""

    def _clear_epub_text_recursively(self, element: ET.Element) -> None:
        if element.text:
            element.text = ""
        for child in list(element):
            self._clear_epub_text_recursively(child)
            child.tail = ""

    def _apply_source_preserving_epub_status_updates(self, bundle: DocumentExportBundle) -> None:
        now = _utcnow()
        for chapter_bundle in bundle.chapters:
            chapter_bundle.chapter.status = ChapterStatus.EXPORTED
            chapter_bundle.chapter.updated_at = now
            self.repository.session.merge(chapter_bundle.chapter)
        bundle.document.status = DocumentStatus.EXPORTED
        bundle.document.updated_at = now
        self.repository.session.merge(bundle.document)

    def _render_rebuilt_pdf_from_html(self, html_path: Path, pdf_path: Path) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - exercised by runtime environment
            raise ExportGateError("Rebuilt PDF renderer is unavailable because Playwright is not installed.") from exc
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    page = browser.new_page()
                    page.emulate_media(media="print")
                    page.goto(html_path.resolve().as_uri(), wait_until="load")
                    page.pdf(path=str(pdf_path), print_background=True, format="A4")
                finally:
                    browser.close()
        except ExportGateError:
            raise
        except Exception as exc:  # pragma: no cover - depends on local browser runtime
            raise ExportGateError(
                "Rebuilt PDF renderer is unavailable or failed to render the merged HTML substrate."
            ) from exc

    def _export_epub_assets_for_chapter_bundle(
        self,
        bundle: ChapterExportBundle,
        output_dir: Path,
    ) -> dict[str, str]:
        persisted_assets = self._export_persisted_document_image_assets(bundle.document_images, output_dir)
        render_blocks = self._render_blocks_for_chapter(bundle)
        materializations: list[DocumentImageMaterialization] = []
        exported_assets = self._export_epub_assets(
            bundle.document.source_type,
            bundle.document.source_path,
            render_blocks,
            output_dir,
            document_images=bundle.document_images,
            materializations=materializations,
        )
        apply_document_image_materializations(materializations)
        merged_assets = dict(persisted_assets)
        merged_assets.update(exported_assets)
        return merged_assets

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

    _ASSET_FILENAME_SAFE_CHARS: str = (
        "abcdefghijklmnopqrstuvwxyz"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789"
        "-._~"
    )

    def _sanitize_asset_basename(self, name: str) -> str:
        cleaned = re.sub(r"\s+", "-", (name or "").strip()) or "asset"
        cleaned = "".join(
            ch if ch in self._ASSET_FILENAME_SAFE_CHARS else "-"
            for ch in cleaned
        )
        cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-.")
        return cleaned or "asset"

    def _allocate_flat_asset_filename(
        self,
        archive_path: str,
        name_assignments: dict[str, str],
    ) -> str:
        if archive_path in name_assignments:
            return name_assignments[archive_path]
        base = PurePosixPath(archive_path).name or "asset"
        suffix = PurePosixPath(base).suffix
        stem = base[: len(base) - len(suffix)] if suffix else base
        safe_stem = self._sanitize_asset_basename(stem)
        safe_suffix = suffix if re.fullmatch(r"\.[A-Za-z0-9]{1,10}", suffix or "") else ""
        candidate = f"{safe_stem}{safe_suffix}"
        taken = set(name_assignments.values())
        if candidate in taken:
            digest = hashlib.sha1(archive_path.encode("utf-8")).hexdigest()[:8]
            candidate = f"{safe_stem}-{digest}{safe_suffix}"
        name_assignments[archive_path] = candidate
        return candidate

    _ASSET_DIR_PRESERVED_SUBDIRS: frozenset[str] = frozenset({"document-images"})

    def _purge_legacy_nested_asset_dirs(self, asset_root: Path) -> None:
        if not asset_root.is_dir():
            return
        for child in asset_root.iterdir():
            if child.is_dir() and child.name not in self._ASSET_DIR_PRESERVED_SUBDIRS:
                shutil.rmtree(child, ignore_errors=True)

    def _export_epub_assets(
        self,
        source_type: SourceType,
        source_path: str | None,
        render_blocks: list[MergedRenderBlock],
        output_dir: Path,
        *,
        document_images: list[object] | None = None,
        materializations: list[DocumentImageMaterialization] | None = None,
    ) -> dict[str, str]:
        if not source_path:
            return {}

        if source_type == SourceType.EPUB:
            return self._export_epub_archive_assets(
                source_path,
                render_blocks,
                output_dir,
                document_images=document_images,
                materializations=materializations,
            )
        if source_type in {SourceType.PDF_TEXT, SourceType.PDF_MIXED, SourceType.PDF_SCAN}:
            return self._export_pdf_assets(
                source_path,
                render_blocks,
                output_dir,
                document_images=document_images,
                materializations=materializations,
            )
        return {}

    def _export_epub_archive_assets(
        self,
        source_path: str,
        render_blocks: list[MergedRenderBlock],
        output_dir: Path,
        *,
        document_images: list[object] | None = None,
        materializations: list[DocumentImageMaterialization] | None = None,
    ) -> dict[str, str]:
        """Copy figure images out of the source EPUB.

        Newly materialized DocumentImage files are reported in
        ``materializations`` for the caller to persist; rows are not modified.
        """
        epub_path = Path(source_path)
        if not epub_path.exists():
            return {}

        document_image_by_block_id = {
            str(getattr(image, "block_id", "")): image
            for image in (document_images or [])
            if getattr(image, "block_id", None)
        }
        archive_path_by_block_id: dict[str, str] = {}
        asset_root = output_dir / "assets"
        asset_root.mkdir(parents=True, exist_ok=True)
        self._purge_legacy_nested_asset_dirs(asset_root)
        relative_path_by_archive_path: dict[str, str] = {}
        flat_name_assignments: dict[str, str] = {}
        with zipfile.ZipFile(epub_path) as archive:
            legacy_figure_index_cache: dict[str, dict[str, str]] = {}
            for block in render_blocks:
                archive_path = epub_assets.safe_epub_archive_path(block.source_metadata.get("image_path"))
                if archive_path is None:
                    archive_path = epub_assets.recover_legacy_epub_figure_archive_path(
                        block,
                        archive,
                        cache=legacy_figure_index_cache,
                    )
                if archive_path is None:
                    continue
                archive_path_by_block_id[block.block_id] = archive_path
            if not archive_path_by_block_id:
                return {}

            for archive_path in sorted(set(archive_path_by_block_id.values())):
                try:
                    archive_info = archive.getinfo(archive_path)
                except KeyError:
                    continue
                flat_name = self._allocate_flat_asset_filename(
                    archive_path, flat_name_assignments
                )
                target_path = asset_root / flat_name
                if not target_path.exists():
                    with archive.open(archive_info) as source_handle, target_path.open("wb") as target_handle:
                        shutil.copyfileobj(source_handle, target_handle)
                relative_path_by_archive_path[archive_path] = PurePosixPath(
                    "assets", flat_name
                ).as_posix()

            for block_id, archive_path in archive_path_by_block_id.items():
                persisted_image = document_image_by_block_id.get(block_id)
                if persisted_image is None:
                    continue
                try:
                    archive_info = archive.getinfo(archive_path)
                except KeyError:
                    continue
                asset_suffix = pdf_crop.normalize_asset_extension(PurePosixPath(archive_path).suffix or ".bin")
                materialized_path = self._materialized_document_image_path(
                    persisted_image,
                    suffix=asset_suffix,
                )
                needs_refresh = pdf_crop.document_image_needs_refresh(
                    persisted_image,
                    expected_vias={"epub_archive_asset"},
                )
                if not materialized_path.exists() or needs_refresh:
                    materialized_path.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(archive_info) as source_handle, materialized_path.open("wb") as target_handle:
                        shutil.copyfileobj(source_handle, target_handle)
                    if materializations is not None:
                        materializations.append(
                            plan_document_image_materialization(
                                persisted_image,
                                materialized_path,
                                materialized_via="epub_archive_asset",
                            )
                        )

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
        materializations: list[DocumentImageMaterialization] | None = None,
    ) -> dict[str, str]:
        """Crop or extract figure images from the source PDF.

        Newly materialized DocumentImage files are reported in
        ``materializations`` for the caller to persist; rows are not modified.
        """
        pdf_path = Path(source_path)
        if not pdf_path.exists():
            return {}

        document_image_by_block_id = {
            str(getattr(image, "block_id", "")): image
            for image in (document_images or [])
            if getattr(image, "block_id", None)
        }
        render_blocks_by_id = {block.block_id: block for block in render_blocks}
        relative_path_by_block_id: dict[str, str] = {}

        # Fast path: use images materialized at parse time via image_path.
        asset_root = output_dir / "assets" / "pdf-images"
        remaining_blocks: list[MergedRenderBlock] = []
        for block in render_blocks:
            if block.artifact_kind not in {"image", "figure"}:
                continue
            materialized_src = str(block.source_metadata.get("image_path") or "").strip()
            if materialized_src and Path(materialized_src).is_file():
                asset_root.mkdir(parents=True, exist_ok=True)
                suffix = Path(materialized_src).suffix or ".png"
                target_path = asset_root / f"{block.block_id}{suffix}"
                if not target_path.exists():
                    shutil.copy2(materialized_src, target_path)
                relative_path_by_block_id[block.block_id] = f"assets/pdf-images/{block.block_id}{suffix}"
            else:
                remaining_blocks.append(block)

        pdf_image_specs: dict[str, tuple[str, int, list[float]]] = {}
        for block in remaining_blocks:
            crop_spec = pdf_crop.pdf_asset_crop_spec(block)
            if crop_spec is None:
                continue
            pdf_image_specs[block.block_id] = crop_spec

        if not pdf_image_specs:
            return relative_path_by_block_id

        try:
            import fitz
        except ImportError:
            return relative_path_by_block_id

        asset_root.mkdir(parents=True, exist_ok=True)
        document = fitz.open(str(pdf_path))
        try:
            for block_id, (spec_kind, page_number, bbox) in pdf_image_specs.items():
                if page_number < 1 or page_number > document.page_count:
                    continue
                page = document.load_page(page_number - 1)
                resolved_bbox = (
                    pdf_crop.caption_anchored_pdf_crop_bbox(page, bbox)
                    if spec_kind == "caption_anchor"
                    else pdf_crop.layout_guided_pdf_crop_bbox(page, bbox)
                )
                if resolved_bbox is None:
                    continue
                rect = fitz.Rect(*resolved_bbox)
                if rect.width <= 1 or rect.height <= 1:
                    continue
                persisted_image = document_image_by_block_id.get(block_id)
                original_pdf_asset = pdf_crop.probe_pdf_original_asset(document, page, rect)
                original_pdf_image = (
                    (original_pdf_asset["image_bytes"], original_pdf_asset["extension"])
                    if isinstance(original_pdf_asset.get("image_bytes"), (bytes, bytearray))
                    and isinstance(original_pdf_asset.get("extension"), str)
                    else None
                )
                asset_suffix = pdf_crop.normalize_asset_extension(
                    str(original_pdf_asset.get("extension") or "")
                    if original_pdf_image is not None
                    else pdf_crop.document_image_asset_suffix(persisted_image, default_ext=".png")
                )
                target_path = asset_root / f"{block_id}{asset_suffix}"
                desired_width_px, desired_height_px = pdf_crop.preferred_pdf_crop_pixel_size(
                    render_blocks_by_id.get(block_id),
                    persisted_image,
                )
                if persisted_image is not None:
                    materialized_path = self._materialized_document_image_path(
                        persisted_image,
                        suffix=asset_suffix,
                    )
                    needs_refresh = pdf_crop.document_image_needs_refresh(
                        persisted_image,
                        expected_vias={"pdf_export_crop", "pdf_original_image"},
                    )
                    if not materialized_path.exists() or needs_refresh:
                        materialized_via, render_scale, original_asset_availability = pdf_crop.save_pdf_asset(
                            document,
                            page,
                            rect,
                            materialized_path,
                            original_asset=original_pdf_asset,
                            desired_width_px=desired_width_px,
                            desired_height_px=desired_height_px,
                        )
                        if materializations is not None:
                            materializations.append(
                                plan_document_image_materialization(
                                    persisted_image,
                                    materialized_path,
                                    materialized_via=materialized_via,
                                    render_scale=render_scale,
                                    original_asset_availability=original_asset_availability,
                                )
                            )
                    if not target_path.exists():
                        shutil.copy2(materialized_path, target_path)
                elif not target_path.exists():
                    pdf_crop.save_pdf_asset(
                        document,
                        page,
                        rect,
                        target_path,
                        original_asset=original_pdf_asset,
                        desired_width_px=desired_width_px,
                        desired_height_px=desired_height_px,
                    )
                relative_path_by_block_id[block_id] = PurePosixPath(
                    "assets",
                    "pdf-images",
                    target_path.name,
                ).as_posix()
        finally:
            document.close()

        return relative_path_by_block_id

    def _pdf_asset_crop_spec(self, block: MergedRenderBlock) -> tuple[str, int, list[float]] | None:
        return pdf_crop.pdf_asset_crop_spec(block)

    def _caption_anchored_pdf_crop_bbox(self, page: object, caption_bbox: list[float]) -> list[float] | None:
        return pdf_crop.caption_anchored_pdf_crop_bbox(page, caption_bbox)

    def _layout_guided_pdf_crop_bbox(self, page: object, seed_bbox: list[float]) -> list[float] | None:
        return pdf_crop.layout_guided_pdf_crop_bbox(page, seed_bbox)

    def _probe_pdf_original_asset(
        self,
        document: object,
        page: object,
        rect: object,
    ) -> dict[str, object]:
        return pdf_crop.probe_pdf_original_asset(document, page, rect)

    def _materialized_document_image_path(self, document_image: object, *, suffix: str | None = None) -> Path:
        document_id = str(getattr(document_image, "document_id"))
        block_id = str(getattr(document_image, "block_id"))
        asset_suffix = pdf_crop.normalize_asset_extension(
            suffix or pdf_crop.document_image_asset_suffix(document_image, default_ext=".png")
        )
        return (self.output_root.parent / "document-images" / document_id / f"{block_id}{asset_suffix}").resolve()



@dataclass(frozen=True, slots=True)
class DocumentRenderer:
    """How one whole-document export type is rendered, described, and recorded.

    ``render`` writes ``file_path`` and returns the rendered text when the
    export also gets a human-titled alias copy; ``manifest`` builds the
    manifest payload.
    """

    export_type: ExportType
    file_name: str
    manifest_name: str
    render: Callable[[ExportService, DocumentExportBundle, Path, Path, dict], str | None]
    manifest: Callable[[ExportService, DocumentExportBundle, Path, dict], dict]
    epub_source_only_error: str | None = None
    uses_upstream_exports: bool = False
    syncs_document_title: bool = True
    apply_status_updates: Callable[[ExportService, DocumentExportBundle], None] | None = None


def _render_merged(builder_name: str) -> Callable[[ExportService, DocumentExportBundle, Path, Path, dict], str]:
    def render(service: ExportService, bundle: DocumentExportBundle, output_dir: Path, file_path: Path, _upstream: dict) -> str:
        asset_path_by_block_id = service._export_epub_assets_for_document_bundle(bundle, output_dir)
        text = getattr(service, builder_name)(bundle, asset_path_by_block_id)
        file_path.write_text(text, encoding="utf-8")
        return text

    return render


def _render_rebuilt_epub(
    service: ExportService, bundle: DocumentExportBundle, output_dir: Path, file_path: Path, _upstream: dict
) -> None:
    asset_path_by_block_id = service._export_epub_assets_for_document_bundle(bundle, output_dir)
    service._write_rebuilt_epub(bundle, file_path, asset_path_by_block_id)
    return None


def _render_zh_epub(
    service: ExportService, bundle: DocumentExportBundle, _output_dir: Path, file_path: Path, _upstream: dict
) -> None:
    service._write_source_preserving_epub(bundle, file_path)
    return None


def _render_rebuilt_pdf(
    service: ExportService, _bundle: DocumentExportBundle, _output_dir: Path, file_path: Path, upstream: dict
) -> None:
    service._render_rebuilt_pdf_from_html(upstream[ExportType.MERGED_HTML].file_path, file_path)
    return None


def _merged_manifest(export_type: ExportType | None):
    def manifest(service: ExportService, bundle: DocumentExportBundle, file_path: Path, _upstream: dict) -> dict:
        if export_type is None:
            return service._build_merged_document_manifest(bundle, file_path)
        return service._build_merged_document_manifest(bundle, file_path, export_type=export_type)

    return manifest


def _rebuilt_manifest(export_type: ExportType, renderer_kind: str, expected_limitations: list[str], *, upstream: bool):
    def manifest(service: ExportService, bundle: DocumentExportBundle, file_path: Path, upstream_exports: dict) -> dict:
        return service._build_rebuilt_document_manifest(
            bundle,
            file_path,
            export_type=export_type,
            renderer_kind=renderer_kind,
            derived_from_exports=upstream_exports if upstream else {},
            expected_limitations=expected_limitations,
        )

    return manifest


_DOCUMENT_RENDERERS: dict[ExportType, DocumentRenderer] = {
    ExportType.MERGED_HTML: DocumentRenderer(
        export_type=ExportType.MERGED_HTML,
        file_name="merged-document.html",
        manifest_name="merged-document.manifest.json",
        render=_render_merged("_build_merged_document_html"),
        manifest=_merged_manifest(None),
        apply_status_updates=lambda service, bundle: service._apply_document_export_status_updates(
            bundle, ExportType.MERGED_HTML
        ),
    ),
    ExportType.MERGED_MARKDOWN: DocumentRenderer(
        export_type=ExportType.MERGED_MARKDOWN,
        file_name="merged-document.md",
        manifest_name="merged-document.markdown.manifest.json",
        render=_render_merged("_build_merged_document_markdown"),
        manifest=_merged_manifest(ExportType.MERGED_MARKDOWN),
        apply_status_updates=lambda service, bundle: service._apply_document_export_status_updates(
            bundle, ExportType.MERGED_MARKDOWN
        ),
    ),
    ExportType.REBUILT_EPUB: DocumentRenderer(
        export_type=ExportType.REBUILT_EPUB,
        file_name="rebuilt-document.epub",
        manifest_name="rebuilt-document.epub.manifest.json",
        render=_render_rebuilt_epub,
        manifest=_rebuilt_manifest(
            ExportType.REBUILT_EPUB,
            "epub_spine_rebuilder",
            [
                "assets_reused_from_source_when_available",
                "no_in_image_text_rewrite",
                "single_document_level_output_only",
            ],
            upstream=True,
        ),
        epub_source_only_error="Rebuilt EPUB is only available for EPUB source documents.",
        uses_upstream_exports=True,
    ),
    ExportType.ZH_EPUB: DocumentRenderer(
        export_type=ExportType.ZH_EPUB,
        file_name="zh-document.epub",
        manifest_name="zh-document.epub.manifest.json",
        render=_render_zh_epub,
        manifest=_rebuilt_manifest(
            ExportType.ZH_EPUB,
            "source_preserving_epub_patcher",
            [
                "source_archive_structure_preserved",
                "nav_and_anchors_preserved",
                "only_leaf_xhtml_nodes_patched",
            ],
            upstream=False,
        ),
        epub_source_only_error="Source-preserving EPUB export is only available for EPUB source documents.",
        syncs_document_title=False,
        apply_status_updates=lambda service, bundle: service._apply_source_preserving_epub_status_updates(bundle),
    ),
    ExportType.REBUILT_PDF: DocumentRenderer(
        export_type=ExportType.REBUILT_PDF,
        file_name="rebuilt-document.pdf",
        manifest_name="rebuilt-document.pdf.manifest.json",
        render=_render_rebuilt_pdf,
        manifest=_rebuilt_manifest(
            ExportType.REBUILT_PDF,
            "html_print_renderer",
            [
                "not_page_faithful_to_source_pdf",
                "assets_reused_from_source_when_available",
                "no_in_image_text_rewrite",
                "single_document_level_output_only",
            ],
            upstream=True,
        ),
        uses_upstream_exports=True,
    ),
}
