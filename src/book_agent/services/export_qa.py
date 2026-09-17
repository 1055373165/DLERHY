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
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
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
from book_agent.export.qa import ERROR, WARNING, QaCheck, audit_html, empty_block_check, heading_hierarchy_check, untranslated_ratio_check
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
    ) -> QaAuditResult:
        """``artifacts`` are (chapter_id or None for the whole document, html path)."""
        result = QaAuditResult(export_type=export_type.value)
        if export_type not in AUDITED_EXPORT_TYPES:
            return result
        detected: list[ReviewIssue] = []
        audited_scopes: list[str | None] = []
        for chapter_id, html_path in artifacts:
            if not html_path.is_file():
                continue
            audited_scopes.append(chapter_id)
            checks = self._checks(document_id, chapter_id, export_type, html_path.read_text(encoding="utf-8"))
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

    def _checks(self, document_id: str, chapter_id: str | None, export_type: ExportType, html: str) -> list[QaCheck]:
        image_blocks = self._image_block_count(document_id, chapter_id)
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
        translatable, untranslated = self._untranslated(document_id, chapter_id)
        checks.append(untranslated_ratio_check(translatable, untranslated))
        return checks

    def _image_block_count(self, document_id: str, chapter_id: str | None) -> int:
        stmt = (
            select(Block.id)
            .join(Chapter, Chapter.id == Block.chapter_id)
            .where(
                Chapter.document_id == document_id,
                Block.status == ArtifactStatus.ACTIVE,
                Block.block_type.in_([BlockType.IMAGE, BlockType.FIGURE]),
            )
        )
        if chapter_id:
            stmt = stmt.where(Block.chapter_id == chapter_id)
        return len(self.session.scalars(stmt).all())

    def _untranslated(self, document_id: str, chapter_id: str | None) -> tuple[int, int]:
        stmt = select(Sentence.id).where(
            Sentence.document_id == document_id,
            Sentence.translatable.is_(True),
            Sentence.retired_by_revision_id.is_(None),
        )
        if chapter_id:
            stmt = stmt.where(Sentence.chapter_id == chapter_id)
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
