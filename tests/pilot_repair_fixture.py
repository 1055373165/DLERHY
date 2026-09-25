"""A small PDF book, translated by a deterministic fake model, as the pilot repair's test bench.

Three chapters with an outline, a running header on every page, a vector figure with its caption,
body text naming LLMs, numbers and a numbered figure. ``PilotBench`` imports it into SQLite,
translates the pilot's two chapters with ``FakeZhWorker`` (Chinese text shaped so the layered QA
has nothing to say about a clean translation) and exports the reading copy; each test then breaks
one thing the way the two real books were broken and runs the repair loop.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz
from sqlalchemy import select

from book_agent.core.ids import stable_id
from book_agent.domain.enums import ExportType
from book_agent.domain.models import Chapter, Document
from book_agent.domain.models.translation import TranslationPacket
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.services.workflows import DocumentWorkflowService
from book_agent.translation.contracts import TranslationUsage
from book_agent.workers.translator import (
    AlignmentSuggestion,
    TranslationTargetSegment,
    TranslationTask,
    TranslationWorkerMetadata,
    TranslationWorkerOutput,
    TranslationWorkerResult,
)

ORANGE = (0.95, 0.42, 0.3)
HEADER = "Retrieval for language models"

PARAGRAPHS = {
    1: [
        "An LLM answers from what it saw during training, and it cannot know about events after its cutoff date.",
        "Retrieval gives the LLM facts from outside sources, so the answer can cite a document written in 2023.",
        "This chapter explains why retrieval matters and how 3 simple parts work together in 12 steps.",
    ],
    2: [
        "Figure 1.4 shows the two prediction techniques that an LLM uses when it writes the next token.",
        "The first technique reads the context from left to right and predicts one token at a time.",
        "The second technique masks a token in the middle and predicts it from both sides of the text.",
    ],
    3: [
        "An indexing pipeline splits documents into chunks and stores each chunk with its embedding vector.",
        "The pipeline runs once for every new source, and it keeps 500 chunks in the store for this example.",
        "A generation pipeline retrieves the best chunks and hands them to the LLM together with the question.",
    ],
    4: [
        "The design of a retrieval system starts with the data that the LLM must be able to use.",
        "Each source needs a connector, and each connector turns 1 document into plain text for the pipeline.",
        "The system then measures how well the answers match the sources in 4 different ways.",
    ],
    5: [
        "Evaluation compares the answer with the retrieved text and scores it from 0 to 10 for faithfulness.",
        "A low score means the LLM added claims that the sources do not support in any way.",
        "Teams track the score over time and fix the retriever before they change the prompt.",
    ],
}
LONG_BODY = (
    "Later chapters build on these ideas with many more examples of real systems, and this paragraph "
    "is long enough to read as ordinary running prose in the body of the chapter, line after line. "
)


def _furniture(page: fitz.Page, number: int) -> None:
    page.insert_text((200, 34), HEADER, fontsize=9)
    page.insert_text((470, 34), str(number), fontsize=9)


def _token_figure(page: fitz.Page) -> None:
    page.draw_rect(fitz.Rect(258, 64, 312, 98), color=(0, 0, 0), dashes="[3] 0")
    page.insert_text((264, 76), "The teacher", fontsize=7)
    page.draw_rect(fitz.Rect(116, 100, 447, 106), color=None, fill=(0, 0, 0))
    page.draw_circle(fitz.Point(185, 202), 25, color=None, fill=ORANGE)
    page.draw_circle(fitz.Point(383, 202), 25, color=None, fill=ORANGE)
    for row_top in (132, 147, 254):
        for left in (102, 131, 170, 207, 237):
            page.draw_rect(fitz.Rect(left, row_top, left + 27, row_top + 12), color=None, fill=ORANGE)
    page.insert_text((133, 124), "Causal Language Modeling", fontsize=8)
    page.insert_text((140, 240), "Objective: Predict next token", fontsize=7)
    page.draw_line(fitz.Point(281, 144), fitz.Point(281, 302), color=(0.9, 0.9, 0.9))
    page.insert_text((151, 297), "Unidirectional context", fontsize=7)
    page.insert_text((102, 320), "Figure 1.4 Two token prediction techniques.", fontsize=8)


def _paragraphs(page: fitz.Page, texts: list[str], top: float) -> None:
    y = top
    for text in texts:
        rect = fitz.Rect(102, y, 477, y + 60)
        page.insert_textbox(rect, text, fontsize=10, fontname="tiro")
        y += 70


def build_book_pdf(path: Path) -> None:
    """Pages 1–3: chapter 1 (figure on page 2); 4–5: chapter 2; 6–9: chapter 3."""
    document = fitz.open()
    titles = {1: "1 Why retrieval", 4: "2 Designing the system", 6: "3 Running it in production"}
    for number in range(1, 10):
        page = document.new_page(width=531, height=666)
        _furniture(page, number)
        top = 72
        if number in titles:
            page.insert_text((102, 110), titles[number], fontname="tibo", fontsize=22)
            top = 150
        if number == 2:
            _token_figure(page)
            _paragraphs(page, PARAGRAPHS[2], 350)
        elif number in PARAGRAPHS:
            _paragraphs(page, PARAGRAPHS[number], top)
        else:
            page.insert_textbox(fitz.Rect(102, top, 477, 620), LONG_BODY * 6, fontsize=10, fontname="tiro")
    document.set_toc([[1, titles[1], 1], [1, titles[4], 4], [1, titles[6], 6]])
    document.save(path)


# ---------------------------------------------------------------------------
# A fake model that writes plausible Chinese


_NUMBER_RE = re.compile(r"\d+(?:\.\d+)*")
_WORDS = {
    "LLM": "大语言模型",
    "retrieval": "检索",
    "pipeline": "流水线",
    "chunk": "文本块",
    "embedding": "嵌入",
}


@dataclass
class FakeZhWorker:
    """Deterministic Chinese: every number, "Figure N.M" as 图 N.M, negations and comparisons kept.

    Knobs a test turns to break the first translation of chosen sentences:
    ``variant_sentences`` render LLM as 大型语言模型; ``omit_sentences`` get no translation;
    ``leak_sentences`` carry 图片锚点保留; ``unspaced_sentences`` drop the CJK–Latin spaces.
    ``retranslation_leaks`` makes every retranslation carry a program note (a repair that makes
    another check worse). ``cost_per_call`` is the usage each call reports.
    """

    variant_sentences: set[str] = field(default_factory=set)
    omit_sentences: set[str] = field(default_factory=set)
    leak_sentences: set[str] = field(default_factory=set)
    unspaced_sentences: set[str] = field(default_factory=set)
    retranslation_leaks: bool = False
    cost_per_call: float | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    translated_once: set[str] = field(default_factory=set)

    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(worker_name="FakeZhWorker", model_name="fake-zh", prompt_version="test.v1", runtime_config={})

    def zh(self, source: str, *, variant: bool = False, spaced: bool = True) -> str:
        text = " ".join(source.split())
        pieces: list[str] = []
        figure = re.search(r"\b(Figure|Listing|Table)\s+(\d+(?:\.\d+)+)", text)
        if figure:
            name = {"Figure": "图", "Listing": "代码清单", "Table": "表"}[figure.group(1)]
            pieces.append(f"{name} {figure.group(2)}")
        for word, rendering in _WORDS.items():
            if re.search(rf"\b{re.escape(word)}", text, re.IGNORECASE):
                pieces.append("大型语言模型" if (word == "LLM" and variant) else rendering)
        numbers = [number for number in _NUMBER_RE.findall(text) if not (figure and number == figure.group(2))]
        # Numbers with a Chinese measure word after them: the book writes a space between the two.
        pieces.extend(f"第 {number} 处" for number in numbers)
        if re.search(r"\b(?:not|cannot|no|never|without)\b|n't", text, re.IGNORECASE):
            pieces.append("不")
        if re.search(r"\bthan\b", text):
            pieces.append("比")
        body = "这一句讲的是" + "，".join(pieces) if pieces else "这一句讲的是正文"
        filler = "内容" * max(2, len(text) // 12)
        sentence = f"{body}，{filler}。"
        return sentence if spaced else sentence.replace(" ", "")

    def translate(self, task: TranslationTask) -> TranslationWorkerResult:
        packet_id = task.context_packet.packet_id
        retranslation = packet_id in self.translated_once
        self.calls.append(
            {"packet_id": packet_id, "retranslation": retranslation, "hints": list(task.context_packet.open_questions)}
        )
        segments: list[TranslationTargetSegment] = []
        alignments: list[AlignmentSuggestion] = []
        for sentence in task.current_sentences:
            if not retranslation and sentence.id in self.omit_sentences:
                continue
            source = sentence.normalized_text or sentence.source_text or ""
            spaced = retranslation or sentence.id not in self.unspaced_sentences
            text = self.zh(source, variant=not retranslation and sentence.id in self.variant_sentences, spaced=spaced)
            # Two spaces around the Latin runs of a "spaced" text (the book's convention).
            text = re.sub(r"(?<=[一-鿿])(?=[A-Za-z0-9])|(?<=[A-Za-z0-9])(?=[一-鿿])", " ", text) if spaced else text
            if (not retranslation and sentence.id in self.leak_sentences) or (retranslation and self.retranslation_leaks):
                text = "图片锚点保留" + text
            temp_id = stable_id("temp-segment", packet_id, sentence.id)
            segments.append(
                TranslationTargetSegment(
                    temp_id=temp_id, text_zh=text, segment_type="sentence", source_sentence_ids=[sentence.id], confidence=0.9
                )
            )
            alignments.append(
                AlignmentSuggestion(source_sentence_ids=[sentence.id], target_temp_ids=[temp_id], relation_type="1:1", confidence=0.95)
            )
        self.translated_once.add(packet_id)
        return TranslationWorkerResult(
            output=TranslationWorkerOutput(packet_id=packet_id, target_segments=segments, alignment_suggestions=alignments),
            usage=TranslationUsage(token_in=1000, token_out=500, total_tokens=1500, cost_usd=self.cost_per_call),
        )


# ---------------------------------------------------------------------------
# The bench


class PilotBench:
    """The book imported into a fresh SQLite database; nothing translated until ``translate_pilot``."""

    def __init__(self, worker: FakeZhWorker | None = None) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.pdf_path = self.root / "book.pdf"
        build_book_pdf(self.pdf_path)
        self.engine = build_engine(
            f"sqlite+pysqlite:///{self.root / 'bench.db'}", connect_args={"check_same_thread": False, "timeout": 30}
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        self.worker = worker or FakeZhWorker()
        self.export_root = self.root / "exports"
        with self.session_factory() as session:
            summary = self.workflow(session).bootstrap_document(self.pdf_path)
            session.commit()
            self.document_id = summary.document_id

    def close(self) -> None:
        self.engine.dispose()
        self.tempdir.cleanup()

    def workflow(self, session) -> DocumentWorkflowService:
        # No output repair call: an injected flaw must survive the first translation, as it would with a real model.
        return DocumentWorkflowService(
            session, export_root=self.export_root, translation_worker=self.worker, translation_max_output_repairs=0
        )

    def chapters(self, session) -> list[Chapter]:
        return list(session.scalars(select(Chapter).where(Chapter.document_id == self.document_id).order_by(Chapter.ordinal)).all())

    def pilot_chapter_ids(self, session) -> list[str]:
        from book_agent.services.pilot import select_pilot_chapters

        return select_pilot_chapters(session, self.document_id).chapter_ids

    def translate_pilot(self) -> list[str]:
        with self.session_factory() as session:
            chapter_ids = self.pilot_chapter_ids(session)
            packet_ids = list(
                session.scalars(select(TranslationPacket.id).where(TranslationPacket.chapter_id.in_(chapter_ids))).all()
            )
            workflow = self.workflow(session)
            for packet_id in packet_ids:
                workflow.translation_service.execute_packet(packet_id)
            session.commit()
            return chapter_ids

    def export(self, chapter_ids: list[str]) -> None:
        with self.session_factory() as session:
            self.workflow(session).export_document(
                self.document_id, ExportType.MERGED_HTML, chapter_ids=chapter_ids, despite_gate=True
            )
            session.commit()

    def document(self, session) -> Document:
        return session.get(Document, self.document_id)


# ---------------------------------------------------------------------------
# Breaking the pilot the way real books were broken


def sentence_ids(session, document_id: str, contains: str) -> list[str]:
    from book_agent.domain.models import Sentence

    return [
        sentence.id
        for sentence in session.scalars(
            select(Sentence).where(Sentence.document_id == document_id, Sentence.retired_by_revision_id.is_(None))
        ).all()
        if contains in (sentence.source_text or "")
    ]


def block_with_text(session, document_id: str, contains: str):
    from book_agent.domain.enums import ArtifactStatus
    from book_agent.domain.models import Block

    return next(
        block
        for block in session.scalars(
            select(Block)
            .join(Chapter, Chapter.id == Block.chapter_id)
            .where(Chapter.document_id == document_id, Block.status == ArtifactStatus.ACTIVE)
            .order_by(Chapter.ordinal, Block.ordinal)
        ).all()
        if contains in (block.source_text or "")
    )


def packet_of(session, sentence_id: str) -> str:
    from book_agent.domain.enums import PacketSentenceRole
    from book_agent.domain.models.translation import PacketSentenceMap

    return str(
        session.scalar(
            select(PacketSentenceMap.packet_id).where(
                PacketSentenceMap.sentence_id == sentence_id, PacketSentenceMap.role == PacketSentenceRole.CURRENT
            )
        )
    )


def fold_into_previous(session, chapter_id: str) -> str:
    """The parser missed a chapter start: the chapter's blocks, sentences, packets and translations
    end up in the chapter before it (the RAG book's "Part" chapters, before the outline fix)."""
    from sqlalchemy import update

    from book_agent.domain.models import Block, MemorySnapshot, Sentence
    from book_agent.domain.models.review import ChapterQualitySummary, ReviewIssue
    from book_agent.domain.models.translation import ChapterMemoryProposal, TargetSegment

    chapter = session.get(Chapter, chapter_id)
    previous = session.scalars(
        select(Chapter).where(Chapter.document_id == chapter.document_id, Chapter.ordinal < chapter.ordinal).order_by(Chapter.ordinal.desc())
    ).first()
    offset = int(session.scalar(select(Block.ordinal).where(Block.chapter_id == previous.id).order_by(Block.ordinal.desc()).limit(1)) or 0)
    for block in session.scalars(select(Block).where(Block.chapter_id == chapter.id)).all():
        block.chapter_id = previous.id
        block.ordinal += offset
    for model in (Sentence, TranslationPacket, TargetSegment, ReviewIssue, ChapterMemoryProposal):
        session.execute(update(model).where(model.chapter_id == chapter.id).values(chapter_id=previous.id))
    session.execute(ChapterQualitySummary.__table__.delete().where(ChapterQualitySummary.chapter_id == chapter.id))
    session.execute(MemorySnapshot.__table__.delete().where(MemorySnapshot.scope_id == chapter.id))
    previous.metadata_json = {**(previous.metadata_json or {}), "source_page_end": (chapter.metadata_json or {}).get("source_page_end")}
    session.flush()
    ordinal = chapter.ordinal
    session.delete(chapter)
    session.flush()
    for later in session.scalars(
        select(Chapter).where(Chapter.document_id == previous.document_id, Chapter.ordinal > ordinal).order_by(Chapter.ordinal)
    ).all():
        later.ordinal -= 1
        session.flush()
    return previous.id


def edit_translation(session, sentence_id: str, change) -> None:
    """Change one sentence's translation (a new attempt, the way a person's edit is stored)."""
    from book_agent.infra.repositories.review import active_target_texts
    from book_agent.services.segment_edit import SegmentEditService

    current = active_target_texts(session, [sentence_id])[sentence_id]
    SegmentEditService(session).edit_sentence(sentence_id, change(current), reason="test", actor_id="test")
