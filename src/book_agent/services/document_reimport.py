"""Importing a book again with the current parser, before anything is translated.

A parse problem the health check finds (chapters split against the bookmarks, say) is fixed in the
parser; a book imported before the fix keeps its old parse. While nothing of it is translated, the
book can simply be imported again from its uploaded file: the document id comes from the file's
content, so it keeps its id and page. Once a pilot or translation has started the old parse holds
translations and is not thrown away (chapter_reparse covers what can be refreshed in place).
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from book_agent.domain.enums import DocumentRunType, SourceType
from book_agent.domain.models import Chapter, Document
from book_agent.domain.models.ops import DocumentRun
from book_agent.domain.models.translation import TargetSegment


def reimport_refusal(session: Session, document: Document) -> str | None:
    """Why the book cannot be imported again now (None: it can)."""
    if document.source_type == SourceType.WEB:
        return "网页不能重新导入；重新添加这个网址即可。"
    if not document.source_path or not Path(document.source_path).is_file():
        return "找不到这本书当初上传的文件，不能重新导入；可以在书库删除后重新上传。"
    # Importing and exporting leave no translations; a pilot, translation, review or repair run does.
    started = session.scalar(
        select(
            exists().where(
                DocumentRun.document_id == document.id,
                DocumentRun.run_type.not_in((DocumentRunType.BOOTSTRAP, DocumentRunType.EXPORT_FULL)),
            )
        )
    )
    translated = session.scalar(
        select(
            exists().where(
                TargetSegment.chapter_id.in_(select(Chapter.id).where(Chapter.document_id == document.id))
            )
        )
    )
    if started or translated:
        return "这本书已经开始试译或翻译，重新导入会丢掉已有的译文，所以不提供。"
    return None
