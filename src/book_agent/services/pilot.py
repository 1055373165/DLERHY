"""The free pilot: the first two chapters, translated through the full pipeline, before the reader pays.

docs/agent-upgrade/08-pilot-quality-gate.md §1, §5. After import the reader can
have the first two chapters translated for free (terminology, translation,
polish, model review, rule review, exports), read them, see the problems that
are left, and then decide whether to pay for the whole book.

Which chapters. The pilot shows the real quality of the book, so it takes body
chapters, not what surrounds them:

1. Chapters are candidates when they have something to translate (packets).
   Front matter (cover, contents, preface, acknowledgments, "about ..."), Part
   dividers, appendices, index and references never count.
2. When the book numbers its chapters ("Chapter 3", "3 Indexing pipeline",
   "3. ...", "第3章"), the pilot is the first two numbered chapters, even when
   chapter 1 is an introduction (the user's decision: it often is, and chapter
   2 then shows code, tables and figures).
3. Without two numbered chapters (many EPUBs title chapters by name only), the
   pilot is the first two remaining body chapters; a chapter titled
   "Introduction" counts there, because in such books it usually is chapter 1.
   Chapters too short to show anything (under ``MIN_BODY_CHARS``, e.g. an
   epigraph page) are passed over.

When there is no pilot. Web pages are one short article: translating it costs
about as much as a pilot, so they go straight to the full translation. Books
whose pilot would be most of the book (``MAX_PILOT_SHARE``) do too; so do books
with no body chapter at all.

The pilot run is an ordinary TRANSLATE_FULL run scoped to those chapters
(``orchestrator/run_plan.py``, scope "pilot") with billing category "pilot"
(``services/billing_category.py``): it is not charged. A book gets one pilot;
retrying or resuming it continues the same one.

After the pilot, the whole-book run reuses it: its translated packets are not
translated again (the translate stage only takes packets still waiting), the
glossary and BOOK.md decisions it made stay the book's, its terminology stage
already read the whole book so the full run skips that stage, and the Reviewer
Agent does not read the pilot's chapters a second time
(``whole_book_request``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from book_agent.domain.enums import DocumentRunStatus, DocumentRunType, SourceType
from book_agent.domain.models import Chapter, Document, Sentence
from book_agent.domain.models.ops import DocumentRun
from book_agent.domain.models.translation import TranslationPacket
from book_agent.orchestrator.pipeline_stage_cache import read_cached_stages
from book_agent.orchestrator.run_plan import (
    CHAPTER_IDS_REQUEST_KEY,
    FAST_READING_REQUEST_KEY,
    MODEL_REVIEWED_CHAPTERS_REQUEST_KEY,
    PACKET_IDS_REQUEST_KEY,
    RUN_REQUEST_KEY,
    SCOPE_PILOT,
    SCOPE_REQUEST_KEY,
    TERMINOLOGY_STAGE,
    MODEL_REVIEW_STAGE,
)
from book_agent.services.billing_category import PILOT

PILOT_CHAPTER_COUNT = 2
# Shorter chapters show nothing of the book's quality (an epigraph, a part's opening page).
MIN_BODY_CHARS = 400
# A pilot that would be more than this share of the book is not a trial any more.
MAX_PILOT_SHARE = 0.6
# Run request key: the pilot run a whole-book run follows (what it reuses).
AFTER_PILOT_REQUEST_KEY = "after_pilot_run_id"

_NUMBERED_TITLE = re.compile(
    r"^\s*(?:chapter\s+(?P<a>\d{1,3})\b|(?P<b>\d{1,3})[.:)]?\s+(?=\S)|第\s*(?P<c>[0-9一二三四五六七八九十百]+)\s*章)",
    re.IGNORECASE,
)
_PART_TITLE = re.compile(
    r"^\s*(?:part\s+(?:\d+|[ivxlc]+|one|two|three|four|five|six)\b|第\s*[0-9一二三四五六七八九十]+\s*(?:部分|篇|部))", re.IGNORECASE
)
_APPENDIX_TITLE = re.compile(r"^\s*(?:appendix|附录)", re.IGNORECASE)
_FRONT_OR_BACK_MATTER = {
    "front matter",
    "cover",
    "title page",
    "half title",
    "copyright",
    "dedication",
    "epigraph",
    "contents",
    "table of contents",
    "brief contents",
    "foreword",
    # A misspelling a book may print (A Complete Guide To Volume Price Analysis).
    "foreward",
    "preface",
    "prologue",
    "acknowledgment",
    "acknowledgments",
    "acknowledgement",
    "acknowledgements",
    "about this book",
    "about the author",
    "about the authors",
    "about the cover",
    "about the cover illustration",
    "praise",
    "praise for this book",
    "index",
    "references",
    "bibliography",
    "glossary",
    "notes",
    "endnotes",
    "afterword",
    "epilogue",
    "colophon",
    "目录",
    "前言",
    "序",
    "序言",
    "致谢",
    "索引",
    "参考文献",
}


def chapter_number(title: str | None) -> int | None:
    """The chapter number a title starts with ("Chapter 3", "3 Indexing", "3. Tools", "第3章"), else None."""
    match = _NUMBERED_TITLE.match(title or "")
    if not match:
        return None
    raw = match.group("a") or match.group("b") or match.group("c") or ""
    if raw.isdigit():
        number = int(raw)
        return number if 0 < number < 200 else None
    return _cjk_number(raw)


def _cjk_number(raw: str) -> int | None:
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if raw in digits:
        return digits[raw]
    if raw.startswith("十"):
        return 10 + digits.get(raw[1:], 0)
    if "十" in raw:
        tens, _, ones = raw.partition("十")
        return digits.get(tens, 0) * 10 + digits.get(ones, 0)
    return None


def _normalized(title: str | None) -> str:
    return re.sub(r"\s+", " ", (title or "")).strip().strip(".:：").casefold()


def is_surrounding_matter(title: str | None) -> bool:
    """Front matter, Part dividers, appendices and back matter: never a pilot chapter."""
    normalized = _normalized(title)
    if not normalized:
        return False
    if _PART_TITLE.match(normalized) or _APPENDIX_TITLE.match(normalized):
        return True
    # "Introduction: ...", "Acknowledgments & Trader Resources": the label before the subtitle or the join.
    label = re.split(r"\s*[:：–—]\s*|\s+-\s+|\s+&\s+", normalized, maxsplit=1)[0]
    return normalized in _FRONT_OR_BACK_MATTER or label in _FRONT_OR_BACK_MATTER or normalized.startswith("about the ")


@dataclass(slots=True)
class PilotChapter:
    chapter_id: str
    ordinal: int
    title: str | None
    packet_count: int
    source_chars: int

    def to_json(self) -> dict[str, Any]:
        return {"chapter_id": self.chapter_id, "ordinal": self.ordinal, "title": self.title}


@dataclass(slots=True)
class PilotSelection:
    """The pilot's chapters, or why this book has no pilot (``unavailable_reason``)."""

    chapters: list[PilotChapter] = field(default_factory=list)
    rule: str | None = None  # "numbered_chapters" | "first_body_chapters"
    unavailable_reason: str | None = None  # "web_page" | "no_body_chapters" | "short_book"
    share: float = 0.0

    @property
    def available(self) -> bool:
        return self.unavailable_reason is None and bool(self.chapters)

    @property
    def chapter_ids(self) -> list[str]:
        return [chapter.chapter_id for chapter in self.chapters]


