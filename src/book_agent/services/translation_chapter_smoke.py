from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from book_agent.services.packet_experiment import PacketExperimentOptions, PacketExperimentService
from book_agent.services.packet_experiment_scan import PacketExperimentScanArtifacts, PacketExperimentScanService
from book_agent.services.style_drift import STYLE_DRIFT_RULES


def _joined_translation_text(payload: dict[str, Any]) -> str:
    worker_output = payload.get("worker_output") or {}
    target_segments = worker_output.get("target_segments") or []
    return "\n".join(
        str(segment.get("text_zh") or "").strip()
        for segment in target_segments
        if isinstance(segment, dict) and str(segment.get("text_zh") or "").strip()
    )


def _joined_current_source_text(payload: dict[str, Any]) -> str:
    context_packet = payload.get("context_packet") or {}
    current_blocks = context_packet.get("current_blocks") or []
    return "\n".join(
        str(block.get("text") or "").strip()
        for block in current_blocks
        if isinstance(block, dict) and str(block.get("text") or "").strip()
    )


def _style_drift_hits(payload: dict[str, Any]) -> list[str]:
    source_text = _joined_current_source_text(payload)
    translation_text = _joined_translation_text(payload)
    if not source_text or not translation_text:
        return []
    return [
        rule.pattern_id
        for rule in STYLE_DRIFT_RULES
        if rule.source_pattern.search(source_text) and rule.target_pattern.search(translation_text)
    ]


@dataclass(frozen=True, slots=True)
class TranslationChapterSmokeOptions:
    selected_packet_limit: int = 3
    execute_selected: bool = True
    prefer_issue_driven_packets: bool = True
    include_memory_blocks: bool = True
    include_chapter_concepts: bool = True
    prefer_memory_chapter_brief: bool = True
    include_paragraph_intent: bool = True
    include_literalism_guardrails: bool = True
    prompt_layout: str = "paragraph-led"
    prompt_profile: str = "role-style-faithful-v6"
    explicit_packet_ids: tuple[str, ...] = ()


@dataclass(slots=True)
class TranslationChapterSmokeArtifacts:
    payload: dict[str, Any]


