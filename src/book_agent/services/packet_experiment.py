from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from book_agent.core.config import Settings
from book_agent.infra.repositories.chapter_memory import ChapterTranslationMemoryRepository
from book_agent.infra.repositories.ops import OpsRepository
from book_agent.infra.repositories.translation import TranslationRepository
from book_agent.orchestrator.rerun import (
    concept_overrides_for_issue,
    merge_concept_overrides,
    merge_style_hints,
    style_hints_for_issue,
)
from book_agent.services.context_compile import ChapterContextCompileOptions, ChapterContextCompiler
from book_agent.workers.contracts import ConceptCandidate, TranslationUsage, TranslationWorkerOutput, TranslationWorkerResult
from book_agent.workers.translator import (
    PromptLayout,
    PromptProfile,
    TranslationTask,
    TranslationWorker,
    TranslationWorkerMetadata,
    TranslationMaterial,
    build_translation_prompt_request,
)


def _coerce(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Path):
        return str(value)
    return value


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _prompt_stats(system_prompt: str, user_prompt: str) -> dict[str, Any]:
    system_lines = system_prompt.splitlines()
    user_lines = user_prompt.splitlines()
    return {
        "system_prompt_chars": len(system_prompt),
        "user_prompt_chars": len(user_prompt),
        "total_prompt_chars": len(system_prompt) + len(user_prompt),
        "system_prompt_lines": len(system_lines),
        "user_prompt_lines": len(user_lines),
        "user_prompt_sections": [line.rstrip(":") for line in user_lines if line.endswith(":")],
    }


