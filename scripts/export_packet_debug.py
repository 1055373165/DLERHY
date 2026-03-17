from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.core.config import get_settings
from book_agent.domain.models import Block, Chapter, Document, Sentence
from book_agent.domain.models.translation import AlignmentEdge, TargetSegment, TranslationPacket, TranslationRun
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.chapter_memory import ChapterTranslationMemoryRepository
from book_agent.infra.repositories.translation import TranslationRepository
from book_agent.services.context_compile import ChapterContextCompileOptions, ChapterContextCompiler
from book_agent.workers.translator import TranslationTask, build_translation_prompt_request


def _coerce(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "value"):
        return value.value
    return value


def _sentence_payload(sentence: Sentence) -> dict[str, Any]:
    return {
        "id": sentence.id,
        "block_id": sentence.block_id,
        "ordinal_in_block": sentence.ordinal_in_block,
        "source_text": sentence.source_text,
        "normalized_text": sentence.normalized_text,
        "translatable": sentence.translatable,
        "sentence_status": _coerce(sentence.sentence_status),
    }


def _block_payload(block: Block, sentences: list[Sentence]) -> dict[str, Any]:
    return {
        "id": block.id,
        "ordinal": block.ordinal,
        "block_type": _coerce(block.block_type),
        "source_text": block.source_text,
        "normalized_text": block.normalized_text,
        "protected_policy": _coerce(block.protected_policy),
        "sentence_ids_in_block_order": [sentence.id for sentence in sentences],
        "sentences": [_sentence_payload(sentence) for sentence in sentences],
    }


def _translation_run_payload(run: TranslationRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        "id": run.id,
        "packet_id": run.packet_id,
        "model_name": run.model_name,
        "prompt_version": run.prompt_version,
        "attempt": run.attempt,
        "status": _coerce(run.status),
        "token_in": run.token_in,
        "token_out": run.token_out,
        "cost_usd": _coerce(run.cost_usd),
        "latency_ms": run.latency_ms,
        "error_code": run.error_code,
        "output_json": run.output_json,
        "created_at": str(run.created_at),
        "updated_at": str(run.updated_at),
    }


def _prompt_variant_payload(
    *,
    raw_context_packet,
    current_sentences,
    chapter_memory_snapshot,
    compiler: ChapterContextCompiler,
    label: str,
    compile_options: ChapterContextCompileOptions,
    prompt_layout: str,
) -> dict[str, Any]:
    compiled_packet = compiler.compile(
        raw_context_packet,
        chapter_memory_snapshot=chapter_memory_snapshot,
        options=compile_options,
    )
    prompt_request = build_translation_prompt_request(
        TranslationTask(
            context_packet=compiled_packet,
            current_sentences=current_sentences,
        ),
        model_name="debug-export",
        prompt_version=f"debug-export.{label}",
        prompt_layout=prompt_layout,
    )
    return {
        "label": label,
        "compile_options": {
            "include_memory_blocks": compile_options.include_memory_blocks,
            "include_chapter_concepts": compile_options.include_chapter_concepts,
            "prefer_memory_chapter_brief": compile_options.prefer_memory_chapter_brief,
        },
        "prompt_layout": prompt_layout,
        "chapter_brief": compiled_packet.chapter_brief,
        "previous_translation_count": len(compiled_packet.prev_translated_blocks),
        "chapter_concept_count": len(compiled_packet.chapter_concepts),
        "prompt_request": {
            "packet_id": prompt_request.packet_id,
            "model_name": prompt_request.model_name,
            "prompt_version": prompt_request.prompt_version,
            "system_prompt": prompt_request.system_prompt,
            "user_prompt": prompt_request.user_prompt,
            "sentence_alias_map": prompt_request.sentence_alias_map,
        },
    }


