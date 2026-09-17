"""Pipeline stage gate-keeper (Phase 2 of state-consistency refactor).

Collapses the scattered ``if`` gates in
``DocumentRunExecutor._process_XXX_stage`` into one declarative
dependency table plus a single entry point. The rule is:

    A stage may start only when *every one of its upstream stages has a
    derived StageStatus of SUCCEEDED* — as computed by
    :class:`StageStatusCalculator`, which reads physical rows in
    ``translation_packets`` and ``work_items``. The JSON cache in
    ``document_runs.status_detail_json.pipeline.stages.*`` is not
    consulted here on purpose.

Gate decisions are immutable for a given (run_id, stage, DB snapshot)
input; if an upstream later flips to FAILED, the downstream stage is not
rolled back, but it is refused re-entry so no new work gets seeded while
an upstream is broken.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from book_agent.domain.enums import ExportType
from book_agent.orchestrator.stage_status import (
    AGENT_STAGE_KINDS,
    PIPELINE_STAGES,
    StageEvidence,
    StageStatus,
    StageStatusCalculator,
)


STAGE_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "terminology": (),
    # Only when the run plans a terminology stage (plan_stages filtering);
    # targeted translate runs have none and start immediately.
    "translate": ("terminology",),
    "review": ("translate",),
    "bilingual_html": ("translate", "review"),
    "merged_html": ("translate", "review", "bilingual_html"),
}
# Other export types (standalone export runs) follow translation and review.
_DEFAULT_EXPORT_DEPENDENCIES: tuple[str, ...] = ("translate", "review")


def _dependencies_for(stage: str) -> tuple[str, ...]:
    if stage in STAGE_DEPENDENCIES:
        return STAGE_DEPENDENCIES[stage]
    if stage in {export_type.value for export_type in ExportType}:
        return _DEFAULT_EXPORT_DEPENDENCIES
    raise ValueError(f"unknown pipeline stage: {stage!r}")


@dataclass(frozen=True)
class GateDecision:
    """Explainable outcome of :meth:`StageGateKeeper.evaluate`.

    ``blocked_by`` lists the upstream stages that are NOT SUCCEEDED yet.
    When ``can_start`` is True, ``blocked_by`` is empty. The attached
    ``evidence`` for the candidate stage itself is useful for logging
    "blocked because translate is running at 82/430" style messages.
    """

    stage: str
    can_start: bool
    blocked_by: tuple[str, ...]
    upstream_statuses: dict[str, StageStatus]

    @property
    def reason(self) -> str:
        if self.can_start:
            return "all upstream stages succeeded"
        pieces = [
            f"{name}={self.upstream_statuses[name].value}" for name in self.blocked_by
        ]
        return f"blocked by: {', '.join(pieces)}"


class StageGateKeeper:
    """One-shot gate for a given stage candidate.

    Usage::

        gate = StageGateKeeper(session).evaluate(run_id, document_id, "review")
        if not gate.can_start:
            logger.info("review gate refused: %s", gate.reason)
            return False
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._calculator = StageStatusCalculator(session)

    def can_start(
        self,
        run_id: str,
        document_id: str,
        stage: str,
        *,
        plan_stages: tuple[str, ...] | None = None,
    ) -> bool:
        return self.evaluate(run_id, document_id, stage, plan_stages=plan_stages).can_start

    def evaluate(
        self,
        run_id: str,
        document_id: str,
        stage: str,
        *,
        plan_stages: tuple[str, ...] | None = None,
    ) -> GateDecision:
        """``plan_stages`` limits gating to upstream stages the run itself owns.

        A standalone review or export run does not wait for stages another
        run performed; the review and export services apply their own checks.
        """
        upstream_stages = _dependencies_for(stage)
        if plan_stages is not None:
            upstream_stages = tuple(upstream for upstream in upstream_stages if upstream in plan_stages)
        else:
            # Agent stages exist only when a run plans them; without a plan
            # they cannot be required upstream.
            upstream_stages = tuple(upstream for upstream in upstream_stages if upstream not in AGENT_STAGE_KINDS)
        upstream_statuses: dict[str, StageStatus] = {
            upstream: self._calculator.stage_status(run_id, document_id, upstream)
            for upstream in upstream_stages
        }
        blocked_by = tuple(
            name for name, status in upstream_statuses.items()
            if status != StageStatus.SUCCEEDED
        )
        return GateDecision(
            stage=stage,
            can_start=not blocked_by,
            blocked_by=blocked_by,
            upstream_statuses=upstream_statuses,
        )

    def candidate_evidence(
        self,
        run_id: str,
        document_id: str,
        stage: str,
    ) -> StageEvidence:
        return self._calculator.stage_evidence(run_id, document_id, stage)


__all__ = [
    "STAGE_DEPENDENCIES",
    "GateDecision",
    "StageGateKeeper",
    "PIPELINE_STAGES",
]
