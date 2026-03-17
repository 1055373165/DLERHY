from __future__ import annotations

from dataclasses import dataclass
from difflib import unified_diff
from typing import Any


def _as_lines(value: str | None) -> list[str]:
    if not value:
        return []
    return value.splitlines()


def _section_titles(prompt_text: str | None) -> list[str]:
    lines = _as_lines(prompt_text)
    return [line.rstrip(":") for line in lines if line.endswith(":")]


def _target_texts(worker_output: dict[str, Any] | None) -> list[str]:
    if not isinstance(worker_output, dict):
        return []
    target_segments = worker_output.get("target_segments")
    if not isinstance(target_segments, list):
        return []
    return [
        str(segment.get("text_zh") or "")
        for segment in target_segments
        if isinstance(segment, dict)
    ]


@dataclass(frozen=True, slots=True)
class PacketExperimentDiffArtifacts:
    payload: dict[str, Any]


def compare_experiment_payloads(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    baseline_label: str = "baseline",
    candidate_label: str = "candidate",
) -> PacketExperimentDiffArtifacts:
    baseline_prompt = (((baseline.get("prompt_request") or {}) if isinstance(baseline, dict) else {}) or {}).get(
        "user_prompt"
    )
    candidate_prompt = (((candidate.get("prompt_request") or {}) if isinstance(candidate, dict) else {}) or {}).get(
        "user_prompt"
    )
    baseline_system_prompt = (
        (((baseline.get("prompt_request") or {}) if isinstance(baseline, dict) else {}) or {}).get("system_prompt")
    )
    candidate_system_prompt = (
        (((candidate.get("prompt_request") or {}) if isinstance(candidate, dict) else {}) or {}).get("system_prompt")
    )

    baseline_context = (baseline.get("context_packet") or {}) if isinstance(baseline, dict) else {}
    candidate_context = (candidate.get("context_packet") or {}) if isinstance(candidate, dict) else {}
    baseline_output = baseline.get("worker_output") if isinstance(baseline, dict) else None
    candidate_output = candidate.get("worker_output") if isinstance(candidate, dict) else None
    baseline_sources = (baseline.get("context_sources") or {}) if isinstance(baseline, dict) else {}
    candidate_sources = (candidate.get("context_sources") or {}) if isinstance(candidate, dict) else {}

    baseline_prev_count = len(list(baseline_context.get("prev_translated_blocks") or []))
    candidate_prev_count = len(list(candidate_context.get("prev_translated_blocks") or []))
    baseline_concept_count = len(list(baseline_context.get("chapter_concepts") or []))
    candidate_concept_count = len(list(candidate_context.get("chapter_concepts") or []))
    baseline_targets = _target_texts(baseline_output)
    candidate_targets = _target_texts(candidate_output)

    payload = {
        "baseline_label": baseline_label,
        "candidate_label": candidate_label,
        "packet_id": baseline.get("packet_id") or candidate.get("packet_id"),
        "baseline_options": baseline.get("options"),
        "candidate_options": candidate.get("options"),
        "summary": {
            "same_packet_id": (baseline.get("packet_id") == candidate.get("packet_id")),
            "context_compile_version_changed": baseline.get("context_compile_version")
            != candidate.get("context_compile_version"),
            "prompt_layout_changed": (
                ((baseline.get("options") or {}).get("prompt_layout"))
                != ((candidate.get("options") or {}).get("prompt_layout"))
            ),
            "chapter_brief_changed": baseline_context.get("chapter_brief") != candidate_context.get("chapter_brief"),
            "previous_translation_count_changed": baseline_prev_count != candidate_prev_count,
            "chapter_concept_count_changed": baseline_concept_count != candidate_concept_count,
            "chapter_brief_source_changed": baseline_sources.get("chapter_brief_source")
            != candidate_sources.get("chapter_brief_source"),
            "user_prompt_changed": baseline_prompt != candidate_prompt,
            "system_prompt_changed": baseline_system_prompt != candidate_system_prompt,
            "worker_output_changed": baseline_targets != candidate_targets,
            "worker_output_presence_changed": bool(baseline_output) != bool(candidate_output),
        },
        "context_delta": {
            "chapter_brief": {
                baseline_label: baseline_context.get("chapter_brief"),
                candidate_label: candidate_context.get("chapter_brief"),
            },
            "previous_translation_count": {
                baseline_label: baseline_prev_count,
                candidate_label: candidate_prev_count,
                "delta": candidate_prev_count - baseline_prev_count,
            },
            "chapter_concept_count": {
                baseline_label: baseline_concept_count,
                candidate_label: candidate_concept_count,
                "delta": candidate_concept_count - baseline_concept_count,
            },
            "context_sources": {
                "raw_prev_translated_count": {
                    baseline_label: baseline_sources.get("raw_prev_translated_count"),
                    candidate_label: candidate_sources.get("raw_prev_translated_count"),
                },
                "compiled_prev_translated_count": {
                    baseline_label: baseline_sources.get("compiled_prev_translated_count"),
                    candidate_label: candidate_sources.get("compiled_prev_translated_count"),
                },
                "chapter_memory_translation_count": {
                    baseline_label: baseline_sources.get("chapter_memory_translation_count"),
                    candidate_label: candidate_sources.get("chapter_memory_translation_count"),
                },
                "raw_chapter_concept_count": {
                    baseline_label: baseline_sources.get("raw_chapter_concept_count"),
                    candidate_label: candidate_sources.get("raw_chapter_concept_count"),
                },
                "compiled_chapter_concept_count": {
                    baseline_label: baseline_sources.get("compiled_chapter_concept_count"),
                    candidate_label: candidate_sources.get("compiled_chapter_concept_count"),
                },
                "chapter_memory_concept_count": {
                    baseline_label: baseline_sources.get("chapter_memory_concept_count"),
                    candidate_label: candidate_sources.get("chapter_memory_concept_count"),
                },
                "chapter_brief_source": {
                    baseline_label: baseline_sources.get("chapter_brief_source"),
                    candidate_label: candidate_sources.get("chapter_brief_source"),
                },
            },
        },
        "prompt_delta": {
            "baseline_sections": _section_titles(baseline_prompt),
            "candidate_sections": _section_titles(candidate_prompt),
            "user_prompt_unified_diff": list(
                unified_diff(
                    _as_lines(baseline_prompt),
                    _as_lines(candidate_prompt),
                    fromfile=baseline_label,
                    tofile=candidate_label,
                    lineterm="",
                )
            ),
            "system_prompt_unified_diff": list(
                unified_diff(
                    _as_lines(baseline_system_prompt),
                    _as_lines(candidate_system_prompt),
                    fromfile=f"{baseline_label}:system",
                    tofile=f"{candidate_label}:system",
                    lineterm="",
                )
            ),
        },
        "output_delta": {
            "baseline_target_texts": baseline_targets,
            "candidate_target_texts": candidate_targets,
            "target_text_unified_diff": list(
                unified_diff(
                    baseline_targets,
                    candidate_targets,
                    fromfile=f"{baseline_label}:targets",
                    tofile=f"{candidate_label}:targets",
                    lineterm="",
                )
            ),
        },
    }
    return PacketExperimentDiffArtifacts(payload=payload)
