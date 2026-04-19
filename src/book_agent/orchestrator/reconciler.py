"""Read-only invariant checks over pipeline stage state (Phase 2).

The Reconciler exists because the system has two state surfaces that
*must* agree: (1) physical rows in ``translation_packets`` + ``work_items``
and (2) the JSON cache at ``document_runs.status_detail_json.pipeline``.
Every place in the code that mutates state is required to do so
transactionally AND to append a ``stage_transitions`` audit row. When
either obligation is skipped — by a buggy write path, a crash between
two statements, or a manual SQL edit — the two surfaces drift, and the
UI starts lying to the user. That was the production defect that kicked
off this refactor (review=SUCCEEDED while translate=82/430 RUNNING).

:class:`Reconciler` scans a run and returns a list of
:class:`DriftFinding` objects. It is deliberately *read-only*: it never
mutates rows, because the correct remediation for each drift class
varies (retry, hard-fail, page a human). The orchestrator decides what
to do with the findings; the Reconciler's job is to make drift visible
rather than let it rot. An append-only ``stage_transitions`` row with
``triggered_by='reconciler'`` and ``to_status='drift_detected'`` is
written for every finding so postmortems can bisect when drift started.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from book_agent.domain.models.ops import DocumentRun
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.orchestrator.stage_status import (
    PIPELINE_STAGES,
    StageStatus,
    StageStatusCalculator,
    StageTransitionLogger,
)


DRIFT_KIND_CACHE_OVER_REPORTS_SUCCESS = "cache_over_reports_success"
DRIFT_KIND_CACHE_OVER_REPORTS_RUNNING = "cache_over_reports_running"


@dataclass(frozen=True)
class DriftFinding:
    """One inconsistency between JSON cache and derived physical state.

    ``kind`` is a stable string so monitoring/alerting can group by it.
    ``cache_status`` is the value the JSON cache claimed; ``derived_status``
    is what the calculator returned from physical rows. The latter is
    authoritative.
    """

    run_id: str
    stage: str
    kind: str
    cache_status: str
    derived_status: StageStatus
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {
            "run_id": self.run_id,
            "stage": self.stage,
            "kind": self.kind,
            "cache_status": self.cache_status,
            "derived_status": self.derived_status.value,
            "detail": self.detail,
        }


class Reconciler:
    """Scans one run for drift between physical state and JSON cache."""

    # Kinds of drift considered a P0 lie to the user. ``cache says
    # succeeded but physics disagrees`` is the exact defect the state-
    # consistency refactor was built to prevent, so it leads the list.
    _SUCCESS_LIE_STATUSES: frozenset[StageStatus] = frozenset(
        {StageStatus.NOT_STARTED, StageStatus.RUNNING, StageStatus.FAILED}
    )

    def __init__(self, session: Session) -> None:
        self._session = session
        self._calculator = StageStatusCalculator(session)

    def check_run(self, run_id: str) -> list[DriftFinding]:
        repository = RunControlRepository(self._session)
        run = repository.get_run(run_id)

        findings: list[DriftFinding] = []
        for stage in PIPELINE_STAGES:
            cache_status = _cache_status(run, stage) or "unknown"
            try:
                derived = self._calculator.stage_status(
                    run_id, run.document_id, stage
                )
            except ValueError:
                continue
            finding = self._compare(run_id, stage, cache_status, derived)
            if finding is not None:
                findings.append(finding)
        return findings

    def check_and_audit(self, run_id: str) -> list[DriftFinding]:
        findings = self.check_run(run_id)
        logger = StageTransitionLogger(self._session)
        for finding in findings:
            logger.record(
                run_id=finding.run_id,
                stage=finding.stage,
                from_status=finding.cache_status,
                to_status="drift_detected",
                triggered_by="reconciler",
                reason=f"{finding.kind}: {finding.detail}",
            )
        return findings

    # --- internals -----------------------------------------------------

    def _compare(
        self,
        run_id: str,
        stage: str,
        cache_status: str,
        derived: StageStatus,
    ) -> DriftFinding | None:
        if cache_status == "succeeded" and derived in self._SUCCESS_LIE_STATUSES:
            return DriftFinding(
                run_id=run_id,
                stage=stage,
                kind=DRIFT_KIND_CACHE_OVER_REPORTS_SUCCESS,
                cache_status=cache_status,
                derived_status=derived,
                detail=(
                    f"JSON cache claims succeeded but physical state is "
                    f"{derived.value}"
                ),
            )
        if cache_status == "running" and derived == StageStatus.NOT_STARTED:
            return DriftFinding(
                run_id=run_id,
                stage=stage,
                kind=DRIFT_KIND_CACHE_OVER_REPORTS_RUNNING,
                cache_status=cache_status,
                derived_status=derived,
                detail="JSON cache claims running but no work items or packets exist",
            )
        return None


def _cache_status(run: DocumentRun, stage_key: str) -> str | None:
    detail = run.status_detail_json or {}
    pipeline = detail.get("pipeline") if isinstance(detail, dict) else None
    if not isinstance(pipeline, dict):
        return None
    stages = pipeline.get("stages")
    if not isinstance(stages, dict):
        return None
    stage = stages.get(stage_key)
    if not isinstance(stage, dict):
        return None
    status = stage.get("status")
    return status if isinstance(status, str) else None


__all__ = [
    "DRIFT_KIND_CACHE_OVER_REPORTS_RUNNING",
    "DRIFT_KIND_CACHE_OVER_REPORTS_SUCCESS",
    "DriftFinding",
    "Reconciler",
]