UNAVAILABLE_TEXT = {
    "web_page": "网页篇幅短，直接翻译全文，不需要试译。",
    "no_body_chapters": "没有找到可以试译的正文章节，可以直接翻译全书。",
    "short_book": "这本书很短，前两章就是大半本书，直接翻译全书即可。",
}


def select_pilot_chapters(session: Session, document_id: str) -> PilotSelection:
    document = session.get(Document, document_id)
    if document is None:
        raise LookupError(f"document not found: {document_id}")
    if document.source_type == SourceType.WEB:
        return PilotSelection(unavailable_reason="web_page")
    packet_counts = dict(
        session.execute(
            select(TranslationPacket.chapter_id, func.count(TranslationPacket.id))
            .join(Chapter, Chapter.id == TranslationPacket.chapter_id)
            .where(Chapter.document_id == document_id)
            .group_by(TranslationPacket.chapter_id)
        ).all()
    )
    chars = dict(
        session.execute(
            select(Sentence.chapter_id, func.coalesce(func.sum(func.length(Sentence.source_text)), 0))
            .where(
                Sentence.document_id == document_id,
                Sentence.translatable.is_(True),
                Sentence.retired_by_revision_id.is_(None),
            )
            .group_by(Sentence.chapter_id)
        ).all()
    )
    candidates = [
        PilotChapter(
            chapter_id=str(chapter.id),
            ordinal=int(chapter.ordinal),
            title=chapter.title_src,
            packet_count=int(packet_counts.get(chapter.id, 0)),
            source_chars=int(chars.get(chapter.id, 0) or 0),
        )
        for chapter in session.scalars(
            select(Chapter).where(Chapter.document_id == document_id).order_by(Chapter.ordinal)
        ).all()
        if packet_counts.get(chapter.id)
    ]
    total_chars = sum(chapter.source_chars for chapter in candidates) or 1
    body = [chapter for chapter in candidates if not is_surrounding_matter(chapter.title)]
    numbered = [chapter for chapter in body if chapter_number(chapter.title) is not None]
    if len(numbered) >= PILOT_CHAPTER_COUNT:
        chosen, rule = numbered[:PILOT_CHAPTER_COUNT], "numbered_chapters"
    else:
        substantial = [chapter for chapter in body if chapter.source_chars >= MIN_BODY_CHARS] or body
        chosen, rule = substantial[:PILOT_CHAPTER_COUNT], "first_body_chapters"
    if not chosen:
        return PilotSelection(unavailable_reason="no_body_chapters")
    share = sum(chapter.source_chars for chapter in chosen) / total_chars
    if len(body) <= PILOT_CHAPTER_COUNT or share > MAX_PILOT_SHARE:
        return PilotSelection(chapters=chosen, rule=rule, unavailable_reason="short_book", share=share)
    return PilotSelection(chapters=chosen, rule=rule, share=share)


