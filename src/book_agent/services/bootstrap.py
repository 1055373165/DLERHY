from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Iterable

from book_agent.core.ids import stable_id
from book_agent.domain.document_titles import resolve_document_titles
from book_agent.domain.block_rules import protected_policy_for_block, translatability_for_block
from book_agent.domain.context.builders import (
    BookProfileBuilder,
    ChapterBriefBuilder,
    ChapterTranslationMemoryBuilder,
    ContextPacketBuilder,
)
from book_agent.domain.enums import (
    ArtifactStatus,
    BlockType,
    ChapterStatus,
    DocumentStatus,
    JobScopeType,
    JobStatus,
    JobType,
    Severity,
    SourceType,
)
from book_agent.domain.models import (
    Block,
    BookProfile,
    Chapter,
    Document,
    DocumentImage,
    JobRun,
    MemorySnapshot,
    Sentence,
)
from book_agent.domain.models.translation import PacketSentenceMap, TranslationPacket
from book_agent.domain.segmentation.sentences import EnglishSentenceSegmenter
from book_agent.domain.structure.epub import EPUBParser
from book_agent.domain.structure.ocr import OcrPdfParser
from book_agent.domain.structure.pdf import PDFParser, PdfFileProfiler, PdfFileProfile
from book_agent.domain.structure.models import ParsedBlock, ParsedChapter


@dataclass(slots=True)
class ParseArtifacts:
    document: Document
    chapters: list[Chapter]
    blocks: list[Block]
    job_run: JobRun
    document_images: list[DocumentImage] = field(default_factory=list)


@dataclass(slots=True)
class SegmentationArtifacts:
    document: Document
    chapters: list[Chapter]
    sentences: list[Sentence]
    job_run: JobRun


@dataclass(slots=True)
class BootstrapArtifacts:
    document: Document
    chapters: list[Chapter]
    blocks: list[Block]
    sentences: list[Sentence]
    book_profile: BookProfile | None = None
    memory_snapshots: list[MemorySnapshot] = field(default_factory=list)
    translation_packets: list[TranslationPacket] = field(default_factory=list)
    packet_sentence_maps: list[PacketSentenceMap] = field(default_factory=list)
    job_runs: list[JobRun] = field(default_factory=list)
    document_images: list[DocumentImage] = field(default_factory=list)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_text(text: str) -> str:
    return " ".join(text.split()).strip()


def _compose_anchor(href: str, anchor: str | None) -> str:
    return f"{href}#{anchor}" if anchor else href


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _pdf_chapter_risk_level(
    layout_risk: str | None,
    parse_confidence: float | None,
    structure_flags: list[str],
) -> Severity | None:
    if layout_risk == "high":
        return Severity.CRITICAL
    if layout_risk == "medium":
        return Severity.HIGH
    if parse_confidence is not None and parse_confidence < 0.85:
        return Severity.MEDIUM
    if structure_flags:
        return Severity.LOW
    return Severity.LOW if layout_risk == "low" else None


class IngestService:
    def __init__(self, pdf_profiler: PdfFileProfiler | None = None):
        self.pdf_profiler = pdf_profiler or PdfFileProfiler()

    def ingest(self, file_path: str | Path) -> tuple[Document, JobRun]:
        path = Path(file_path)
        fingerprint = sha256(path.read_bytes()).hexdigest()
        source_type, pdf_profile = self._detect_source_type(path)
        now = _utcnow()

        document = Document(
            id=stable_id("document", fingerprint),
            source_type=source_type,
            file_fingerprint=fingerprint,
            source_path=str(path),
            status=DocumentStatus.INGESTED,
            metadata_json={
                "file_name": path.name,
                **({"pdf_profile": pdf_profile.to_dict()} if pdf_profile is not None else {}),
            },
            created_at=now,
            updated_at=now,
        )
        job = JobRun(
            id=stable_id("job", JobType.INGEST.value, document.id),
            job_type=JobType.INGEST,
            scope_type=JobScopeType.DOCUMENT,
            scope_id=document.id,
            status=JobStatus.SUCCEEDED,
            started_at=now,
            ended_at=now,
            created_at=now,
        )
        return document, job

    def _detect_source_type(self, path: Path) -> tuple[SourceType, PdfFileProfile | None]:
        suffix = path.suffix.lower()
        if suffix == ".epub":
            return SourceType.EPUB, None
        if suffix == ".pdf":
            pdf_profile = self.pdf_profiler.profile(path)
            if pdf_profile.pdf_kind == "text_pdf":
                return SourceType.PDF_TEXT, pdf_profile
            if pdf_profile.pdf_kind == "mixed_pdf":
                return SourceType.PDF_MIXED, pdf_profile
            return SourceType.PDF_SCAN, pdf_profile
        raise ValueError(f"Unsupported source file type: {path.suffix}")


