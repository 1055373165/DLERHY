"""Export QA sensor: audit rendered HTML exports and keep the findings in the issue ledger.

Runs after run-based HTML exports (bilingual per chapter, merged document).
Checks come from ``book_agent.export.qa``: R2-R5 structural rules, heading
hierarchy, empty blocks, image coverage against the chapter's image blocks,
and the share of translatable sentences without a current translation.

Every failed check becomes a non-blocking ``EXPORT_QA_FAILURE`` issue
(root cause EXPORT, one per export type, chapter and check) written through
``ReviewRepository.sync_issues``; a later audit that passes resolves it, and
human decisions stay. A JSON report is written next to the export. QA never
fails the run: it is a sensor for people and agents, not a gate.

An export scoped to some chapters (a pilot's two) is judged on those chapters only: the
untranslated share, the image count and the expected chapter sections of the document-level
checks come from ``chapter_ids``, not from the whole book.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from collections.abc import Collection
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.core.ids import stable_id
from book_agent.domain.enums import (
    ArtifactStatus,
    BlockType,
    Detector,
    ExportType,
    IssueStatus,
    RootCauseLayer,
    Severity,
)
from book_agent.domain.models import Block, Chapter, Sentence
from book_agent.domain.models.review import ReviewIssue
from book_agent.export.atomic import atomic_write_text
from book_agent.export.qa import (
    ERROR,
    WARNING,
    QaCheck,
    audit_html,
    chapter_sections_check,
    empty_block_check,
    heading_hierarchy_check,
    untranslated_ratio_check,
)
from book_agent.infra.repositories.review import ReviewRepository, active_target_texts
from book_agent.orchestrator.rule_engine import build_issue_action

ISSUE_TYPE = "EXPORT_QA_FAILURE"
AUDITED_EXPORT_TYPES = frozenset({ExportType.BILINGUAL_HTML, ExportType.MERGED_HTML})
# A merged document legitimately has many h2 (one per section of every chapter).
_MAX_H2_BY_TYPE = {ExportType.BILINGUAL_HTML: 60, ExportType.MERGED_HTML: 2000}


@dataclass(slots=True)
class QaAuditResult:
    export_type: str
    report_paths: list[str] = field(default_factory=list)
    failed_checks: list[dict[str, Any]] = field(default_factory=list)
    opened_issue_ids: list[str] = field(default_factory=list)
    resolved_issue_ids: list[str] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return not self.failed_checks

    def to_json(self) -> dict[str, Any]:
        return {
            "export_type": self.export_type,
            "all_ok": self.all_ok,
            "report_paths": list(self.report_paths),
            "failed_checks": list(self.failed_checks),
            "opened_issue_count": len(self.opened_issue_ids),
            "resolved_issue_count": len(self.resolved_issue_ids),
        }


class ExportQaService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def audit(
        self,
        document_id: str,
        export_type: ExportType,
        artifacts: list[tuple[str | None, Path]],
        *,
        chapter_ids: Collection[str] | None = None,
    ) -> QaAuditResult:
        """``artifacts`` are (chapter_id or None for the whole document, html path).

        ``chapter_ids``: the chapters the export holds when it is scoped (a pilot); None is the whole book.
        """
        scope = [str(chapter_id) for chapter_id in chapter_ids] if chapter_ids is not None else None
        result = QaAuditResult(export_type=export_type.value)
        if export_type not in AUDITED_EXPORT_TYPES:
            return result
        detected: list[ReviewIssue] = []
        audited_scopes: list[str | None] = []
        for chapter_id, html_path in artifacts:
            if not html_path.is_file():
                continue
            audited_scopes.append(chapter_id)
            checks = self._checks(document_id, chapter_id, export_type, html_path.read_text(encoding="utf-8"), scope)
            report_path = html_path.with_name(html_path.stem + ".qa.json")
            atomic_write_text(
                report_path,
                json.dumps(
                    {
                        "document_id": document_id,
                        "chapter_id": chapter_id,
                        "export_type": export_type.value,
                        "html_path": str(html_path),
                        "all_ok": all(check.ok for check in checks),
                        "checks": [check.to_json() for check in checks],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            result.report_paths.append(str(report_path))
            for check in checks:
                if check.ok:
                    continue
                result.failed_checks.append({"chapter_id": chapter_id, **check.to_json()})
                detected.append(self._issue(document_id, chapter_id, export_type, check, html_path))
        owned = [
            issue
            for issue in self.session.scalars(
                select(ReviewIssue).where(
                    ReviewIssue.document_id == document_id,
                    ReviewIssue.issue_type == ISSUE_TYPE,
                    ReviewIssue.status.in_([IssueStatus.OPEN, IssueStatus.TRIAGED]),
                )
            ).all()
            if (issue.evidence_json or {}).get("export_type") == export_type.value and issue.chapter_id in audited_scopes
        ]
        sync = ReviewRepository(self.session).sync_issues(
            detected,
            [build_issue_action(issue) for issue in detected],
            owned_existing=owned,
            resolution_note="Resolved by the latest export QA audit.",
            actor_id="services.export_qa",
        )
        result.opened_issue_ids = [issue.id for issue in sync.opened + sync.reopened]
        result.resolved_issue_ids = [issue.id for issue in sync.resolved]
        return result

    def _checks(
        self,
        document_id: str,
        chapter_id: str | None,
        export_type: ExportType,
        html: str,
        scope: list[str] | None = None,
    ) -> list[QaCheck]:
        image_blocks = self._image_block_count(document_id, chapter_id, scope)
        report = audit_html(
            html,
            max_h2=_MAX_H2_BY_TYPE.get(export_type, 60),
            min_img_coverage=image_blocks if image_blocks else None,
        )
        checks = [check for check in report.checks if check.name != "R6 image_render_coverage"]
        if image_blocks:
            coverage = next(check for check in report.checks if check.name == "R6 image_render_coverage")
            # Parser-detected images can legitimately render as placeholders; flag, do not fail hard.
            coverage.severity = WARNING
            checks.append(coverage)
        checks.append(heading_hierarchy_check(html))
        checks.append(empty_block_check(html))
        translatable, untranslated = self._untranslated(document_id, chapter_id, scope)
        checks.append(untranslated_ratio_check(translatable, untranslated))
        if export_type == ExportType.MERGED_HTML and chapter_id is None:
            checks.append(self._chapter_sections(document_id, html, scope))
        return checks

    def _chapter_sections(self, document_id: str, html: str, scope: list[str] | None = None) -> QaCheck:
        """The merged book folds continuation fragments into the chapter before them; a real chapter must not be."""
        from book_agent.export import titles

        expected: list[Chapter] = []
        statement = select(Chapter).where(Chapter.document_id == document_id).order_by(Chapter.ordinal)
        if scope is not None:
            statement = statement.where(Chapter.id.in_(scope or ["-"]))
        for chapter in self.session.scalars(statement).all():
            source_title = str(chapter.title_src or "").strip()
            chapter_like = (
                titles.extract_main_chapter_number(source_title) is not None
                or titles.looks_like_frontmatter_title(source_title)
                or titles.looks_like_appendix_title(source_title)
            )
            has_text = self.session.scalar(
                select(Sentence.id)
                .where(Sentence.chapter_id == chapter.id, Sentence.translatable.is_(True), Sentence.retired_by_revision_id.is_(None))
                .limit(1)
            )
            if chapter_like and has_text:
                expected.append(chapter)
        missing = [str(chapter.title_src) for chapter in expected if f"id='chapter-{chapter.id}'" not in html]
        return chapter_sections_check(missing, len(expected))

    def _image_block_count(self, document_id: str, chapter_id: str | None, scope: list[str] | None = None) -> int:
        """Pictures a reader should see: inline icons, hairline fragments and blocks a repair hid
        are not rendered on purpose, so they do not count against the export."""
        from book_agent.domain.structure.parse_health import HealthBlock, image_fragment_kind, is_inline_icon

        stmt = (
            select(Block)
            .join(Chapter, Chapter.id == Block.chapter_id)
            .where(
                Chapter.document_id == document_id,
                Block.status == ArtifactStatus.ACTIVE,
                Block.block_type.in_([BlockType.IMAGE, BlockType.FIGURE]),
            )
        )
        if chapter_id:
            stmt = stmt.where(Block.chapter_id == chapter_id)
        elif scope is not None:
            stmt = stmt.where(Block.chapter_id.in_(scope or ["-"]))
        count = 0
        for block in self.session.scalars(stmt).all():
            span = dict(block.source_span_json or {})
            if span.get("repair_hidden_from_export"):
                continue
            bbox = HealthBlock(
                id=block.id, chapter_id=block.chapter_id, ordinal=block.ordinal, block_type=block.block_type.value,
                source_text=block.source_text or "", source_span_json=span,
            ).bbox
            if bbox is not None and (is_inline_icon(bbox) or image_fragment_kind(bbox) is not None):
                continue
            count += 1
        return count

    def _untranslated(self, document_id: str, chapter_id: str | None, scope: list[str] | None = None) -> tuple[int, int]:
        stmt = select(Sentence.id).where(
            Sentence.document_id == document_id,
            Sentence.translatable.is_(True),
            Sentence.retired_by_revision_id.is_(None),
        )
        if chapter_id:
            stmt = stmt.where(Sentence.chapter_id == chapter_id)
        elif scope is not None:
            stmt = stmt.where(Sentence.chapter_id.in_(scope or ["-"]))
        sentence_ids = list(self.session.scalars(stmt).all())
        translated = active_target_texts(self.session, sentence_ids)
        return len(sentence_ids), sum(1 for sentence_id in sentence_ids if sentence_id not in translated)

    def _issue(
        self,
        document_id: str,
        chapter_id: str | None,
        export_type: ExportType,
        check: QaCheck,
        html_path: Path,
    ) -> ReviewIssue:
        return ReviewIssue(
            id=stable_id("review-issue", document_id, chapter_id or "document", ISSUE_TYPE, export_type.value, check.name),
            document_id=document_id,
            chapter_id=chapter_id,
            issue_type=ISSUE_TYPE,
            root_cause_layer=RootCauseLayer.EXPORT,
            severity=Severity.HIGH if check.severity == ERROR else Severity.MEDIUM,
            blocking=False,
            detector=Detector.RULE,
            confidence=1.0,
            evidence_json={
                "reason": "export_qa",
                "export_type": export_type.value,
                "check": check.name,
                "detail": check.detail,
                "html_path": str(html_path),
                **({"data": check.data} if check.data else {}),
            },
            status=IssueStatus.OPEN,
        )