def _memory_translation_entries(snapshot_content: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(snapshot_content, dict):
        return []
    entries = snapshot_content.get("recent_accepted_translations", [])
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def _memory_concept_entries(snapshot_content: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(snapshot_content, dict):
        return []
    entries = snapshot_content.get("active_concepts", [])
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


@dataclass(frozen=True, slots=True)
class PacketExperimentOptions:
    include_memory_blocks: bool = True
    include_chapter_concepts: bool = True
    prefer_memory_chapter_brief: bool = True
    prefer_previous_translations_over_source_context: bool = True
    include_paragraph_intent: bool = True
    include_literalism_guardrails: bool = True
    prompt_layout: PromptLayout = "paragraph-led"
    prompt_profile: PromptProfile = "role-style-faithful-v6"
    material_profile_override: TranslationMaterial | None = None
    execute: bool = False
    concept_overrides: tuple[ConceptCandidate, ...] = ()
    rerun_hints: tuple[str, ...] = ()
    review_issue_ids: tuple[str, ...] = ()


@dataclass(slots=True)
class PacketExperimentArtifacts:
    payload: dict[str, Any]


class PacketExperimentService:
    def __init__(
        self,
        repository: TranslationRepository,
        *,
        settings: Settings,
        worker: TranslationWorker | None = None,
        chapter_memory_repository: ChapterTranslationMemoryRepository | None = None,
        context_compiler: ChapterContextCompiler | None = None,
        ops_repository: OpsRepository | None = None,
    ):
        self.repository = repository
        self.settings = settings
        self.worker = worker
        self.chapter_memory_repository = chapter_memory_repository or ChapterTranslationMemoryRepository(
            repository.session
        )
        self.context_compiler = context_compiler or ChapterContextCompiler()
        self.ops_repository = ops_repository or OpsRepository(repository.session)

    def run(self, packet_id: str, options: PacketExperimentOptions) -> PacketExperimentArtifacts:
        bundle = self.repository.load_packet_bundle(packet_id)
        chapter_memory_snapshot = self.chapter_memory_repository.load_latest(
            document_id=bundle.context_packet.document_id,
            chapter_id=bundle.context_packet.chapter_id,
        )
        resolved_concept_overrides, resolved_rerun_hints, review_issue_context = self._resolve_rerun_inputs(options)
        compiled_context = self.context_compiler.compile(
            bundle.context_packet,
            chapter_memory_snapshot=chapter_memory_snapshot,
            options=ChapterContextCompileOptions(
                include_memory_blocks=options.include_memory_blocks,
                include_chapter_concepts=options.include_chapter_concepts,
                prefer_memory_chapter_brief=options.prefer_memory_chapter_brief,
                prefer_previous_translations_over_source_context=(
                    options.prefer_previous_translations_over_source_context
                ),
                include_paragraph_intent=options.include_paragraph_intent,
                include_literalism_guardrails=options.include_literalism_guardrails,
                concept_overrides=resolved_concept_overrides,
            ),
        )
        if resolved_rerun_hints:
            merged_open_questions = list(compiled_context.open_questions)
            for hint in resolved_rerun_hints:
                if hint and hint not in merged_open_questions:
                    merged_open_questions.append(hint)
            compiled_context = compiled_context.model_copy(
                update={"open_questions": merged_open_questions}
            )
        if options.material_profile_override is not None:
            compiled_context = compiled_context.model_copy(
                update={
                    "style_constraints": {
                        **compiled_context.style_constraints,
                        "translation_material": options.material_profile_override,
                    }
                }
            )

        metadata = self.worker.metadata() if self.worker is not None else self._planned_metadata()
        prompt_request = build_translation_prompt_request(
            TranslationTask(
                context_packet=compiled_context,
                current_sentences=bundle.current_sentences,
            ),
            model_name=metadata.model_name,
            prompt_version=metadata.prompt_version,
            prompt_layout=options.prompt_layout,
            prompt_profile=options.prompt_profile,
        )

        worker_output: TranslationWorkerOutput | None = None
        usage = TranslationUsage()
        if options.execute:
            if self.worker is None:
                raise ValueError("Packet experiment execution requires a translation worker.")
            result = self._coerce_worker_result(
                self.worker.translate(
                    TranslationTask(
                        context_packet=compiled_context,
                        current_sentences=bundle.current_sentences,
                    )
                )
            )
            worker_output = result.output
            usage = result.usage

        payload = {
            "generated_at": _utcnow_iso(),
            "packet_id": packet_id,
            "document_id": bundle.context_packet.document_id,
            "chapter_id": bundle.context_packet.chapter_id,
            "database_url": self.settings.database_url,
            "options": {
                "include_memory_blocks": options.include_memory_blocks,
                "include_chapter_concepts": options.include_chapter_concepts,
                "prefer_memory_chapter_brief": options.prefer_memory_chapter_brief,
                "prefer_previous_translations_over_source_context": (
                    options.prefer_previous_translations_over_source_context
                ),
                "include_paragraph_intent": options.include_paragraph_intent,
                "include_literalism_guardrails": options.include_literalism_guardrails,
                "prompt_layout": options.prompt_layout,
                "prompt_profile": options.prompt_profile,
                "material_profile_override": options.material_profile_override,
                "execute": options.execute,
                "concept_overrides": [concept.model_dump() for concept in options.concept_overrides],
                "rerun_hints": list(options.rerun_hints),
                "review_issue_ids": list(options.review_issue_ids),
            },
            "rerun_context": {
                "review_issue_ids": list(options.review_issue_ids),
                "review_issue_count": len(review_issue_context),
                "review_issues": review_issue_context,
                "resolved_concept_overrides": [concept.model_dump() for concept in resolved_concept_overrides],
                "resolved_rerun_hints": list(resolved_rerun_hints),
            },
            "context_compile_version": self.context_compiler.compile_version,
            "chapter_memory_snapshot_id": (
                chapter_memory_snapshot.id if chapter_memory_snapshot is not None else None
            ),
            "chapter_memory_snapshot_version": (
                chapter_memory_snapshot.version if chapter_memory_snapshot is not None else None
            ),
            "context_sources": {
                "raw_prev_translated_count": len(bundle.context_packet.prev_translated_blocks),
                "compiled_prev_translated_count": len(compiled_context.prev_translated_blocks),
                "raw_prev_block_count": len(bundle.context_packet.prev_blocks),
                "compiled_prev_block_count": len(compiled_context.prev_blocks),
                "raw_next_block_count": len(bundle.context_packet.next_blocks),
                "compiled_next_block_count": len(compiled_context.next_blocks),
                "chapter_memory_translation_count": len(
                    _memory_translation_entries(
                        chapter_memory_snapshot.content_json if chapter_memory_snapshot is not None else None
                    )
                ),
                "raw_relevant_term_count": len(bundle.context_packet.relevant_terms),
                "compiled_relevant_term_count": len(compiled_context.relevant_terms),
                "raw_chapter_concept_count": len(bundle.context_packet.chapter_concepts),
                "compiled_chapter_concept_count": len(compiled_context.chapter_concepts),
                "chapter_memory_concept_count": len(
                    _memory_concept_entries(
                        chapter_memory_snapshot.content_json if chapter_memory_snapshot is not None else None
                    )
                ),
                "raw_chapter_brief_present": bool(bundle.context_packet.chapter_brief),
                "compiled_chapter_brief_present": bool(compiled_context.chapter_brief),
                "prompt_chapter_brief_present": (
                    bool(compiled_context.chapter_brief)
                    and not bool(compiled_context.style_constraints.get("suppress_chapter_brief_in_prompt"))
                ),
                "compiled_section_brief_present": bool(compiled_context.section_brief),
                "compiled_discourse_bridge_present": compiled_context.discourse_bridge is not None,
                "chapter_brief_source": (
                    "memory"
                    if compiled_context.chapter_brief != bundle.context_packet.chapter_brief
                    else "packet"
                ),
                "translation_material": compiled_context.style_constraints.get("translation_material"),
            },
            "worker_metadata": {
                "worker_name": metadata.worker_name,
                "model_name": metadata.model_name,
                "prompt_version": metadata.prompt_version,
                "runtime_config": metadata.runtime_config,
            },
            "prompt_stats": _prompt_stats(
                prompt_request.system_prompt,
                prompt_request.user_prompt,
            ),
            "context_packet": compiled_context.model_dump(),
            "prompt_request": {
                "packet_id": prompt_request.packet_id,
                "model_name": prompt_request.model_name,
                "prompt_version": prompt_request.prompt_version,
                "system_prompt": prompt_request.system_prompt,
                "user_prompt": prompt_request.user_prompt,
                "sentence_alias_map": prompt_request.sentence_alias_map,
            },
            "worker_output": worker_output.model_dump(mode="json") if worker_output is not None else None,
            "usage": usage.model_dump(mode="json"),
        }
        return PacketExperimentArtifacts(payload=payload)

    def _resolve_rerun_inputs(
        self,
        options: PacketExperimentOptions,
    ) -> tuple[tuple[ConceptCandidate, ...], tuple[str, ...], list[dict[str, Any]]]:
        review_issue_context: list[dict[str, Any]] = []
        review_issue_concept_groups: list[tuple[ConceptCandidate, ...]] = []
        review_issue_hint_groups: list[tuple[str, ...]] = []
        for issue_id in options.review_issue_ids:
            issue = self.ops_repository.get_issue(issue_id)
            issue_concepts = concept_overrides_for_issue(issue)
            issue_hints = style_hints_for_issue(issue)
            review_issue_concept_groups.append(issue_concepts)
            review_issue_hint_groups.append(issue_hints)
            evidence = issue.evidence_json or {}
            review_issue_context.append(
                {
                    "issue_id": issue.id,
                    "issue_type": issue.issue_type,
                    "packet_id": issue.packet_id,
                    "sentence_id": issue.sentence_id,
                    "style_rule": str(evidence.get("style_rule") or "").strip() or None,
                    "preferred_hint": str(evidence.get("preferred_hint") or "").strip() or None,
                    "prompt_guidance": str(evidence.get("prompt_guidance") or "").strip() or None,
                    "resolved_concept_overrides": [concept.model_dump() for concept in issue_concepts],
                    "resolved_style_hints": list(issue_hints),
                }
            )
        resolved_concepts = merge_concept_overrides([*review_issue_concept_groups, options.concept_overrides])
        resolved_hints = merge_style_hints([*review_issue_hint_groups, options.rerun_hints])
        return resolved_concepts, resolved_hints, review_issue_context

    def _planned_metadata(self) -> TranslationWorkerMetadata:
        runtime_config: dict[str, Any] = {"provider": self.settings.translation_backend}
        if self.settings.translation_backend == "openai_compatible":
            runtime_config.update(
                {
                    "base_url": self.settings.translation_openai_base_url,
                    "timeout_seconds": self.settings.translation_timeout_seconds,
                    "max_retries": self.settings.translation_max_retries,
                    "retry_backoff_seconds": self.settings.translation_retry_backoff_seconds,
                }
            )
        return TranslationWorkerMetadata(
            worker_name=f"planned::{self.settings.translation_backend}",
            model_name=self.settings.translation_model,
            prompt_version=self.settings.translation_prompt_version,
            runtime_config=runtime_config,
        )

    def _coerce_worker_result(
        self,
        payload: TranslationWorkerResult | TranslationWorkerOutput,
    ) -> TranslationWorkerResult:
        if isinstance(payload, TranslationWorkerResult):
            return payload
        if isinstance(payload, TranslationWorkerOutput):
            return TranslationWorkerResult(output=payload, usage=TranslationUsage())
        raise TypeError(f"Unsupported translation worker payload: {type(payload)!r}")
