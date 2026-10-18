"""Importing a book again with the current parser, before anything is translated.

A parse problem the health check finds (chapters split against the bookmarks, say) is fixed in the
parser; a book imported before the fix keeps its old parse. While nothing of it is translated, the
book can simply be imported again from its uploaded file: the document id comes from the file's
content, so it keeps its id and page.

After the free pilot alone it can still be imported again: the pilot is where a parse defect
shows (a running head read as a footnote, a sidebar taken for a picture), and its two chapters
are cheap to redo. Re-importing drops the pilot (its runs and translations go with the old parse)
and the book can be piloted again. Once the whole book has started, or anything outside the
pilot's chapters is translated, the old parse holds work and is not thrown away
(chapter_reparse covers what can be refreshed in place).
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from book_agent.domain.enums import DocumentRunStatus, DocumentRunType, SourceType
from book_agent.domain.models import Chapter, Document
from book_agent.domain.models.ops import DocumentRun
from book_agent.domain.models.translation import TargetSegment


_ACTIVE_RUN_STATUSES = (
    DocumentRunStatus.QUEUED,
    DocumentRunStatus.RUNNING,
    DocumentRunStatus.PAUSED,
    DocumentRunStatus.DRAINING,
)


def _pilot_runs(session: Session, document_id: str) -> list[DocumentRun]:
    from book_agent.services.billing_category import PILOT

    return list(
        session.scalars(
            select(DocumentRun).where(
                DocumentRun.document_id == document_id,
                DocumentRun.run_type == DocumentRunType.TRANSLATE_FULL,
                DocumentRun.billing_category == PILOT,
            )
        ).all()
    )


def reimport_discards_pilot(session: Session, document: Document) -> bool:
    """Whether importing again throws a pilot away (the book is then piloted again)."""
    return bool(_pilot_runs(session, document.id))


def reimport_refusal(session: Session, document: Document) -> str | None:
    """Why the book cannot be imported again now (None: it can)."""
    from book_agent.services.pilot_quality import pilot_chapter_ids

    if document.source_type == SourceType.WEB:
        return "网页不能重新导入；重新添加这个网址即可。"
    if not document.source_path or not Path(document.source_path).is_file():
        return "找不到这本书当初上传的文件，不能重新导入；可以在书库删除后重新上传。"
    if session.scalar(
        select(exists().where(DocumentRun.document_id == document.id, DocumentRun.status.in_(_ACTIVE_RUN_STATUSES)))
    ):
        return "这本书还有任务在进行，等它结束后再重新导入。"
    pilots = _pilot_runs(session, document.id)
    # Importing and exporting leave no translations, nor does the pilot count here; a whole-book
    # translation, review or repair run does.
    started = session.scalar(
        select(
            exists().where(
                DocumentRun.document_id == document.id,
                DocumentRun.run_type.not_in((DocumentRunType.BOOTSTRAP, DocumentRunType.EXPORT_FULL)),
                DocumentRun.id.not_in([run.id for run in pilots]),
                # A run stopped or failed before translating anything leaves nothing to lose (How to
                # Swing Trade's whole-book run, cancelled on a book imported with no chapters); what
                # one did translate, the check below still finds.
                DocumentRun.status.not_in((DocumentRunStatus.CANCELLED, DocumentRunStatus.FAILED)),
            )
        )
    )
    pilot_chapters = pilot_chapter_ids(session, document.id) if pilots else []
    translated = session.scalar(
        select(
            exists().where(
                TargetSegment.chapter_id.in_(select(Chapter.id).where(Chapter.document_id == document.id)),
                TargetSegment.chapter_id.not_in(pilot_chapters),
            )
        )
    )
    if started or translated:
        return "这本书已经开始翻译全书，重新导入会丢掉已有的译文，所以不提供。"
    return None