def export_packet_debug(
    *,
    database_url: str,
    document_id: str,
    packet_id: str,
    output_dir: Path,
) -> Path:
    engine = build_engine(database_url)
    session_factory = build_session_factory(engine=engine)

    with session_factory() as session:
        repository = TranslationRepository(session)
        bundle = repository.load_packet_bundle(packet_id)
        packet = bundle.packet
        raw_context_packet = bundle.context_packet
        compiler = ChapterContextCompiler()
        chapter_memory_snapshot = ChapterTranslationMemoryRepository(session).load_latest(
            document_id=raw_context_packet.document_id,
            chapter_id=raw_context_packet.chapter_id,
        )
        context_packet = compiler.compile(
            raw_context_packet,
            chapter_memory_snapshot=chapter_memory_snapshot,
        )
        prompt_request = build_translation_prompt_request(
            TranslationTask(
                context_packet=context_packet,
                current_sentences=bundle.current_sentences,
            ),
            model_name="debug-export",
            prompt_version="debug-export.v1",
        )
        prompt_variants = [
            _prompt_variant_payload(
                raw_context_packet=raw_context_packet,
                current_sentences=bundle.current_sentences,
                chapter_memory_snapshot=chapter_memory_snapshot,
                compiler=compiler,
                label="paragraph_led_current",
                compile_options=ChapterContextCompileOptions(),
                prompt_layout="paragraph-led",
            ),
            _prompt_variant_payload(
                raw_context_packet=raw_context_packet,
                current_sentences=bundle.current_sentences,
                chapter_memory_snapshot=chapter_memory_snapshot,
                compiler=compiler,
                label="paragraph_led_no_memory",
                compile_options=ChapterContextCompileOptions(
                    include_memory_blocks=False,
                    include_chapter_concepts=False,
                    prefer_memory_chapter_brief=False,
                ),
                prompt_layout="paragraph-led",
            ),
            _prompt_variant_payload(
                raw_context_packet=raw_context_packet,
                current_sentences=bundle.current_sentences,
                chapter_memory_snapshot=chapter_memory_snapshot,
                compiler=compiler,
                label="paragraph_led_no_concepts",
                compile_options=ChapterContextCompileOptions(
                    include_memory_blocks=True,
                    include_chapter_concepts=False,
                    prefer_memory_chapter_brief=True,
                ),
                prompt_layout="paragraph-led",
            ),
            _prompt_variant_payload(
                raw_context_packet=raw_context_packet,
                current_sentences=bundle.current_sentences,
                chapter_memory_snapshot=chapter_memory_snapshot,
                compiler=compiler,
                label="sentence_led_current",
                compile_options=ChapterContextCompileOptions(),
                prompt_layout="sentence-led",
            ),
        ]

        document = session.get(Document, document_id)
        if document is None:
            raise ValueError(f"Document not found: {document_id}")
        chapter = session.get(Chapter, packet.chapter_id)
        if chapter is None:
            raise ValueError(f"Chapter not found for packet: {packet_id}")

        current_block_ids = [block.block_id for block in context_packet.current_blocks]
        current_blocks = (
            session.scalars(select(Block).where(Block.id.in_(current_block_ids))).all()
            if current_block_ids
            else []
        )
        block_map = {block.id: block for block in current_blocks}

        ordered_block_sentences: list[Sentence] = []
        current_blocks_payload: list[dict[str, Any]] = []
        for packet_block in context_packet.current_blocks:
            block = block_map.get(packet_block.block_id)
            if block is None:
                continue
            ordered_sentences = session.scalars(
                select(Sentence)
                .where(Sentence.block_id == block.id)
                .order_by(Sentence.ordinal_in_block.asc())
            ).all()
            ordered_block_sentences.extend(ordered_sentences)
            current_blocks_payload.append(_block_payload(block, ordered_sentences))

        latest_run = session.scalars(
            select(TranslationRun)
            .where(TranslationRun.packet_id == packet_id)
            .order_by(TranslationRun.attempt.desc(), TranslationRun.created_at.desc())
        ).first()

        target_segments_payload: list[dict[str, Any]] = []
        alignment_payload: list[dict[str, Any]] = []
        if latest_run is not None:
            target_segments = session.scalars(
                select(TargetSegment)
                .where(TargetSegment.translation_run_id == latest_run.id)
                .order_by(TargetSegment.ordinal.asc())
            ).all()
            target_segment_map = {segment.id: segment for segment in target_segments}
            target_segments_payload = [
                {
                    "id": segment.id,
                    "ordinal": segment.ordinal,
                    "text_zh": segment.text_zh,
                    "segment_type": _coerce(segment.segment_type),
                    "confidence": _coerce(segment.confidence),
                    "final_status": _coerce(segment.final_status),
                }
                for segment in target_segments
            ]
            alignments = session.scalars(
                select(AlignmentEdge)
                .where(
                    AlignmentEdge.target_segment_id.in_(list(target_segment_map.keys()))
                )
            ).all() if target_segment_map else []
            sentence_lookup = {
                sentence.id: sentence
                for sentence in session.scalars(
                    select(Sentence).where(
                        Sentence.id.in_([edge.sentence_id for edge in alignments])
                    )
                ).all()
            } if alignments else {}
            alignment_payload = [
                {
                    "sentence_id": edge.sentence_id,
                    "sentence_source_text": sentence_lookup.get(edge.sentence_id).source_text
                    if sentence_lookup.get(edge.sentence_id) is not None
                    else None,
                    "target_segment_id": edge.target_segment_id,
                    "target_segment_text": target_segment_map.get(edge.target_segment_id).text_zh
                    if target_segment_map.get(edge.target_segment_id) is not None
                    else None,
                    "relation_type": _coerce(edge.relation_type),
                    "confidence": _coerce(edge.confidence),
                    "created_by": _coerce(edge.created_by),
                }
                for edge in alignments
            ]

        loaded_current_sentence_ids = [sentence.id for sentence in bundle.current_sentences]
        ordered_block_sentence_ids = [sentence.id for sentence in ordered_block_sentences]

        payload = {
            "document": {
                "id": document.id,
                "title": document.title,
                "author": document.author,
                "source_type": _coerce(document.source_type),
                "source_path": document.source_path,
                "status": _coerce(document.status),
            },
            "chapter": {
                "id": chapter.id,
                "ordinal": chapter.ordinal,
                "title_src": chapter.title_src,
                "title_tgt": chapter.title_tgt,
                "status": _coerce(chapter.status),
                "risk_level": _coerce(chapter.risk_level),
            },
            "packet": {
                "id": packet.id,
                "chapter_id": packet.chapter_id,
                "packet_type": _coerce(packet.packet_type),
                "status": _coerce(packet.status),
                "book_profile_version": packet.book_profile_version,
                "chapter_brief_version": packet.chapter_brief_version,
                "termbase_version": packet.termbase_version,
                "entity_snapshot_version": packet.entity_snapshot_version,
                "style_snapshot_version": packet.style_snapshot_version,
                "risk_score": _coerce(packet.risk_score),
            },
            "context_packet": context_packet.model_dump(),
            "chapter_memory_snapshot": chapter_memory_snapshot.content_json if chapter_memory_snapshot is not None else None,
            "prompt_request": {
                "packet_id": prompt_request.packet_id,
                "model_name": prompt_request.model_name,
                "prompt_version": prompt_request.prompt_version,
                "system_prompt": prompt_request.system_prompt,
                "user_prompt": prompt_request.user_prompt,
                "sentence_alias_map": prompt_request.sentence_alias_map,
            },
            "prompt_variants": prompt_variants,
            "current_blocks_ordered": current_blocks_payload,
            "current_sentences_loaded": [_sentence_payload(sentence) for sentence in bundle.current_sentences],
            "sentence_order_diagnostics": {
                "matches_block_order": loaded_current_sentence_ids == ordered_block_sentence_ids,
                "loaded_current_sentence_ids": loaded_current_sentence_ids,
                "ordered_block_sentence_ids": ordered_block_sentence_ids,
            },
            "all_packet_sentences_loaded": [_sentence_payload(sentence) for sentence in bundle.all_packet_sentences],
            "latest_translation_run": _translation_run_payload(latest_run),
            "persisted_target_segments": target_segments_payload,
            "persisted_alignment_edges": alignment_payload,
        }

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{packet_id}.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_coerce), encoding="utf-8")
    return output_path


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Export packet-level debug artifact from live DB.")
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--packet-id", action="append", required=True, dest="packet_ids")
    parser.add_argument(
        "--database-url",
        default=settings.database_url,
        help="SQLAlchemy database URL. Defaults to current settings.database_url.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory where packet debug JSON files will be written.",
    )
    args = parser.parse_args()

    for packet_id in args.packet_ids:
        output_path = export_packet_debug(
            database_url=args.database_url,
            document_id=args.document_id,
            packet_id=packet_id,
            output_dir=args.output_dir,
        )
        print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
