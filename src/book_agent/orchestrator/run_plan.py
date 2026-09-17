"""What a document run executes, derived from its type and request.

Before run plans every run was a TRANSLATE_FULL pipeline. The document API's
translate / review / export endpoints now enqueue narrower runs, so the
executor, stage gates and terminal reconcile ask the plan which stages a run
owns instead of assuming the full pipeline.

Request parameters live in ``document_runs.status_detail_json["run_request"]``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from book_agent.domain.enums import DocumentRunType, ExportType

RUN_REQUEST_KEY = "run_request"

TERMINOLOGY_STAGE = "terminology"
TERMINOLOGY_MODES: tuple[str, ...] = ("sampled", "thorough", "skip")
DEFAULT_TERMINOLOGY_MODE = "sampled"
MODEL_REVIEW_STAGE = "model_review"
MODEL_REVIEW_MODES: tuple[str, ...] = ("sampled", "full", "skip")
DEFAULT_MODEL_REVIEW_MODE = "sampled"
# Agent stages: pipeline stage key -> agent kind executed as an AGENT work item.
AGENT_STAGES: dict[str, str] = {TERMINOLOGY_STAGE: "terminology", MODEL_REVIEW_STAGE: "reviewer"}
FULL_PIPELINE_STAGES: tuple[str, ...] = (
    TERMINOLOGY_STAGE,
    "translate",
    MODEL_REVIEW_STAGE,
    "review",
    "bilingual_html",
    "merged_html",
)
EXPORT_STAGE_KEYS: frozenset[str] = frozenset(export_type.value for export_type in ExportType)


@dataclass(frozen=True, slots=True)
class RunPlan:
    run_type: DocumentRunType
    stages: tuple[str, ...]
    # Translate only these packets; None means every pending packet.
    packet_ids: frozenset[str] | None = None
    # TRANSLATE_FULL review auto-executes followups and repairs blockers, and
    # fails while blockers remain; a standalone review only records issues.
    review_repairs_blockers: bool = True
    auto_followup_on_export_gate: bool = True
    max_auto_followup_attempts: int | None = None
    # How the terminology agent samples the book: sampled | thorough | skip.
    terminology_mode: str = DEFAULT_TERMINOLOGY_MODE
    # How much of the translation the Reviewer Agent reads: sampled | full | skip.
    model_review_mode: str = DEFAULT_MODEL_REVIEW_MODE

    def includes(self, stage: str) -> bool:
        return stage in self.stages

    @property
    def export_stages(self) -> tuple[str, ...]:
        return tuple(stage for stage in self.stages if stage in EXPORT_STAGE_KEYS)

    @property
    def agent_stages(self) -> tuple[str, ...]:
        return tuple(stage for stage in self.stages if stage in AGENT_STAGES)

    @property
    def required_stages(self) -> frozenset[str]:
        return frozenset(self.stages)


def run_request(status_detail_json: Mapping[str, Any] | None) -> dict[str, Any]:
    request = (status_detail_json or {}).get(RUN_REQUEST_KEY)
    return dict(request) if isinstance(request, Mapping) else {}


def _mode(value: Any, allowed: tuple[str, ...], default: str) -> str:
    mode = str(value or default).strip().lower()
    return mode if mode in allowed else default


def plan_for_run(run_type: DocumentRunType | str, status_detail_json: Mapping[str, Any] | None) -> RunPlan:
    run_type = DocumentRunType(run_type)
    request = run_request(status_detail_json)
    if run_type == DocumentRunType.TRANSLATE_FULL:
        mode = _mode(request.get("terminology"), TERMINOLOGY_MODES, DEFAULT_TERMINOLOGY_MODE)
        review_mode = _mode(request.get("model_review"), MODEL_REVIEW_MODES, DEFAULT_MODEL_REVIEW_MODE)
        skipped = {TERMINOLOGY_STAGE} if mode == "skip" else set()
        if review_mode == "skip":
            skipped.add(MODEL_REVIEW_STAGE)
        stages = tuple(stage for stage in FULL_PIPELINE_STAGES if stage not in skipped)
        return RunPlan(run_type=run_type, stages=stages, terminology_mode=mode, model_review_mode=review_mode)
    if run_type == DocumentRunType.TRANSLATE_TARGETED:
        packet_ids = [str(packet_id) for packet_id in request.get("packet_ids") or [] if str(packet_id)]
        return RunPlan(
            run_type=run_type,
            stages=("translate",),
            packet_ids=frozenset(packet_ids) if packet_ids else None,
        )
    if run_type == DocumentRunType.REVIEW_FULL:
        return RunPlan(run_type=run_type, stages=("review",), review_repairs_blockers=False)
    if run_type == DocumentRunType.EXPORT_FULL:
        export_type = ExportType(str(request.get("export_type") or ExportType.MERGED_HTML.value))
        max_attempts = request.get("max_auto_followup_attempts")
        return RunPlan(
            run_type=run_type,
            stages=(export_type.value,),
            auto_followup_on_export_gate=bool(request.get("auto_execute_followup_on_gate", False)),
            max_auto_followup_attempts=int(max_attempts) if max_attempts is not None else None,
        )
    raise ValueError(f"Run type {run_type.value} has no execution plan.")


def translate_packet_scope(
    run_type: DocumentRunType | str, status_detail_json: Mapping[str, Any] | None
) -> frozenset[str] | None:
    """The packet set a run's translate stage owns, or None for every packet.

    Every reader of the derived translate status (run summary projection,
    terminal reconcile, drift reconciler, snapshot finalisation) must use the
    same scope, otherwise a targeted run looks unfinished as long as the
    document has other packets left to translate.
    """
    try:
        return plan_for_run(run_type, status_detail_json).packet_ids
    except ValueError:
        return None


EXECUTABLE_RUN_TYPES: frozenset[DocumentRunType] = frozenset(
    {
        DocumentRunType.TRANSLATE_FULL,
        DocumentRunType.TRANSLATE_TARGETED,
        DocumentRunType.REVIEW_FULL,
        DocumentRunType.EXPORT_FULL,
    }
)
