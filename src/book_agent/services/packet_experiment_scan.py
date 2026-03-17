from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from book_agent.domain.models.translation import TranslationPacket
from book_agent.infra.repositories.translation import TranslationRepository
from book_agent.services.packet_experiment import PacketExperimentOptions, PacketExperimentService


def _memory_gain(payload: dict[str, Any]) -> int:
    context_sources = payload.get("context_sources") or {}
    compiled = int(context_sources.get("compiled_prev_translated_count") or 0)
    raw = int(context_sources.get("raw_prev_translated_count") or 0)
    return compiled - raw


def _concept_gain(payload: dict[str, Any]) -> int:
    context_sources = payload.get("context_sources") or {}
    compiled = int(context_sources.get("compiled_chapter_concept_count") or 0)
    raw = int(context_sources.get("raw_chapter_concept_count") or 0)
    return compiled - raw


def _brief_from_memory(payload: dict[str, Any]) -> bool:
    context_sources = payload.get("context_sources") or {}
    return context_sources.get("chapter_brief_source") == "memory"


def _current_sentence_count(payload: dict[str, Any]) -> int:
    context_packet = payload.get("context_packet") or {}
    current_blocks = context_packet.get("current_blocks") or []
    return sum(
        len(list(block.get("sentence_ids") or []))
        for block in current_blocks
        if isinstance(block, dict)
    )


def _memory_signal_score(payload: dict[str, Any]) -> int:
    return (
        _memory_gain(payload) * 100
        + _concept_gain(payload) * 20
        + (10 if _brief_from_memory(payload) else 0)
        + _current_sentence_count(payload)
    )


@dataclass(slots=True)
class PacketExperimentScanArtifacts:
    payload: dict[str, Any]


class PacketExperimentScanService:
    def __init__(
        self,
        repository: TranslationRepository,
        experiment_service: PacketExperimentService,
    ):
        self.repository = repository
        self.experiment_service = experiment_service

    def scan_chapter(
        self,
        chapter_id: str,
        *,
        options: PacketExperimentOptions | None = None,
    ) -> PacketExperimentScanArtifacts:
        experiment_options = options or PacketExperimentOptions(execute=False)
        packets = self.repository.session.scalars(
            select(TranslationPacket)
            .where(TranslationPacket.chapter_id == chapter_id)
            .order_by(TranslationPacket.created_at.asc())
        ).all()

        entries: list[dict[str, Any]] = []
        for packet in packets:
            experiment = self.experiment_service.run(packet.id, experiment_options)
            payload = experiment.payload
            entries.append(
                {
                    "packet_id": packet.id,
                    "packet_type": packet.packet_type.value,
                    "current_block_type": ((payload.get("context_packet") or {}).get("current_blocks") or [{}])[0].get(
                        "block_type"
                    ),
                    "current_sentence_count": _current_sentence_count(payload),
                    "memory_gain": _memory_gain(payload),
                    "concept_gain": _concept_gain(payload),
                    "brief_from_memory": _brief_from_memory(payload),
                    "memory_signal_score": _memory_signal_score(payload),
                    "chapter_brief_source": (payload.get("context_sources") or {}).get("chapter_brief_source"),
                    "context_sources": payload.get("context_sources"),
                }
            )

        entries.sort(
            key=lambda item: (
                -int(item["memory_signal_score"]),
                -int(item["memory_gain"]),
                -int(item["concept_gain"]),
                -int(item["current_sentence_count"]),
                str(item["packet_id"]),
            )
        )

        top_candidate = entries[0] if entries else None
        return PacketExperimentScanArtifacts(
            payload={
                "chapter_id": chapter_id,
                "options": {
                    "include_memory_blocks": experiment_options.include_memory_blocks,
                    "include_chapter_concepts": experiment_options.include_chapter_concepts,
                    "prefer_memory_chapter_brief": experiment_options.prefer_memory_chapter_brief,
                    "prompt_layout": experiment_options.prompt_layout,
                    "execute": experiment_options.execute,
                },
                "packet_count": len(entries),
                "top_candidate": top_candidate,
                "entries": entries,
            }
        )
