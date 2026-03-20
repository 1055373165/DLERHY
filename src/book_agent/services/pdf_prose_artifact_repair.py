from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from book_agent.core.ids import stable_id
from book_agent.domain.block_rules import block_is_context_translatable
from book_agent.domain.context.builders import ContextPacketBuilder
from book_agent.domain.enums import BlockType, SentenceStatus
from book_agent.domain.models import Block, BookProfile, Chapter, Document, MemorySnapshot, Sentence
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.services.bootstrap import SegmentationService
from book_agent.services.export import ExportService
from book_agent.workers.contracts import ContextPacket
from book_agent.workers.translator import EchoTranslationWorker, TranslationTask, TranslationWorker


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class ProseArtifactRepairCandidate:
    mode: str
    chapter_id: str
    chapter_ordinal: int
    chapter_title: str | None
    lead_block_id: str
    continuation_block_ids: list[str]
    merged_source_text: str


@dataclass(slots=True)
class ProseArtifactRepairResult:
    document_id: str
    candidate_count: int
    repaired_chain_count: int
    repaired_block_ids: list[str]
    skipped_block_ids: list[str]
    token_in: int = 0
    token_out: int = 0
    total_cost_usd: float = 0.0
    failed_candidates: list[dict[str, Any]] = field(default_factory=list)