class TranslationChapterSmokeService:
    def __init__(
        self,
        *,
        experiment_service: PacketExperimentService,
        scan_service: PacketExperimentScanService,
    ):
        self.experiment_service = experiment_service
        self.scan_service = scan_service

    def run_chapter(
        self,
        chapter_id: str,
        *,
        options: TranslationChapterSmokeOptions | None = None,
    ) -> TranslationChapterSmokeArtifacts:
        smoke_options = options or TranslationChapterSmokeOptions()
        base_packet_options = PacketExperimentOptions(
            include_memory_blocks=smoke_options.include_memory_blocks,
            include_chapter_concepts=smoke_options.include_chapter_concepts,
            prefer_memory_chapter_brief=smoke_options.prefer_memory_chapter_brief,
            include_paragraph_intent=smoke_options.include_paragraph_intent,
            include_literalism_guardrails=smoke_options.include_literalism_guardrails,
            prompt_layout=smoke_options.prompt_layout,
            prompt_profile=smoke_options.prompt_profile,
            execute=False,
        )
        scan_artifacts = self.scan_service.scan_chapter(chapter_id, options=base_packet_options)
        selected_entries = self._select_entries(scan_artifacts, smoke_options)
        packet_results: list[dict[str, Any]] = []
        for entry in selected_entries:
            packet_id = str(entry["packet_id"])
            experiment = self.experiment_service.run(
                packet_id,
                PacketExperimentOptions(
                    include_memory_blocks=smoke_options.include_memory_blocks,
                    include_chapter_concepts=smoke_options.include_chapter_concepts,
                    prefer_memory_chapter_brief=smoke_options.prefer_memory_chapter_brief,
                    include_paragraph_intent=smoke_options.include_paragraph_intent,
                    include_literalism_guardrails=smoke_options.include_literalism_guardrails,
                    prompt_layout=smoke_options.prompt_layout,
                    prompt_profile=smoke_options.prompt_profile,
                    execute=smoke_options.execute_selected,
                ),
            )
            payload = experiment.payload
            packet_results.append(
                {
                    "packet_id": packet_id,
                    "memory_signal_score": entry.get("memory_signal_score"),
                    "current_sentence_count": entry.get("current_sentence_count"),
                    "unresolved_issue_count": entry.get("unresolved_issue_count"),
                    "unresolved_issue_types": entry.get("unresolved_issue_types"),
                    "style_drift_issue_count": entry.get("style_drift_issue_count"),
                    "non_style_issue_count": entry.get("non_style_issue_count"),
                    "has_non_style_issue": entry.get("has_non_style_issue"),
                    "mixed_issue_types": entry.get("mixed_issue_types"),
                    "issue_priority_tier": entry.get("issue_priority_tier"),
                    "issue_priority_reason": entry.get("issue_priority_reason"),
                    "context_sources": payload.get("context_sources"),
                    "worker_output_present": payload.get("worker_output") is not None,
                    "target_segment_count": len(((payload.get("worker_output") or {}).get("target_segments") or [])),
                    "alignment_suggestion_count": len(
                        ((payload.get("worker_output") or {}).get("alignment_suggestions") or [])
                    ),
                    "low_confidence_flag_count": len(
                        ((payload.get("worker_output") or {}).get("low_confidence_flags") or [])
                    ),
                    "translation_text": _joined_translation_text(payload),
                    "style_drift_hits": _style_drift_hits(payload),
                    "usage": payload.get("usage") or {},
                    "prompt_request": payload.get("prompt_request") or {},
                }
            )
        total_cost = sum(float((item.get("usage") or {}).get("cost_usd") or 0.0) for item in packet_results)
        total_style_hits = sum(len(item.get("style_drift_hits") or []) for item in packet_results)
        return TranslationChapterSmokeArtifacts(
            payload={
                "chapter_id": chapter_id,
                "options": {
                    "selected_packet_limit": smoke_options.selected_packet_limit,
                    "execute_selected": smoke_options.execute_selected,
                    "prefer_issue_driven_packets": smoke_options.prefer_issue_driven_packets,
                    "include_memory_blocks": smoke_options.include_memory_blocks,
                    "include_chapter_concepts": smoke_options.include_chapter_concepts,
                    "prefer_memory_chapter_brief": smoke_options.prefer_memory_chapter_brief,
                    "include_paragraph_intent": smoke_options.include_paragraph_intent,
                    "include_literalism_guardrails": smoke_options.include_literalism_guardrails,
                    "prompt_layout": smoke_options.prompt_layout,
                    "prompt_profile": smoke_options.prompt_profile,
                    "explicit_packet_ids": list(smoke_options.explicit_packet_ids),
                },
                "scan_summary": scan_artifacts.payload,
                "selected_packet_ids": [str(entry["packet_id"]) for entry in selected_entries],
                "packet_results": packet_results,
                "aggregate_summary": {
                    "selected_packet_count": len(packet_results),
                    "executed_packet_count": sum(1 for item in packet_results if item["worker_output_present"]),
                    "total_style_drift_hits": total_style_hits,
                    "total_cost_usd": total_cost,
                    "selected_mixed_issue_packet_count": sum(
                        1 for item in packet_results if bool(item.get("mixed_issue_types"))
                    ),
                    "selected_non_style_issue_packet_count": sum(
                        1 for item in packet_results if bool(item.get("has_non_style_issue"))
                    ),
                    "zero_style_drift_packet_count": sum(
                        1 for item in packet_results if not item.get("style_drift_hits")
                    ),
                },
            }
        )

    def _select_entries(
        self,
        scan_artifacts: PacketExperimentScanArtifacts,
        options: TranslationChapterSmokeOptions,
    ) -> list[dict[str, Any]]:
        entries = list(scan_artifacts.payload.get("entries") or [])
        if options.explicit_packet_ids:
            wanted = {str(packet_id) for packet_id in options.explicit_packet_ids}
            selected = [entry for entry in entries if str(entry.get("packet_id")) in wanted]
            selected.sort(key=lambda item: list(options.explicit_packet_ids).index(str(item["packet_id"])))
            return selected
        if not options.prefer_issue_driven_packets:
            return entries[: max(0, options.selected_packet_limit)]
        ranked = sorted(
            entries,
            key=lambda item: (
                int(item.get("issue_priority_tier", 2)),
                -int(item.get("unresolved_issue_count", 0)),
                -int(item.get("memory_signal_score", 0)),
                -int(item.get("memory_gain", 0)),
                -int(item.get("concept_gain", 0)),
                -int(item.get("current_sentence_count", 0)),
                str(item.get("packet_id") or ""),
            ),
        )
        return ranked[: max(0, options.selected_packet_limit)]