def pilot_packet_ids(session: Session, chapter_ids: list[str]) -> list[str]:
    if not chapter_ids:
        return []
    return [
        str(packet_id)
        for packet_id in session.scalars(
            select(TranslationPacket.id).where(TranslationPacket.chapter_id.in_(chapter_ids)).order_by(TranslationPacket.id)
        ).all()
    ]


class PilotUnavailable(ValueError):
    pass


class PilotAlreadyRun(ValueError):
    pass


def latest_pilot_run(session: Session, document_id: str) -> DocumentRun | None:
    return session.scalars(
        select(DocumentRun)
        .where(
            DocumentRun.document_id == document_id,
            DocumentRun.run_type == DocumentRunType.TRANSLATE_FULL,
            DocumentRun.billing_category == PILOT,
        )
        .order_by(DocumentRun.created_at.desc(), DocumentRun.id.desc())
        .limit(1)
    ).first()


def latest_book_run(session: Session, document_id: str) -> DocumentRun | None:
    """The latest whole-book (paid) translation run."""
    return session.scalars(
        select(DocumentRun)
        .where(
            DocumentRun.document_id == document_id,
            DocumentRun.run_type == DocumentRunType.TRANSLATE_FULL,
            DocumentRun.billing_category != PILOT,
        )
        .order_by(DocumentRun.created_at.desc(), DocumentRun.id.desc())
        .limit(1)
    ).first()


def pilot_request(session: Session, document_id: str, *, fast_reading: bool = False) -> dict[str, Any]:
    """The run request of a new pilot run; raises when the book has no pilot or already had one."""
    if latest_pilot_run(session, document_id) is not None:
        raise PilotAlreadyRun("这本书已经试译过了：一本书只有一次免费试译。")
    if latest_book_run(session, document_id) is not None:
        raise PilotAlreadyRun("这本书已经开始翻译全书，不需要再试译。")
    selection = select_pilot_chapters(session, document_id)
    if not selection.available:
        raise PilotUnavailable(UNAVAILABLE_TEXT.get(selection.unavailable_reason or "", "这本书不能试译。"))
    return {
        SCOPE_REQUEST_KEY: SCOPE_PILOT,
        CHAPTER_IDS_REQUEST_KEY: selection.chapter_ids,
        PACKET_IDS_REQUEST_KEY: pilot_packet_ids(session, selection.chapter_ids),
        "pilot_rule": selection.rule,
        FAST_READING_REQUEST_KEY: "on" if fast_reading else "off",
    }


def _stage_succeeded(run: DocumentRun, stage: str) -> bool:
    stages = read_cached_stages(((run.status_detail_json or {}).get("pipeline"))) or {}
    detail = stages.get(stage) or {}
    return str(detail.get("status") or "") == "succeeded" and not detail.get("degraded")


def whole_book_request(session: Session, document_id: str, request: dict[str, Any]) -> dict[str, Any]:
    """A whole-book run's request after a finished pilot: what the pilot already did is not redone.

    Explicit choices in ``request`` win; this only fills in what the reader did not choose.
    """
    if request.get(SCOPE_REQUEST_KEY) == SCOPE_PILOT:
        return request
    pilot = latest_pilot_run(session, document_id)
    if pilot is None or pilot.status not in {DocumentRunStatus.SUCCEEDED, DocumentRunStatus.SUCCEEDED_WITH_WARNINGS}:
        return request
    pilot_request_ = dict((pilot.status_detail_json or {}).get(RUN_REQUEST_KEY) or {})
    request = dict(request)
    request.setdefault(AFTER_PILOT_REQUEST_KEY, pilot.id)
    # The pilot's terminology agent read the whole book; its glossary and BOOK.md stay.
    if _stage_succeeded(pilot, TERMINOLOGY_STAGE):
        request.setdefault("terminology", "skip")
    if _stage_succeeded(pilot, MODEL_REVIEW_STAGE):
        request.setdefault(MODEL_REVIEWED_CHAPTERS_REQUEST_KEY, list(pilot_request_.get(CHAPTER_IDS_REQUEST_KEY) or []))
    return request
