"""Re-parse the chapters nothing has translated yet, with the current parser.

A book is parsed once, at import. The parser keeps improving (section headings kept as paragraphs,
prompt callouts split into loose lines, figure text left as tables), and the pilot's repair loop
only fixes the pilot's chapters. Before the whole book is translated, the chapters no translation
has touched can simply be read again: nothing is lost, and every later packet is translated from
the better structure.

What it does, all in the caller's transaction:

1. parse the source file again (``ParseService``; the canonical IR sidecar goes to a scratch
   directory, the import's stays as it is);
2. refuse unless the new parse cuts the book into the same chapters (same count, ids, titles and
   first pages): chapter ids, order and titles are kept, only blocks change;
3. leave alone every chapter with a translation attempt (the pilot's), with structure edits (a
   person's or the repair's), or whose blocks came back unchanged;
4. for the others, open a parse revision (``parse_revision_fork``: versioned, audited, an event),
   invalidate the old blocks (parked above every real ordinal), retire their sentences, delete their
   untranslated packets, and insert the new blocks, sentences, figure records, a new chapter brief
   and new packets;
5. record the parser fingerprint the book is now read with (``parser_fingerprint``).

``revert`` undoes one such revision while its new packets are still untranslated: the old blocks and
sentences come back and their packets are built again.
"""

from __future__ import annotations

import hashlib
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from book_agent.core.ids import stable_id
from book_agent.domain.context.builders import ChapterBriefBuilder, ContextPacketBuilder
from book_agent.domain.enums import (
    ActorType,
    ArtifactStatus,
    ChapterStatus,
    IssueStatus,
    MemoryScopeType,
    MemoryStatus,
    ParseRevisionStatus,
    SnapshotType,
    SourceType,
)
from book_agent.domain.event_kinds import DOCUMENT_REPARSED
from book_agent.domain.models import Block, Chapter, Document, DocumentParseRevision, MemorySnapshot, Sentence, StructureEdit
from book_agent.domain.models.document import BookProfile, DocumentImage
from book_agent.domain.models.ops import AuditEvent
from book_agent.domain.models.review import ReviewIssue
from book_agent.domain.models.translation import PacketSentenceMap, TargetSegment, TranslationPacket, TranslationRun
from book_agent.domain.structure.callouts import is_callout_metadata
from book_agent.infra.repositories.events import emit_event
from book_agent.infra.repositories.review import ReviewRepository

ACTOR_ID = "services.chapter_reparse"
# A chapter's layout fields the parser derives from its pages (the review reads them).
_LAYOUT_EVIDENCE_KEYS = (
    "pdf_layout_risk",
    "suspicious_page_numbers",
    "structure_flags",
    "parse_confidence",
    "pdf_role_counts",
    "pdf_page_family_counts",
)
PDF_SOURCE_TYPES = frozenset({SourceType.PDF_TEXT, SourceType.PDF_MIXED, SourceType.PDF_SCAN})
# Old blocks of a re-parsed chapter wait here, above the structure edits' parking range.
REPARSE_PARKED_ORDINAL_BASE = 3_000_000
PARSER_FINGERPRINT_KEY = "parser_fingerprint"
REPARSE_HISTORY_KEY = "chapter_reparse"
# Where the parser lives: a change to any of these files is a change of how a book is read.
_PARSER_PACKAGES = ("ingestion", "domain/structure")
_PARSER_FILES = ("domain/block_rules.py", "services/bootstrap.py")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@lru_cache(maxsize=1)
def parser_fingerprint() -> str:
    """A hash of the parser's source: it changes whenever the code that reads a book changes."""
    root = Path(__file__).resolve().parents[1]
    files: list[Path] = []
    for package in _PARSER_PACKAGES:
        files.extend(sorted((root / package).rglob("*.py")))
    files.extend(root / name for name in _PARSER_FILES)
    digest = hashlib.sha256()
    for path in files:
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def record_parser_fingerprint(document: Document) -> None:
    document.metadata_json = {**(document.metadata_json or {}), PARSER_FINGERPRINT_KEY: parser_fingerprint()}