class ParseService:
    def __init__(
        self,
        epub_parser: EPUBParser | None = None,
        pdf_parser: PDFParser | None = None,
        ocr_pdf_parser: OcrPdfParser | None = None,
    ):
        self.epub_parser = epub_parser or EPUBParser()
        self.pdf_parser = pdf_parser or PDFParser()
        self.ocr_pdf_parser = ocr_pdf_parser or OcrPdfParser()

    def parse(self, document: Document, file_path: str | Path) -> ParseArtifacts:
        if document.source_type == SourceType.EPUB:
            parsed = self.epub_parser.parse(file_path)
        elif document.source_type == SourceType.PDF_TEXT:
            pdf_profile = document.metadata_json.get("pdf_profile")
            if (
                isinstance(pdf_profile, dict)
                and pdf_profile.get("layout_risk") == "high"
                and pdf_profile.get("recovery_lane") != "academic_paper"
            ):
                raise ValueError("P1-A only supports low-risk or medium-risk text PDFs; layout_risk=high")
            parsed = self.pdf_parser.parse(file_path, profile=pdf_profile if isinstance(pdf_profile, dict) else None)
        elif document.source_type == SourceType.PDF_MIXED:
            pdf_profile = document.metadata_json.get("pdf_profile")
            parsed = self.ocr_pdf_parser.parse(file_path, profile=pdf_profile if isinstance(pdf_profile, dict) else None)
        elif document.source_type == SourceType.PDF_SCAN:
            pdf_profile = document.metadata_json.get("pdf_profile")
            parsed = self.ocr_pdf_parser.parse(file_path, profile=pdf_profile if isinstance(pdf_profile, dict) else None)
        else:
            raise ValueError(f"Unsupported source type: {document.source_type}")

        now = _utcnow()
        pdf_profile = document.metadata_json.get("pdf_profile")
        effective_pdf_profile = pdf_profile if isinstance(pdf_profile, dict) else {}
        resolved_titles = resolve_document_titles(
            source_type=document.source_type,
            parsed_title=parsed.title,
            parsed_metadata=parsed.metadata,
            source_path=file_path,
            src_lang=parsed.language,
            tgt_lang=document.tgt_lang,
            pdf_recovery_lane=(
                str(effective_pdf_profile.get("recovery_lane")).strip() or None
                if effective_pdf_profile
                else None
            ),
        )
        document.title = resolved_titles.title
        document.title_src = resolved_titles.title_src
        document.title_tgt = resolved_titles.title_tgt
        document.author = parsed.author
        if parsed.language:
            document.src_lang = parsed.language
        document.metadata_json = {
            **document.metadata_json,
            **parsed.metadata,
            "document_title": {
                "title": resolved_titles.title,
                "src": resolved_titles.title_src,
                "tgt": resolved_titles.title_tgt,
                "resolution_source": resolved_titles.resolution_source,
            },
        }
        document.status = DocumentStatus.PARSED
        document.updated_at = now

        chapters: list[Chapter] = []
        blocks: list[Block] = []
        document_images: list[DocumentImage] = []
        for ordinal, parsed_chapter in enumerate(parsed.chapters, start=1):
            chapter = self._build_chapter(document, parsed_chapter, ordinal, now)
            chapters.append(chapter)
            chapter_block_pairs: list[tuple[ParsedBlock, Block]] = []
            for parsed_block in parsed_chapter.blocks:
                block = self._build_block(document, chapter, parsed_block, now)
                chapter_block_pairs.append((parsed_block, block))
            self._materialize_pdf_block_relations(chapter_block_pairs)
            for parsed_block, block in chapter_block_pairs:
                blocks.append(block)
                document_image = self._build_document_image(document, block, parsed_block, now)
                if document_image is not None:
                    document_images.append(document_image)

        job = JobRun(
            id=stable_id("job", JobType.PARSE.value, document.id, document.parser_version),
            job_type=JobType.PARSE,
            scope_type=JobScopeType.DOCUMENT,
            scope_id=document.id,
            status=JobStatus.SUCCEEDED,
            started_at=now,
            ended_at=now,
            created_at=now,
        )
        return ParseArtifacts(
            document=document,
            chapters=chapters,
            blocks=blocks,
            job_run=job,
            document_images=document_images,
        )

    def _build_chapter(
        self,
        document: Document,
        parsed_chapter: ParsedChapter,
        ordinal: int,
        now: datetime,
    ) -> Chapter:
        first_anchor = parsed_chapter.blocks[0].anchor if parsed_chapter.blocks else None
        last_anchor = parsed_chapter.blocks[-1].anchor if parsed_chapter.blocks else None
        chapter_metadata, risk_level = self._chapter_metadata(document, parsed_chapter)
        return Chapter(
            id=stable_id("chapter", document.id, ordinal, parsed_chapter.href),
            document_id=document.id,
            ordinal=ordinal,
            title_src=parsed_chapter.title,
            anchor_start=_compose_anchor(parsed_chapter.href, first_anchor),
            anchor_end=_compose_anchor(parsed_chapter.href, last_anchor),
            status=ChapterStatus.READY,
            risk_level=risk_level,
            metadata_json=chapter_metadata,
            created_at=now,
            updated_at=now,
        )

    def _chapter_metadata(
        self,
        document: Document,
        parsed_chapter: ParsedChapter,
    ) -> tuple[dict[str, object], Severity | None]:
        metadata: dict[str, object] = {"href": parsed_chapter.href, **parsed_chapter.metadata}
        if document.source_type not in {SourceType.PDF_TEXT, SourceType.PDF_MIXED}:
            return metadata, None

        parse_confidences = [
            float(block.parse_confidence)
            for block in parsed_chapter.blocks
            if block.parse_confidence is not None
        ]
        parse_confidence = _mean(parse_confidences)
        role_counts = Counter(
            str(block.metadata.get("pdf_block_role"))
            for block in parsed_chapter.blocks
            if block.metadata.get("pdf_block_role")
        )
        page_family_counts = Counter(
            str(block.metadata.get("pdf_page_family"))
            for block in parsed_chapter.blocks
            if block.metadata.get("pdf_page_family")
        )
        recovery_flags = sorted(
            {
                str(flag)
                for block in parsed_chapter.blocks
                for flag in block.metadata.get("recovery_flags", [])
            }
        )
        pdf_profile = document.metadata_json.get("pdf_profile", {})
        layout_risk = str(metadata.get("pdf_layout_risk") or pdf_profile.get("layout_risk") or "low")
        source_page_start = metadata.get("source_page_start")
        source_page_end = metadata.get("source_page_end")
        suspicious_pages = [
            int(page)
            for page in pdf_profile.get("suspicious_page_numbers", [])
            if (
                isinstance(source_page_start, int)
                and isinstance(source_page_end, int)
                and source_page_start <= int(page) <= source_page_end
            )
        ]
        structure_flags = [
            *([f"layout_risk_{layout_risk}"] if layout_risk != "low" else []),
            *(
                [f"page_family_{metadata.get('pdf_section_family')}"]
                if metadata.get("pdf_section_family") not in {None, "body"}
                else []
            ),
            *recovery_flags,
        ]
        risk_level = _pdf_chapter_risk_level(layout_risk, parse_confidence, structure_flags)
        metadata.update(
            {
                "parse_confidence": round(parse_confidence, 3) if parse_confidence is not None else None,
                "pdf_layout_risk": layout_risk,
                "pdf_role_counts": dict(role_counts),
                "pdf_page_family_counts": dict(page_family_counts),
                "structure_flags": structure_flags,
                "suspicious_page_numbers": suspicious_pages,
            }
        )
        return metadata, risk_level

    def _build_block(
        self,
        document: Document,
        chapter: Chapter,
        parsed_block: ParsedBlock,
        now: datetime,
    ) -> Block:
        block_type = BlockType(parsed_block.block_type)
        return Block(
            id=stable_id(
                "block",
                document.id,
                chapter.id,
                parsed_block.ordinal,
                parsed_block.source_path,
                parsed_block.anchor or "no-anchor",
            ),
            chapter_id=chapter.id,
            ordinal=parsed_block.ordinal,
            block_type=block_type,
            source_text=parsed_block.text,
            normalized_text=_normalize_text(parsed_block.text),
            source_anchor=_compose_anchor(parsed_block.source_path, parsed_block.anchor),
            source_span_json={
                "source_path": parsed_block.source_path,
                "anchor": parsed_block.anchor,
                **parsed_block.metadata,
            },
            parse_confidence=parsed_block.parse_confidence if parsed_block.parse_confidence is not None else 1.0,
            protected_policy=protected_policy_for_block(block_type.value, parsed_block.metadata),
            status=ArtifactStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )

    def _build_document_image(
        self,
        document: Document,
        block: Block,
        parsed_block: ParsedBlock,
        now: datetime,
    ) -> DocumentImage | None:
        if block.block_type not in {BlockType.IMAGE, BlockType.FIGURE}:
            return None

        metadata = parsed_block.metadata or {}
        source_page_start = metadata.get("source_page_start")
        if not isinstance(source_page_start, int):
            return None

        source_bbox_json = metadata.get("source_bbox_json")
        bbox_json = source_bbox_json if isinstance(source_bbox_json, dict) else {"regions": []}
        storage_path = metadata.get("storage_path")
        if not isinstance(storage_path, str) or not storage_path.strip():
            storage_path = f"document-images/{document.id}/{block.id}.png"

        width_px = metadata.get("image_width_px")
        height_px = metadata.get("image_height_px")
        return DocumentImage(
            id=stable_id("document-image", document.id, block.id),
            document_id=document.id,
            block_id=block.id,
            page_number=source_page_start,
            image_type=str(metadata.get("image_type") or block.block_type.value),
            storage_path=storage_path,
            bbox_json=bbox_json,
            ocr_text=str(metadata["ocr_text"]) if metadata.get("ocr_text") is not None else None,
            latex=str(metadata["latex"]) if metadata.get("latex") is not None else None,
            alt_text=(
                str(metadata["image_alt"])
                if metadata.get("image_alt") is not None
                else str(metadata["alt_text"])
                if metadata.get("alt_text") is not None
                else str(metadata["linked_caption_text"])
                if metadata.get("linked_caption_text") is not None
                else None
            ),
            width_px=int(width_px) if isinstance(width_px, (int, float)) else None,
            height_px=int(height_px) if isinstance(height_px, (int, float)) else None,
            metadata_json={
                "source_path": parsed_block.source_path,
                "anchor": parsed_block.anchor,
                "image_ext": metadata.get("image_ext"),
                "linked_caption_block_id": metadata.get("linked_caption_block_id"),
                "storage_status": "logical_only",
            },
            created_at=now,
        )

    def _materialize_pdf_block_relations(self, block_pairs: list[tuple[ParsedBlock, Block]]) -> None:
        if not block_pairs:
            return
        block_id_by_source_anchor = {
            block.source_anchor: block.id
            for _parsed_block, block in block_pairs
            if block.source_anchor
        }
        for parsed_block, block in block_pairs:
            metadata = parsed_block.metadata or {}
            block_metadata = dict(block.source_span_json or {})
            linked_caption_source_anchor = metadata.get("linked_caption_source_anchor")
            if isinstance(linked_caption_source_anchor, str):
                linked_caption_block_id = block_id_by_source_anchor.get(linked_caption_source_anchor)
                if linked_caption_block_id:
                    metadata["linked_caption_block_id"] = linked_caption_block_id
                    block_metadata["linked_caption_block_id"] = linked_caption_block_id
            caption_for_source_anchor = metadata.get("caption_for_source_anchor")
            if isinstance(caption_for_source_anchor, str):
                caption_for_block_id = block_id_by_source_anchor.get(caption_for_source_anchor)
                if caption_for_block_id:
                    metadata["caption_for_block_id"] = caption_for_block_id
                    block_metadata["caption_for_block_id"] = caption_for_block_id
            artifact_group_context_source_anchors = metadata.get("artifact_group_context_source_anchors")
            if isinstance(artifact_group_context_source_anchors, list):
                artifact_group_context_block_ids = [
                    block_id_by_source_anchor.get(str(source_anchor))
                    for source_anchor in artifact_group_context_source_anchors
                    if isinstance(source_anchor, str)
                ]
                artifact_group_context_block_ids = [
                    block_id
                    for block_id in artifact_group_context_block_ids
                    if isinstance(block_id, str) and block_id.strip()
                ]
                if artifact_group_context_block_ids:
                    metadata["artifact_group_context_block_ids"] = artifact_group_context_block_ids
                    block_metadata["artifact_group_context_block_ids"] = artifact_group_context_block_ids
            artifact_group_source_anchor = metadata.get("artifact_group_source_anchor")
            if isinstance(artifact_group_source_anchor, str):
                artifact_group_block_id = block_id_by_source_anchor.get(artifact_group_source_anchor)
                if artifact_group_block_id:
                    metadata["artifact_group_block_id"] = artifact_group_block_id
                    block_metadata["artifact_group_block_id"] = artifact_group_block_id
            block.source_span_json = block_metadata


