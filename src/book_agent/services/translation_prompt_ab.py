from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from book_agent.services.packet_experiment import PacketExperimentArtifacts, PacketExperimentOptions, PacketExperimentService
from book_agent.services.packet_experiment_diff import compare_experiment_payloads
from book_agent.workers.translator import PromptLayout, PromptProfile


def _joined_translation_text(payload: dict[str, Any]) -> str:
    worker_output = payload.get("worker_output") or {}
    target_segments = worker_output.get("target_segments") or []
    return "\n".join(
        str(segment.get("text_zh") or "").strip()
        for segment in target_segments
        if isinstance(segment, dict) and str(segment.get("text_zh") or "").strip()
    )


@dataclass(frozen=True, slots=True)
class TranslationPromptCandidate:
    label: str
    prompt_profile: PromptProfile
    prompt_layout: PromptLayout = "paragraph-led"
    notes: str = ""


@dataclass(frozen=True, slots=True)
class TranslationPromptABOptions:
    execute: bool = True
    include_memory_blocks: bool = True
    include_chapter_concepts: bool = True
    prefer_memory_chapter_brief: bool = True
    prefer_previous_translations_over_source_context: bool = True
    include_paragraph_intent: bool = True
    include_literalism_guardrails: bool = True
    candidates: tuple[TranslationPromptCandidate, ...] = (
        TranslationPromptCandidate(
            label="baseline-v6",
            prompt_profile="role-style-faithful-v6",
            notes="当前基线，强调高保真与反抽象升级。",
        ),
        TranslationPromptCandidate(
            label="native-v1",
            prompt_profile="cn-native-faithful-v1",
            notes="强调像中文作者原生写作，同时保持严格忠实。",
        ),
        TranslationPromptCandidate(
            label="native-v2",
            prompt_profile="cn-native-faithful-v2",
            notes="更偏简洁、顺滑、去英文腔。",
        ),
        TranslationPromptCandidate(
            label="native-v3",
            prompt_profile="cn-native-faithful-v3",
            notes="更强调篇章连贯与中文行文节奏。",
        ),
    )


@dataclass(slots=True)
class TranslationPromptABArtifacts:
    payload: dict[str, Any]


class TranslationPromptABService:
    def __init__(self, *, experiment_service: PacketExperimentService):
        self.experiment_service = experiment_service

    def run_packet(
        self,
        packet_id: str,
        *,
        options: TranslationPromptABOptions | None = None,
    ) -> TranslationPromptABArtifacts:
        ab_options = options or TranslationPromptABOptions()
        candidate_payloads: list[dict[str, Any]] = []
        for candidate in ab_options.candidates:
            experiment = self.experiment_service.run(
                packet_id,
                PacketExperimentOptions(
                    include_memory_blocks=ab_options.include_memory_blocks,
                    include_chapter_concepts=ab_options.include_chapter_concepts,
                    prefer_memory_chapter_brief=ab_options.prefer_memory_chapter_brief,
                    prefer_previous_translations_over_source_context=(
                        ab_options.prefer_previous_translations_over_source_context
                    ),
                    include_paragraph_intent=ab_options.include_paragraph_intent,
                    include_literalism_guardrails=ab_options.include_literalism_guardrails,
                    prompt_layout=candidate.prompt_layout,
                    prompt_profile=candidate.prompt_profile,
                    execute=ab_options.execute,
                ),
            )
            candidate_payloads.append(
                {
                    "label": candidate.label,
                    "prompt_profile": candidate.prompt_profile,
                    "prompt_layout": candidate.prompt_layout,
                    "notes": candidate.notes,
                    "translation_text": _joined_translation_text(experiment.payload),
                    "experiment": experiment.payload,
                }
            )

        pairwise_diffs: list[dict[str, Any]] = []
        for index, baseline in enumerate(candidate_payloads):
            for candidate in candidate_payloads[index + 1 :]:
                diff = compare_experiment_payloads(
                    baseline["experiment"],
                    candidate["experiment"],
                    baseline_label=str(baseline["label"]),
                    candidate_label=str(candidate["label"]),
                )
                pairwise_diffs.append(diff.payload)

        review_summary = {
            "packet_id": packet_id,
            "candidate_count": len(candidate_payloads),
            "executed_candidate_count": sum(
                1 for item in candidate_payloads if (item.get("experiment") or {}).get("worker_output") is not None
            ),
            "labels": [str(item["label"]) for item in candidate_payloads],
            "profiles": [str(item["prompt_profile"]) for item in candidate_payloads],
        }
        return TranslationPromptABArtifacts(
            payload={
                "packet_id": packet_id,
                "options": {
                    "execute": ab_options.execute,
                    "include_memory_blocks": ab_options.include_memory_blocks,
                    "include_chapter_concepts": ab_options.include_chapter_concepts,
                    "prefer_memory_chapter_brief": ab_options.prefer_memory_chapter_brief,
                    "prefer_previous_translations_over_source_context": (
                        ab_options.prefer_previous_translations_over_source_context
                    ),
                    "include_paragraph_intent": ab_options.include_paragraph_intent,
                    "include_literalism_guardrails": ab_options.include_literalism_guardrails,
                },
                "review_summary": review_summary,
                "candidates": candidate_payloads,
                "pairwise_diffs": pairwise_diffs,
            }
        )