def parser_changed_since_import(document: Document) -> bool:
    """True when the book was read by other parser code than today's (or before fingerprints were kept)."""
    return (document.metadata_json or {}).get(PARSER_FINGERPRINT_KEY) != parser_fingerprint()


class ReparseRefused(ValueError):
    """The re-parse would not be safe (the reason is for the reader, in Chinese)."""


class SourceMissing(ReparseRefused):
    pass


class ChapterBoundaryMismatch(ReparseRefused):
    pass


@dataclass(slots=True)
class ChapterReparse:
    chapter_id: str
    ordinal: int
    title: str | None
    # refreshed | unchanged | translated | edited
    outcome: str
    before: dict[str, int] = field(default_factory=dict)
    after: dict[str, int] = field(default_factory=dict)
    old_block_count: int = 0
    new_block_count: int = 0
    new_packet_count: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "chapter_id": self.chapter_id,
            "ordinal": self.ordinal,
            "title": self.title,
            "outcome": self.outcome,
            "before": dict(self.before),
            "after": dict(self.after),
            "old_block_count": self.old_block_count,
            "new_block_count": self.new_block_count,
            "new_packet_count": self.new_packet_count,
        }


@dataclass(slots=True)
class ReparseResult:
    document_id: str
    dry_run: bool
    parser_fingerprint: str
    parse_revision_id: str | None = None
    parse_revision_version: int | None = None
    chapters: list[ChapterReparse] = field(default_factory=list)

    @property
    def refreshed_chapter_ids(self) -> list[str]:
        return [chapter.chapter_id for chapter in self.chapters if chapter.outcome == "refreshed"]

    def to_json(self) -> dict[str, Any]:
        outcomes = Counter(chapter.outcome for chapter in self.chapters)
        return {
            "document_id": self.document_id,
            "dry_run": self.dry_run,
            "parser_fingerprint": self.parser_fingerprint,
            "parse_revision_id": self.parse_revision_id,
            "parse_revision_version": self.parse_revision_version,
            "refreshed_chapter_count": outcomes.get("refreshed", 0),
            "outcomes": dict(outcomes),
            "chapters": [chapter.to_json() for chapter in self.chapters],
        }


def block_stats(blocks: list[Any]) -> dict[str, int]:
    """What a reader notices of a chapter's structure: block types, and callouts."""
    counts: Counter[str] = Counter()
    for block in blocks:
        block_type = getattr(block.block_type, "value", str(block.block_type))
        counts[block_type] += 1
        if is_callout_metadata(block.source_span_json):
            counts["callout"] += 1
    return dict(sorted(counts.items()))


def _signature(blocks: list[Any]) -> list[tuple[str, str, bool]]:
    return [
        (
            getattr(block.block_type, "value", str(block.block_type)),
            " ".join(str(block.source_text or "").split()),
            is_callout_metadata(block.source_span_json),
        )
        for block in sorted(blocks, key=lambda item: item.ordinal)
    ]


def _title_key(title: str | None) -> str:
    return " ".join(str(title or "").split()).casefold()


def _remap(value: Any, id_map: dict[str, str]) -> Any:
    """Block ids the parser wrote into metadata (captions, figure groups, callout icons), renamed."""
    if isinstance(value, str):
        return id_map.get(value, value)
    if isinstance(value, list):
        return [_remap(item, id_map) for item in value]
    if isinstance(value, dict):
        return {key: _remap(item, id_map) for key, item in value.items()}
    return value