class SegmentationService:
    def __init__(self, segmenter: EnglishSentenceSegmenter | None = None):
        self.segmenter = segmenter or EnglishSentenceSegmenter()

    def segment(self, document: Document, chapters: list[Chapter], blocks: list[Block]) -> SegmentationArtifacts:
        now = _utcnow()
        blocks_by_chapter: dict[str, list[Block]] = {}
        for block in blocks:
            blocks_by_chapter.setdefault(block.chapter_id, []).append(block)

        sentences: list[Sentence] = []
        for chapter in chapters:
            for block in sorted(blocks_by_chapter.get(chapter.id, []), key=lambda item: item.ordinal):
                for sentence in self._build_sentences(document, chapter, block, now):
                    sentences.append(sentence)
            chapter.status = ChapterStatus.SEGMENTED
            chapter.updated_at = now

        job = JobRun(
            id=stable_id("job", JobType.SEGMENT.value, document.id, document.segmentation_version),
            job_type=JobType.SEGMENT,
            scope_type=JobScopeType.DOCUMENT,
            scope_id=document.id,
            status=JobStatus.SUCCEEDED,
            started_at=now,
            ended_at=now,
            created_at=now,
        )
        return SegmentationArtifacts(document=document, chapters=chapters, sentences=sentences, job_run=job)

    def _build_sentences(
        self,
        document: Document,
        chapter: Chapter,
        block: Block,
        now: datetime,
    ) -> list[Sentence]:
        segmented = self.segmenter.segment_text(block.normalized_text or block.source_text)
        if block.block_type in {
            BlockType.HEADING, BlockType.CODE, BlockType.FOOTNOTE, BlockType.CAPTION,
            BlockType.FIGURE, BlockType.EQUATION, BlockType.IMAGE,
        }:
            segmented = [block.normalized_text or block.source_text]

        translatable, nontranslatable_reason, initial_status = translatability_for_block(
            block.block_type,
            block.source_span_json,
        )
        output: list[Sentence] = []
        for ordinal, text in enumerate(segmented, start=1):
            output.append(
                Sentence(
                    id=stable_id(
                        "sentence",
                        document.id,
                        block.id,
                        document.segmentation_version,
                        ordinal,
                    ),
                    block_id=block.id,
                    chapter_id=chapter.id,
                    document_id=document.id,
                    ordinal_in_block=ordinal,
                    source_text=text,
                    normalized_text=_normalize_text(text),
                    source_lang=document.src_lang,
                    translatable=translatable,
                    nontranslatable_reason=nontranslatable_reason,
                    source_anchor=block.source_anchor,
                    source_span_json={
                        "block_id": block.id,
                        "block_type": block.block_type.value,
                        "ordinal_in_block": ordinal,
                    },
                    upstream_confidence=block.parse_confidence,
                    sentence_status=initial_status,
                    active_version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
        return output


class BootstrapPipeline:
    def __init__(
        self,
        ingest_service: IngestService | None = None,
        parse_service: ParseService | None = None,
        segmentation_service: SegmentationService | None = None,
        profile_builder: BookProfileBuilder | None = None,
        chapter_brief_builder: ChapterBriefBuilder | None = None,
        chapter_translation_memory_builder: ChapterTranslationMemoryBuilder | None = None,
        context_packet_builder: ContextPacketBuilder | None = None,
    ):
        self.ingest_service = ingest_service or IngestService()
        self.parse_service = parse_service or ParseService()
        self.segmentation_service = segmentation_service or SegmentationService()
        self.profile_builder = profile_builder or BookProfileBuilder()
        self.chapter_brief_builder = chapter_brief_builder or ChapterBriefBuilder()
        self.chapter_translation_memory_builder = (
            chapter_translation_memory_builder or ChapterTranslationMemoryBuilder()
        )
        self.context_packet_builder = context_packet_builder or ContextPacketBuilder()

    def run(self, file_path: str | Path) -> BootstrapArtifacts:
        document, ingest_job = self.ingest_service.ingest(file_path)
        parse_artifacts = self.parse_service.parse(document, file_path)
        segment_artifacts = self.segmentation_service.segment(
            parse_artifacts.document,
            parse_artifacts.chapters,
            parse_artifacts.blocks,
        )

        profile_result = self.profile_builder.build(
            document=parse_artifacts.document,
            chapters=parse_artifacts.chapters,
            blocks=parse_artifacts.blocks,
        )
        chapter_briefs = self.chapter_brief_builder.build_many(
            document=parse_artifacts.document,
            chapters=parse_artifacts.chapters,
            blocks=parse_artifacts.blocks,
            sentences=segment_artifacts.sentences,
        )
        chapter_translation_memories = self.chapter_translation_memory_builder.build_many(
            document=parse_artifacts.document,
            chapters=parse_artifacts.chapters,
            chapter_briefs=chapter_briefs,
        )
        packet_result = self.context_packet_builder.build_many(
            document=parse_artifacts.document,
            chapters=parse_artifacts.chapters,
            blocks=parse_artifacts.blocks,
            sentences=segment_artifacts.sentences,
            book_profile=profile_result.book_profile,
            chapter_briefs=chapter_briefs,
            termbase_snapshot=profile_result.termbase_snapshot,
            entity_snapshot=profile_result.entity_snapshot,
        )

        now = _utcnow()
        for chapter in parse_artifacts.chapters:
            chapter.status = ChapterStatus.PACKET_BUILT
            chapter.updated_at = now
        document.status = DocumentStatus.ACTIVE
        document.active_book_profile_version = profile_result.book_profile.version
        document.updated_at = now

        return BootstrapArtifacts(
            document=document,
            chapters=parse_artifacts.chapters,
            blocks=parse_artifacts.blocks,
            sentences=segment_artifacts.sentences,
            book_profile=profile_result.book_profile,
            memory_snapshots=[
                profile_result.termbase_snapshot,
                profile_result.entity_snapshot,
                *chapter_briefs,
                *chapter_translation_memories,
            ],
            translation_packets=packet_result.translation_packets,
            packet_sentence_maps=packet_result.packet_sentence_maps,
            job_runs=[
                ingest_job,
                parse_artifacts.job_run,
                segment_artifacts.job_run,
                profile_result.job_run,
                *profile_result.seed_jobs,
                *self._build_brief_jobs(document.id, chapter_briefs),
                *packet_result.job_runs,
            ],
            document_images=parse_artifacts.document_images,
        )

    def _build_brief_jobs(self, document_id: str, chapter_briefs: Iterable[MemorySnapshot]) -> list[JobRun]:
        now = _utcnow()
        jobs: list[JobRun] = []
        for brief in chapter_briefs:
            jobs.append(
                JobRun(
                    id=stable_id("job", JobType.BRIEF.value, document_id, brief.scope_id, brief.version),
                    job_type=JobType.BRIEF,
                    scope_type=JobScopeType.CHAPTER,
                    scope_id=brief.scope_id,
                    status=JobStatus.SUCCEEDED,
                    started_at=now,
                    ended_at=now,
                    created_at=now,
                )
            )
        return jobs
