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

from book_agent.orchestrator.stage_status import (
    PIPELINE_STAGES,
    StageEvidence,
    StageStatus,
    StageStatusCalculator,
)


STAGE_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "translate": (),
    "review": ("translate",),
    "bilingual_html": ("translate", "review"),
    "merged_html": ("translate", "review", "bilingual_html"),
}


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

    def can_start(self, run_id: str, document_id: str, stage: str) -> bool:
        return self.evaluate(run_id, document_id, stage).can_start

    def evaluate(
        self,
        run_id: str,
        document_id: str,
        stage: str,
    ) -> GateDecision:
        if stage not in STAGE_DEPENDENCIES:
            raise ValueError(f"unknown pipeline stage: {stage!r}")

        upstream_stages = STAGE_DEPENDENCIES[stage]
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