class ChapterReparseService:
    def __init__(self, session: Session, *, parse_service: Any | None = None) -> None:
        self.session = session
        self._parse_service = parse_service

    # --- which chapters ------------------------------------------------------------------

    def translated_chapter_ids(self, document_id: str) -> set[str]:
        """Chapters with any translation attempt (a failed one included) or translation text."""
        with_runs = self.session.scalars(
            select(TranslationPacket.chapter_id)
            .join(TranslationRun, TranslationRun.packet_id == TranslationPacket.id)
            .join(Chapter, Chapter.id == TranslationPacket.chapter_id)
            .where(Chapter.document_id == document_id)
            .distinct()
        ).all()
        with_text = self.session.scalars(
            select(TargetSegment.chapter_id)
            .join(Chapter, Chapter.id == TargetSegment.chapter_id)
            .where(Chapter.document_id == document_id)
            .distinct()
        ).all()
        return {str(chapter_id) for chapter_id in [*with_runs, *with_text]}

    def edited_chapter_ids(self, document_id: str) -> set[str]:
        """Chapters a structure edit touched: its edit would be lost with the old blocks."""
        chapters: set[str] = set()
        for blocks in self.session.scalars(select(StructureEdit.blocks_json).where(StructureEdit.document_id == document_id)).all():
            for entry in blocks or []:
                if isinstance(entry, dict) and entry.get("chapter_id"):
                    chapters.add(str(entry["chapter_id"]))
        return chapters

    def untranslated_chapter_ids(self, document_id: str) -> list[str]:
        translated = self.translated_chapter_ids(document_id)
        return [
            str(chapter_id)
            for chapter_id in self.session.scalars(
                select(Chapter.id).where(Chapter.document_id == document_id).order_by(Chapter.ordinal)
            ).all()
            if str(chapter_id) not in translated
        ]

    def needs_reparse(self, document_id: str) -> bool:
        """The whole-book run's question: a PDF read by older parser code, with chapters still untranslated."""
        document = self.session.get(Document, document_id)
        if document is None or document.source_type not in PDF_SOURCE_TYPES:
            return False
        return parser_changed_since_import(document) and bool(self.untranslated_chapter_ids(document_id))

    def epub_outdated_chapter_ids(self, document_id: str) -> list[str]:
        """The whole-book run's question for an EPUB: its untranslated chapters whose blocks an older
        parser read (``epub_structure_version`` below the current one: lists without markers, definition
        terms dropped, notes as a heading over loose paragraphs). The structure refresh re-reads them."""
        from book_agent.domain.structure.epub import EPUB_STRUCTURE_VERSION

        document = self.session.get(Document, document_id)
        if document is None or document.source_type != SourceType.EPUB:
            return []
        outdated: list[str] = []
        for chapter_id in self.untranslated_chapter_ids(document_id):
            versions = [
                int((block.source_span_json or {}).get("epub_structure_version") or 1)
                for block in self._active_blocks(chapter_id)
            ]
            if versions and min(versions) < EPUB_STRUCTURE_VERSION:
                outdated.append(chapter_id)
        return outdated

    # --- the re-parse ----------------------------------------------------------------------

    def _parse(self, document: Document) -> Any:
        from book_agent.services.bootstrap import ParseService
        from book_agent.services.parse_ir import ParseIrService
        from book_agent.services.pdf_structure_refresh import _clone_document

        source = Path(str(document.source_path or "")).expanduser()
        if not document.source_path or not source.is_file():
            raise SourceMissing(f"找不到这本书的原文件（{document.source_path or '没有记录路径'}），无法按新的解析规则重新读取。")
        parse_service = self._parse_service or ParseService(
            # The import's canonical IR stays as it is; this parse's goes to a scratch directory.
            parse_ir_service=ParseIrService(output_root=tempfile.mkdtemp(prefix="book-agent-reparse-"))
        )
        return parse_service.parse(_clone_document(document), source)

    def reparse_untranslated(
        self,
        document_id: str,
        *,
        dry_run: bool = False,
        include_edited: bool = False,
        actor_id: str = ACTOR_ID,
        run_id: str | None = None,
    ) -> ReparseResult:
        document = self.session.get(Document, document_id)
        if document is None:
            raise LookupError(f"document not found: {document_id}")
        if document.source_type not in PDF_SOURCE_TYPES:
            raise ReparseRefused("只有 PDF 书需要按新的解析规则重新读取。")
        parsed = self._parse(document)
        chapters = list(self.session.scalars(select(Chapter).where(Chapter.document_id == document_id).order_by(Chapter.ordinal)).all())
        self._check_boundaries(chapters, parsed.chapters)
        parsed_blocks: dict[str, list[Block]] = {}
        for block in parsed.blocks:
            parsed_blocks.setdefault(block.chapter_id, []).append(block)
        images_by_block = {image.block_id: image for image in parsed.document_images}

        translated = self.translated_chapter_ids(document_id)
        edited = set() if include_edited else self.edited_chapter_ids(document_id)
        result = ReparseResult(document_id=document_id, dry_run=dry_run, parser_fingerprint=parser_fingerprint())
        to_refresh: list[tuple[Chapter, list[Block], list[Block]]] = []
        for chapter in chapters:
            old_blocks = self._active_blocks(chapter.id)
            new_blocks = sorted(parsed_blocks.get(chapter.id, []), key=lambda block: block.ordinal)
            entry = ChapterReparse(
                chapter_id=chapter.id,
                ordinal=chapter.ordinal,
                title=chapter.title_src,
                outcome="refreshed",
                before=block_stats(old_blocks),
                after=block_stats(new_blocks),
                old_block_count=len(old_blocks),
                new_block_count=len(new_blocks),
            )
            if chapter.id in translated:
                entry.outcome, entry.after = "translated", dict(entry.before)
            elif chapter.id in edited:
                entry.outcome, entry.after = "edited", dict(entry.before)
            elif _signature(old_blocks) == _signature(new_blocks):
                entry.outcome = "unchanged"
            else:
                to_refresh.append((chapter, old_blocks, new_blocks))
            result.chapters.append(entry)
        if dry_run:
            return result

        now = _utcnow()
        revision: DocumentParseRevision | None = None
        record: dict[str, Any] = {"chapters": {}}
        by_id = {entry.chapter_id: entry for entry in result.chapters}
        for chapter, old_blocks, new_blocks in to_refresh:
            if revision is None:
                revision = self._open_revision(document, actor_id=actor_id, now=now)
            by_id[chapter.id].new_packet_count, record["chapters"][chapter.id] = self._replace_chapter(
                document, chapter, old_blocks, new_blocks, images_by_block, revision, now
            )
        self._sync_layout_evidence(
            document,
            chapters,
            parsed,
            {entry.chapter_id for entry in result.chapters if entry.outcome in {"refreshed", "unchanged"}},
        )
        record_parser_fingerprint(document)
        document.updated_at = now
        if revision is not None:
            result.parse_revision_id, result.parse_revision_version = revision.id, revision.version
            revision.metadata_json = {**(revision.metadata_json or {}), **record, "summary": result.to_json()}
        self._log(document, result, actor_id=actor_id, run_id=run_id, now=now)
        self.session.flush()
        return result

    def _sync_layout_evidence(
        self,
        document: Document,
        chapters: list[Chapter],
        parsed: Any,
        chapter_ids: set[str],
    ) -> None:
        """The layout evidence of the chapters now read by this parser: each chapter's layout fields
        and the document's per-page evidence for its pages. The review judges reading order by them;
        left from the import, they flagged pages whose tables the old parser read as stray prose
        (Art of AI p304 and the appendix) though the new blocks read in one column. Translated and
        edited chapters keep theirs, which describe the blocks they still have."""
        parsed_by_id = {chapter.id: chapter for chapter in parsed.chapters}
        pages: set[int] = set()
        for chapter in chapters:
            fresh = parsed_by_id.get(chapter.id)
            if chapter.id not in chapter_ids or fresh is None:
                continue
            fresh_metadata = fresh.metadata_json or {}
            chapter.metadata_json = {
                **(chapter.metadata_json or {}),
                **{key: fresh_metadata[key] for key in _LAYOUT_EVIDENCE_KEYS if key in fresh_metadata},
            }
            if fresh.risk_level is not None:
                chapter.risk_level = fresh.risk_level
            start, end = fresh_metadata.get("source_page_start"), fresh_metadata.get("source_page_end")
            if isinstance(start, int) and isinstance(end, int):
                pages.update(range(start, end + 1))
        fresh_evidence = (parsed.document.metadata_json or {}).get("pdf_page_evidence")
        old_evidence = (document.metadata_json or {}).get("pdf_page_evidence")
        if not pages or not isinstance(fresh_evidence, dict):
            return
        fresh_pages = {
            int(page["page_number"]): page
            for page in fresh_evidence.get("pdf_pages") or []
            if isinstance(page, dict) and isinstance(page.get("page_number"), int)
        }
        if not isinstance(old_evidence, dict):
            merged = fresh_evidence
        else:
            kept = [
                page
                for page in old_evidence.get("pdf_pages") or []
                if not (isinstance(page, dict) and page.get("page_number") in pages and page.get("page_number") in fresh_pages)
            ]
            replaced = [fresh_pages[number] for number in sorted(pages) if number in fresh_pages]
            merged = {
                **old_evidence,
                "pdf_pages": sorted(kept + replaced, key=lambda page: int(page.get("page_number") or 0)),
            }
        document.metadata_json = {**(document.metadata_json or {}), "pdf_page_evidence": merged}

    def _check_boundaries(self, chapters: list[Chapter], parsed_chapters: list[Chapter]) -> None:
        if len(chapters) != len(parsed_chapters):
            raise ChapterBoundaryMismatch(
                f"新的解析把这本书分成了 {len(parsed_chapters)} 章，现在是 {len(chapters)} 章：章节边界变了，不能只换章节内容。"
            )
        for old, new in zip(chapters, parsed_chapters):
            old_page = (old.metadata_json or {}).get("source_page_start")
            new_page = (new.metadata_json or {}).get("source_page_start")
            if old.id != new.id or _title_key(old.title_src) != _title_key(new.title_src) or old_page != new_page:
                raise ChapterBoundaryMismatch(
                    f"第 {old.ordinal} 章在新的解析里不一样了（「{(old.title_src or '').strip()[:40]}」第 {old_page} 页 → "
                    f"「{(new.title_src or '').strip()[:40]}」第 {new_page} 页）：章节边界变了，不能只换章节内容。"
                )

    def _active_blocks(self, chapter_id: str) -> list[Block]:
        return list(
            self.session.scalars(
                select(Block)
                .where(Block.chapter_id == chapter_id, Block.status == ArtifactStatus.ACTIVE, Block.ordinal < 1_000_000)
                .order_by(Block.ordinal)
            ).all()
        )

    def _open_revision(self, document: Document, *, actor_id: str, now: datetime) -> DocumentParseRevision:
        from book_agent.services.parse_revision_fork import ParseRevisionForkService

        revision = ParseRevisionForkService(self.session)._open_revision(
            document, reason="re-parse untranslated chapters with the current parser", actor_id=actor_id, now=now
        )
        revision.metadata_json = {**(revision.metadata_json or {}), "kind": "chapter_reparse", "parser_fingerprint": parser_fingerprint()}
        return revision

    def _replace_chapter(
        self,
        document: Document,
        chapter: Chapter,
        old_blocks: list[Block],
        parsed_blocks: list[Block],
        images_by_block: dict[str, DocumentImage],
        revision: DocumentParseRevision,
        now: datetime,
    ) -> tuple[int, dict[str, Any]]:
        from book_agent.services.bootstrap import SegmentationService

        # 1. The old structure steps aside: blocks parked and invalidated, sentences retired, packets gone.
        old_ids = [block.id for block in old_blocks]
        parked_base = REPARSE_PARKED_ORDINAL_BASE + revision.version * 100_000
        for block in old_blocks:
            block.source_span_json = {
                **(block.source_span_json or {}),
                "reparse_superseded_by_revision_id": revision.id,
                "reparse_original_ordinal": block.ordinal,
            }
            block.ordinal = parked_base + block.ordinal
            block.status = ArtifactStatus.INVALIDATED
            block.updated_at = now
        self.session.flush()
        old_sentence_ids = list(
            self.session.scalars(
                select(Sentence.id).where(Sentence.chapter_id == chapter.id, Sentence.retired_by_revision_id.is_(None))
            ).all()
        )
        for sentence in self.session.scalars(select(Sentence).where(Sentence.id.in_(old_sentence_ids or ["-"]))).all():
            sentence.retired_by_revision_id = revision.id
            sentence.updated_at = now
        old_packet_ids = list(self.session.scalars(select(TranslationPacket.id).where(TranslationPacket.chapter_id == chapter.id)).all())
        self._settle_issues(chapter.id, revision)
        if old_packet_ids:
            self.session.execute(delete(PacketSentenceMap).where(PacketSentenceMap.packet_id.in_(old_packet_ids)))
            self.session.execute(delete(TranslationPacket).where(TranslationPacket.id.in_(old_packet_ids)))
        self.session.flush()

        # 2. The new blocks, under ids of this revision (the parser's ids are the old blocks').
        id_map = {
            block.id: stable_id("block", document.id, chapter.id, "reparse", revision.version, block.ordinal)
            for block in parsed_blocks
        }
        new_blocks: list[Block] = []
        for parsed in parsed_blocks:
            block = Block(
                id=id_map[parsed.id],
                chapter_id=chapter.id,
                ordinal=parsed.ordinal,
                block_type=parsed.block_type,
                parse_revision_id=revision.id,
                canonical_node_id=parsed.canonical_node_id,
                source_text=parsed.source_text,
                normalized_text=parsed.normalized_text,
                source_anchor=parsed.source_anchor,
                source_span_json={**_remap(dict(parsed.source_span_json or {}), id_map), "parse_revision_id": revision.id},
                parse_confidence=parsed.parse_confidence,
                protected_policy=parsed.protected_policy,
                status=ArtifactStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            )
            self.session.add(block)
            new_blocks.append(block)
        self.session.flush()
        for parsed in parsed_blocks:
            image = images_by_block.get(parsed.id)
            if image is None:
                continue
            block_id = id_map[parsed.id]
            self.session.add(
                DocumentImage(
                    id=stable_id("document-image", document.id, block_id),
                    document_id=document.id,
                    block_id=block_id,
                    page_number=image.page_number,
                    image_type=image.image_type,
                    storage_path=str(image.storage_path or "").replace(parsed.id, block_id),
                    bbox_json=image.bbox_json,
                    ocr_text=image.ocr_text,
                    latex=image.latex,
                    alt_text=image.alt_text,
                    width_px=image.width_px,
                    height_px=image.height_px,
                    metadata_json=_remap(dict(image.metadata_json or {}), id_map),
                    created_at=now,
                )
            )

        # 3. Sentences, the chapter brief and packets, as an import builds them.
        segmentation = SegmentationService()
        sentences: list[Sentence] = []
        for block in new_blocks:
            for sentence in segmentation._build_sentences(document, chapter, block, now):
                sentence.parse_revision_id = revision.id
                sentence.source_span_json = {**(sentence.source_span_json or {}), "parse_revision_id": revision.id}
                self.session.add(sentence)
                sentences.append(sentence)
        self.session.flush()
        new_packet_ids = self._build_packets(document, chapter, new_blocks, sentences, now)
        chapter.status = ChapterStatus.PACKET_BUILT if new_packet_ids else ChapterStatus.SEGMENTED
        chapter.updated_at = now
        self.session.flush()
        return len(new_packet_ids), {
            "old_block_ids": old_ids,
            "old_sentence_ids": old_sentence_ids,
            "old_packet_ids": old_packet_ids,
            "new_block_ids": [block.id for block in new_blocks],
            "new_packet_ids": new_packet_ids,
        }

    def _latest_snapshot(self, document_id: str, snapshot_type: SnapshotType, scope_id: str | None) -> MemorySnapshot | None:
        query = select(MemorySnapshot).where(
            MemorySnapshot.document_id == document_id,
            MemorySnapshot.snapshot_type == snapshot_type,
            MemorySnapshot.status == MemoryStatus.ACTIVE,
        )
        if scope_id is not None:
            query = query.where(MemorySnapshot.scope_type == MemoryScopeType.CHAPTER, MemorySnapshot.scope_id == scope_id)
        return self.session.scalars(query.order_by(MemorySnapshot.version.desc()).limit(1)).first()

    def _build_packets(self, document: Document, chapter: Chapter, blocks: list[Block], sentences: list[Sentence], now: datetime) -> list[str]:
        profile = self.session.scalars(
            select(BookProfile).where(BookProfile.document_id == document.id).order_by(BookProfile.version.desc()).limit(1)
        ).first()
        termbase = self._latest_snapshot(document.id, SnapshotType.TERMBASE, None)
        entities = self._latest_snapshot(document.id, SnapshotType.ENTITY_REGISTRY, None)
        if profile is None or termbase is None or entities is None:
            raise ReparseRefused("这本书缺少导入时建立的翻译上下文（书籍档案或术语快照），不能重建翻译包。")
        previous = self._latest_snapshot(document.id, SnapshotType.CHAPTER_BRIEF, chapter.id)
        latest_version = self.session.scalar(
            select(func.max(MemorySnapshot.version)).where(
                MemorySnapshot.document_id == document.id,
                MemorySnapshot.snapshot_type == SnapshotType.CHAPTER_BRIEF,
                MemorySnapshot.scope_id == chapter.id,
            )
        )
        if previous is not None:
            previous.status = MemoryStatus.SUPERSEDED
        brief = ChapterBriefBuilder().build(document, chapter, blocks, sentences, version=int(latest_version or 0) + 1)
        brief.created_at = now
        self.session.add(brief)
        self.session.flush()
        built = ContextPacketBuilder().build_many(
            document=document,
            chapters=[chapter],
            blocks=blocks,
            sentences=sentences,
            book_profile=profile,
            chapter_briefs=[brief],
            termbase_snapshot=termbase,
            entity_snapshot=entities,
        )
        for packet in built.translation_packets:
            self.session.add(packet)
        self.session.flush()
        for mapping in built.packet_sentence_maps:
            self.session.add(mapping)
        self.session.flush()
        return [packet.id for packet in built.translation_packets]

    def _settle_issues(self, chapter_id: str, revision: DocumentParseRevision) -> None:
        """Open issues the system raised on the old structure are resolved; a person's stay as they are."""
        stale = list(
            self.session.scalars(
                select(ReviewIssue).where(
                    ReviewIssue.chapter_id == chapter_id,
                    ReviewIssue.status.in_((IssueStatus.OPEN, IssueStatus.TRIAGED)),
                    ReviewIssue.decided_by.is_(None),
                )
            ).all()
        )
        if stale:
            ReviewRepository(self.session).sync_issues(
                [],
                [],
                owned_existing=stale,
                resolution_note=f"Chapter re-parsed with the current parser (parse revision v{revision.version}).",
                actor_id=ACTOR_ID,
            )

    def _log(self, document: Document, result: ReparseResult, *, actor_id: str, run_id: str | None, now: datetime) -> None:
        payload = {"kind": "chapter_reparse", "run_id": run_id, **result.to_json()}
        history = list((document.metadata_json or {}).get(REPARSE_HISTORY_KEY) or [])[-9:]
        history.append(
            {
                "at": now.isoformat(),
                "parse_revision_id": result.parse_revision_id,
                "refreshed_chapter_ids": result.refreshed_chapter_ids,
                "parser_fingerprint": result.parser_fingerprint,
                "run_id": run_id,
            }
        )
        document.metadata_json = {**(document.metadata_json or {}), REPARSE_HISTORY_KEY: history}
        self.session.add(
            AuditEvent(
                object_type="document",
                object_id=document.id,
                action="document.chapters_reparsed",
                actor_type=ActorType.SYSTEM,
                actor_id=actor_id,
                payload_json=payload,
                created_at=now,
            )
        )
        emit_event(self.session, kind=DOCUMENT_REPARSED, run_id=run_id, document_id=document.id, actor_kind="system", actor_id=actor_id, payload=payload)

    # --- undo --------------------------------------------------------------------------------

    def revert(self, document_id: str, parse_revision_id: str, *, actor_id: str = ACTOR_ID) -> dict[str, Any]:
        """Put back the structure a re-parse replaced, while nothing of the new one is translated."""
        revision = self.session.get(DocumentParseRevision, parse_revision_id)
        if revision is None or revision.document_id != document_id or (revision.metadata_json or {}).get("kind") != "chapter_reparse":
            raise ReparseRefused("找不到这次重新解析的记录。")
        if (revision.metadata_json or {}).get("reverted_at"):
            raise ReparseRefused("这次重新解析已经撤销过了。")
        chapters = dict((revision.metadata_json or {}).get("chapters") or {})
        new_packets = [packet_id for record in chapters.values() for packet_id in record.get("new_packet_ids") or []]
        if new_packets and self.session.scalar(select(func.count(TranslationRun.id)).where(TranslationRun.packet_id.in_(new_packets))):
            raise ReparseRefused("重新解析后的章节已经开始翻译，不能再撤销。")
        document = self.session.get(Document, document_id)
        now = _utcnow()
        for chapter_id, record in chapters.items():
            chapter = self.session.get(Chapter, chapter_id)
            new_block_ids = list(record.get("new_block_ids") or [])
            if new_packets:
                self.session.execute(delete(PacketSentenceMap).where(PacketSentenceMap.packet_id.in_(list(record.get("new_packet_ids") or []) or ["-"])))
                self.session.execute(delete(TranslationPacket).where(TranslationPacket.id.in_(list(record.get("new_packet_ids") or []) or ["-"])))
            self.session.execute(delete(DocumentImage).where(DocumentImage.block_id.in_(new_block_ids or ["-"])))
            self.session.execute(delete(Sentence).where(Sentence.block_id.in_(new_block_ids or ["-"])))
            self.session.execute(delete(Block).where(Block.id.in_(new_block_ids or ["-"])))
            self.session.flush()
            for block in self.session.scalars(select(Block).where(Block.id.in_(list(record.get("old_block_ids") or []) or ["-"]))).all():
                metadata = dict(block.source_span_json or {})
                block.ordinal = int(metadata.pop("reparse_original_ordinal", block.ordinal))
                metadata.pop("reparse_superseded_by_revision_id", None)
                block.source_span_json = metadata
                block.status = ArtifactStatus.ACTIVE
                block.updated_at = now
            self.session.flush()
            old_sentences = list(
                self.session.scalars(select(Sentence).where(Sentence.id.in_(list(record.get("old_sentence_ids") or []) or ["-"]))).all()
            )
            for sentence in old_sentences:
                sentence.retired_by_revision_id = None
                sentence.updated_at = now
            self.session.flush()
            if chapter is not None:
                blocks = self._active_blocks(chapter.id)
                self._build_packets(document, chapter, blocks, old_sentences, now)
                chapter.status = ChapterStatus.PACKET_BUILT
        revision.status = ParseRevisionStatus.INVALIDATED
        revision.metadata_json = {**(revision.metadata_json or {}), "reverted_at": now.isoformat(), "reverted_by": actor_id}
        payload = {"kind": "chapter_reparse_reverted", "parse_revision_id": revision.id, "chapter_ids": list(chapters)}
        self.session.add(
            AuditEvent(
                object_type="document",
                object_id=document_id,
                action="document.chapters_reparse_reverted",
                actor_type=ActorType.SYSTEM,
                actor_id=actor_id,
                payload_json=payload,
                created_at=now,
            )
        )
        emit_event(self.session, kind=DOCUMENT_REPARSED, document_id=document_id, actor_kind="system", actor_id=actor_id, payload=payload)
        self.session.flush()
        return payload