class PdfProseArtifactRepairService:
    def __init__(
        self,
        session: Session,
        *,
        worker: TranslationWorker | None = None,
        bootstrap_repository: BootstrapRepository | None = None,
        segmentation_service: SegmentationService | None = None,
        context_packet_builder: ContextPacketBuilder | None = None,
    ):
        self.session = session
        self.worker = worker or EchoTranslationWorker()
        self.bootstrap_repository = bootstrap_repository or BootstrapRepository(session)
        self.segmentation_service = segmentation_service or SegmentationService()
        self.context_packet_builder = context_packet_builder or ContextPacketBuilder()
        self.export_service = ExportService(repository=None)

    def scan_document(self, document_id: str) -> list[ProseArtifactRepairCandidate]:
        bundle = self.bootstrap_repository.load_document_bundle(document_id)
        skipped_block_ids = self._repair_skip_ids(bundle)
        candidates: list[ProseArtifactRepairCandidate] = []
        for chapter_bundle in bundle.chapters:
            chapter_blocks = sorted(chapter_bundle.blocks, key=lambda item: item.ordinal)
            sentences_by_block: dict[str, list[Sentence]] = {}
            for sentence in chapter_bundle.sentences:
                sentences_by_block.setdefault(sentence.block_id, []).append(sentence)
            consumed_block_ids = set(skipped_block_ids)
            index = 0
            while index < len(chapter_blocks):
                lead_block = chapter_blocks[index]
                if lead_block.id in consumed_block_ids:
                    index += 1
                    continue
                if not self._eligible_lead_block(lead_block):
                    if self._eligible_standalone_block(
                        lead_block,
                        sentences_by_block.get(lead_block.id, []),
                    ):
                        candidates.append(
                            ProseArtifactRepairCandidate(
                                mode="standalone",
                                chapter_id=chapter_bundle.chapter.id,
                                chapter_ordinal=chapter_bundle.chapter.ordinal,
                                chapter_title=chapter_bundle.chapter.title_src,
                                lead_block_id=lead_block.id,
                                continuation_block_ids=[],
                                merged_source_text=lead_block.source_text or "",
                            )
                        )
                        consumed_block_ids.add(lead_block.id)
                    index += 1
                    continue
                chain = self._collect_continuation_chain(
                    chapter_blocks=chapter_blocks,
                    lead_index=index,
                    skipped_block_ids=consumed_block_ids,
                    sentences_by_block=sentences_by_block,
                )
                if chain:
                    continuation_blocks = chain
                    merged_source_text = self._merge_text_chain([lead_block, *continuation_blocks])
                    candidates.append(
                        ProseArtifactRepairCandidate(
                            mode="chain",
                            chapter_id=chapter_bundle.chapter.id,
                            chapter_ordinal=chapter_bundle.chapter.ordinal,
                            chapter_title=chapter_bundle.chapter.title_src,
                            lead_block_id=lead_block.id,
                            continuation_block_ids=[block.id for block in continuation_blocks],
                            merged_source_text=merged_source_text,
                        )
                    )
                    consumed_block_ids.update(block.id for block in continuation_blocks)
                    index += 1 + len(continuation_blocks)
                    continue
                index += 1
        return candidates

    def apply(
        self,
        document_id: str,
        *,
        candidates: list[ProseArtifactRepairCandidate] | None = None,
        progress_callback: Callable[[int, int, ProseArtifactRepairCandidate, str, str | None], None] | None = None,
    ) -> ProseArtifactRepairResult:
        bundle = self.bootstrap_repository.load_document_bundle(document_id)
        candidates = list(candidates or self.scan_document(document_id))
        chapter_by_id = {chapter_bundle.chapter.id: chapter_bundle for chapter_bundle in bundle.chapters}
        repaired_block_ids: list[str] = []
        skipped_block_ids: list[str] = []
        repaired_chain_count = 0
        token_in = 0
        token_out = 0
        total_cost_usd = 0.0
        failed_candidates: list[dict[str, Any]] = []
        now = _utcnow()

        total = len(candidates)
        for index, candidate in enumerate(candidates, start=1):
            try:
                chapter_bundle = chapter_by_id.get(candidate.chapter_id)
                if chapter_bundle is None:
                    skipped_block_ids.extend(candidate.continuation_block_ids or [candidate.lead_block_id])
                    if progress_callback is not None:
                        progress_callback(index, total, candidate, "skipped", "chapter_bundle_missing")
                    continue
                lead_block = next((block for block in chapter_bundle.blocks if block.id == candidate.lead_block_id), None)
                if lead_block is None:
                    skipped_block_ids.extend(candidate.continuation_block_ids or [candidate.lead_block_id])
                    if progress_callback is not None:
                        progress_callback(index, total, candidate, "skipped", "lead_block_missing")
                    continue
                continuation_blocks: list[Block] = []
                if candidate.continuation_block_ids:
                    continuation_id_set = set(candidate.continuation_block_ids)
                    continuation_blocks = [
                        block
                        for block in chapter_bundle.blocks
                        if block.id in continuation_id_set
                    ]
                    if not continuation_blocks:
                        skipped_block_ids.extend(candidate.continuation_block_ids)
                        if progress_callback is not None:
                            progress_callback(index, total, candidate, "skipped", "continuation_blocks_missing")
                        continue
                merged_target_text, usage = self._translate_merged_chain(
                    document=bundle.document,
                    chapter=chapter_bundle.chapter,
                    chapter_blocks=chapter_bundle.blocks,
                    chapter_sentences=chapter_bundle.sentences,
                    book_profile=bundle.book_profile,
                    memory_snapshots=bundle.memory_snapshots,
                    lead_block=lead_block,
                    continuation_blocks=continuation_blocks,
                    force_paragraph_current=(candidate.mode == "standalone"),
                )
                if not merged_target_text.strip():
                    skipped_block_ids.extend(candidate.continuation_block_ids or [candidate.lead_block_id])
                    if progress_callback is not None:
                        progress_callback(index, total, candidate, "skipped", "empty_translation")
                    continue

                metadata = dict(lead_block.source_span_json or {})
                recovery_flags = list(metadata.get("recovery_flags") or [])
                recovery_flags.append(
                    "persisted_prose_artifact_chain_repaired"
                    if candidate.mode == "chain"
                    else "persisted_prose_artifact_block_repaired"
                )
                repaired_ids = candidate.continuation_block_ids or [candidate.lead_block_id]
                metadata.update(
                    {
                        "repair_source_text": candidate.merged_source_text,
                        "repair_target_text": merged_target_text,
                        "repair_block_type": BlockType.PARAGRAPH.value,
                        "repair_generated_at": now.isoformat(),
                        "repair_worker_name": self.worker.metadata().worker_name,
                        "repair_prompt_version": self.worker.metadata().prompt_version,
                        "recovery_flags": list(dict.fromkeys(recovery_flags)),
                    }
                )
                if candidate.continuation_block_ids:
                    metadata["repair_skip_block_ids"] = candidate.continuation_block_ids
                lead_block.source_span_json = metadata
                lead_block.updated_at = now
                self.session.merge(lead_block)
                repaired_block_ids.extend(repaired_ids)
                repaired_chain_count += 1
                token_in += int(getattr(usage, "token_in", 0) or 0)
                token_out += int(getattr(usage, "token_out", 0) or 0)
                total_cost_usd += float(getattr(usage, "cost_usd", 0.0) or 0.0)
                if progress_callback is not None:
                    progress_callback(index, total, candidate, "repaired", None)
            except Exception as exc:
                failed_candidates.append(
                    {
                        "mode": candidate.mode,
                        "chapter_id": candidate.chapter_id,
                        "chapter_ordinal": candidate.chapter_ordinal,
                        "chapter_title": candidate.chapter_title,
                        "lead_block_id": candidate.lead_block_id,
                        "continuation_block_ids": list(candidate.continuation_block_ids),
                        "error": str(exc),
                    }
                )
                skipped_block_ids.extend(candidate.continuation_block_ids or [candidate.lead_block_id])
                if progress_callback is not None:
                    progress_callback(index, total, candidate, "failed", str(exc))

        self.session.flush()
        return ProseArtifactRepairResult(
            document_id=document_id,
            candidate_count=len(candidates),
            repaired_chain_count=repaired_chain_count,
            repaired_block_ids=sorted(set(repaired_block_ids)),
            skipped_block_ids=sorted(set(skipped_block_ids)),
            token_in=token_in,
            token_out=token_out,
            total_cost_usd=round(total_cost_usd, 6),
            failed_candidates=failed_candidates,
        )

    def _repair_skip_ids(self, bundle) -> set[str]:
        skipped: set[str] = set()
        for chapter_bundle in bundle.chapters:
            for block in chapter_bundle.blocks:
                metadata = dict(block.source_span_json or {})
                repair_skip_ids = metadata.get("repair_skip_block_ids")
                if isinstance(repair_skip_ids, list):
                    skipped.update(
                        str(item)
                        for item in repair_skip_ids
                        if isinstance(item, str) and item.strip()
                    )
        return skipped

    def _eligible_lead_block(self, block: Block) -> bool:
        if block.block_type != BlockType.PARAGRAPH:
            return False
        metadata = dict(block.source_span_json or {})
        if bool(metadata.get("repair_target_text")):
            return False
        if str(metadata.get("pdf_block_role") or "body") != "body":
            return False
        if (block.source_text or "").rstrip().endswith((".", "!", "?", ":", ";", "\"", "'", "\u201d", "\u2019")):
            return False
        return True

    def _collect_continuation_chain(
        self,
        *,
        chapter_blocks: list[Block],
        lead_index: int,
        skipped_block_ids: set[str],
        sentences_by_block: dict[str, list[Sentence]],
    ) -> list[Block]:
        chain: list[Block] = []
        previous = chapter_blocks[lead_index]
        cursor = lead_index + 1
        while cursor < len(chapter_blocks):
            current = chapter_blocks[cursor]
            if current.id in skipped_block_ids:
                break
            if not self._eligible_continuation_block(
                previous=previous,
                current=current,
                current_sentences=sentences_by_block.get(current.id, []),
            ):
                break
            chain.append(current)
            previous = current
            cursor += 1
        return chain

    def _eligible_continuation_block(
        self,
        *,
        previous: Block,
        current: Block,
        current_sentences: list[Sentence],
    ) -> bool:
        metadata = dict(current.source_span_json or {})
        role = str(metadata.get("pdf_block_role") or "").strip().casefold()
        if role not in {"code_like", "table_like"}:
            return False
        if not self.export_service._looks_like_prose_continuation_artifact_text(current.source_text):
            return False
        if self._block_has_translation(current_sentences):
            return False
        previous_page = previous.source_span_json.get("source_page_end") or previous.source_span_json.get("source_page_start")
        current_page = metadata.get("source_page_start") or metadata.get("source_page_end")
        if isinstance(previous_page, int) and isinstance(current_page, int) and current_page - previous_page > 1:
            return False
        previous_index = previous.source_span_json.get("reading_order_index")
        current_index = metadata.get("reading_order_index")
        if isinstance(previous_index, int) and isinstance(current_index, int) and current_index - previous_index > 1:
            return False
        return True

    def _eligible_standalone_block(self, block: Block, block_sentences: list[Sentence]) -> bool:
        metadata = dict(block.source_span_json or {})
        role = str(metadata.get("pdf_block_role") or "").strip().casefold()
        if role not in {"code_like", "table_like"}:
            return False
        if bool(metadata.get("repair_target_text")):
            return False
        if not self.export_service._looks_like_prose_artifact_text(block.source_text):
            return False
        if self._block_has_translation(block_sentences):
            return False
        return True

    def _merge_text_chain(self, blocks: list[Block]) -> str:
        merged = ""
        for block in blocks:
            merged = self.export_service._merge_render_text_fragments(merged, block.source_text or "")
        return merged

    def _block_has_translation(self, sentences: list[Sentence]) -> bool:
        return any(sentence.sentence_status == SentenceStatus.TRANSLATED for sentence in sentences)

    def _translate_merged_chain(
        self,
        *,
        document: Document,
        chapter: Chapter,
        chapter_blocks: list[Block],
        chapter_sentences: list[Sentence],
        book_profile: BookProfile | None,
        memory_snapshots: list[MemorySnapshot],
        lead_block: Block,
        continuation_blocks: list[Block],
        force_paragraph_current: bool = False,
    ) -> tuple[str, Any]:
        if book_profile is None:
            raise ValueError(f"Book profile missing for document {document.id}")
        chapter_brief = self._latest_snapshot(memory_snapshots, chapter.id, "chapter_brief")
        termbase_snapshot = self._latest_snapshot(memory_snapshots, None, "termbase")
        entity_snapshot = self._latest_snapshot(memory_snapshots, None, "entity_registry")
        if chapter_brief is None or termbase_snapshot is None or entity_snapshot is None:
            raise ValueError(f"Repair prerequisites missing for chapter {chapter.id}")

        merged_source_text = self._merge_text_chain([lead_block, *continuation_blocks])
        temporary_sentences = self._build_temporary_sentences(
            document=document,
            chapter=chapter,
            lead_block=lead_block,
            merged_source_text=merged_source_text,
        )
        sentences_by_block: dict[str, list[Sentence]] = {}
        for sentence in chapter_sentences:
            sentences_by_block.setdefault(sentence.block_id, []).append(sentence)
        sentences_by_block[lead_block.id] = temporary_sentences

        translatable_blocks = [block for block in sorted(chapter_blocks, key=lambda item: item.ordinal) if block_is_context_translatable(block)]
        continuation_ids = {block.id for block in continuation_blocks}
        try:
            lead_index = next(index for index, block in enumerate(translatable_blocks) if block.id == lead_block.id)
            prev_blocks = translatable_blocks[max(0, lead_index - 2):lead_index]
            next_blocks: list[Block] = []
            cursor = lead_index + 1
            while cursor < len(translatable_blocks) and len(next_blocks) < 2:
                candidate = translatable_blocks[cursor]
                if candidate.id in continuation_ids:
                    cursor += 1
                    continue
                next_blocks.append(candidate)
                cursor += 1
        except StopIteration:
            range_end_ordinal = continuation_blocks[-1].ordinal if continuation_blocks else lead_block.ordinal
            prev_blocks = [block for block in translatable_blocks if block.ordinal < lead_block.ordinal][-2:]
            next_blocks = [block for block in translatable_blocks if block.ordinal > range_end_ordinal][:2]

        packet, _maps = self.context_packet_builder._build_packet(
            document=document,
            chapter=chapter,
            block=lead_block,
            prev_blocks=prev_blocks,
            next_blocks=next_blocks,
            sentences_by_block=sentences_by_block,
            book_profile=book_profile,
            chapter_brief=chapter_brief,
            termbase_snapshot=termbase_snapshot,
            entity_snapshot=entity_snapshot,
            now=_utcnow(),
            packet_id=stable_id("repair-preview", document.id, chapter.id, lead_block.id, *[block.id for block in continuation_blocks]),
            current_sentences=temporary_sentences,
            current_block_text=merged_source_text,
        )
        context_packet = ContextPacket.model_validate(packet.packet_json)
        if force_paragraph_current:
            context_packet = context_packet.model_copy(
                update={
                    "current_blocks": [
                        block.model_copy(update={"block_type": BlockType.PARAGRAPH.value})
                        for block in context_packet.current_blocks
                    ]
                }
            )
        worker_result = self.worker.translate(
            TranslationTask(
                context_packet=context_packet,
                current_sentences=temporary_sentences,
            )
        )
        result = worker_result if hasattr(worker_result, "usage") else worker_result
        output = result.output if hasattr(result, "output") else result
        usage = result.usage if hasattr(result, "usage") else None
        target_text = self.export_service._join_block_target_text(
            [segment.text_zh for segment in output.target_segments],
            block_type=BlockType.PARAGRAPH,
            render_mode="zh_primary_with_optional_source",
        )
        return target_text, usage

    def _build_temporary_sentences(
        self,
        *,
        document: Document,
        chapter: Chapter,
        lead_block: Block,
        merged_source_text: str,
    ) -> list[Sentence]:
        segmented = self.segmentation_service.segmenter.segment_text(merged_source_text)
        if not segmented:
            segmented = [merged_source_text]
        now = _utcnow()
        sentences: list[Sentence] = []
        for ordinal, text in enumerate(segmented, start=1):
            sentences.append(
                Sentence(
                    id=stable_id("repair-sentence", document.id, lead_block.id, ordinal, merged_source_text[:64]),
                    block_id=lead_block.id,
                    chapter_id=chapter.id,
                    document_id=document.id,
                    ordinal_in_block=ordinal,
                    source_text=text,
                    normalized_text=" ".join((text or "").split()),
                    source_lang=document.src_lang,
                    translatable=True,
                    nontranslatable_reason=None,
                    source_anchor=lead_block.source_anchor,
                    source_span_json={
                        "block_id": lead_block.id,
                        "block_type": BlockType.PARAGRAPH.value,
                        "ordinal_in_block": ordinal,
                        "repair_preview": True,
                    },
                    upstream_confidence=lead_block.parse_confidence,
                    sentence_status=SentenceStatus.PENDING,
                    active_version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
        return sentences

    def _latest_snapshot(
        self,
        memory_snapshots: list[MemorySnapshot],
        scope_id: str | None,
        snapshot_type: str,
    ) -> MemorySnapshot | None:
        matching = [
            snapshot
            for snapshot in memory_snapshots
            if snapshot.snapshot_type.value == snapshot_type and snapshot.scope_id == scope_id and snapshot.status.value == "active"
        ]
        matching.sort(key=lambda item: item.version, reverse=True)
        return matching[0] if matching else None
