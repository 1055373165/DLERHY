"""Derived stage status + append-only transition audit (spec Phase 1).

Design north-star:
    "Stage status MUST be a faithful derivation of work-item / packet physical
    state, and MUST never reach 'succeeded' before the physical state does."

This module supplies two collaborators that enforce the north-star:

* :class:`StageStatusCalculator` — derives the status of a pipeline stage
  from ``translation_packets`` and ``work_items`` rows. **Read-only.**
  Callers (orchestrator loops, gatekeepers, read APIs) must consult this
  instead of the deprecated JSON cache at
  ``document_runs.status_detail_json.pipeline.stages.*.status``.

* :class:`StageTransitionLogger` — append-only writer to
  ``stage_transitions``. Every state flip the executor performs (stage cache
  update, work-item status change) must call :meth:`record` inside the
  same transaction; an orphan state flip with no audit row is a P0
  invariant violation for the future Reconciler.

The calculator is deliberately narrow: it only knows about the four
pipeline stages that appear on the UI — ``translate``, ``review``,
``bilingual_html``, ``merged_html``. Other work-item stages (e.g. repair)
are out of scope for stage-level status and should not call in here.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from book_agent.domain.enums import (
    ExportType,
    PacketStatus,
    WorkItemStage,
    WorkItemStatus,
)
from book_agent.domain.models import Chapter
from book_agent.domain.models.ops import StageTransition, WorkItem
from book_agent.domain.models.translation import TranslationPacket


PIPELINE_STAGES = ("translate", "review", "bilingual_html", "merged_html")

# Stage classification (spec Phase 2). A *required* stage must reach
# ``SUCCEEDED`` before the run can reach ``SUCCEEDED`` / ``SUCCEEDED_WITH_WARNINGS``;
# a failure in any required stage makes the run ``FAILED``. An *optional*
# stage can remain ``NOT_STARTED`` (the operator / run config never requested
# it) without blocking the terminal transition, and an optional-stage
# ``FAILED`` downgrades the run to ``SUCCEEDED_WITH_WARNINGS`` instead of
# failing the whole pipeline.
#
# Today ``translate`` is the only strictly required stage — without a
# translated ledger there is no artifact to hand off. Review and the two
# export stages are treated as optional because the operator may not have
# requested them (single-lang output, HTML-only, etc.). P0.2b/P0.2c will
# widen this to per-run configuration once the UI can surface the choice;
# until then, "missing work_items ⇒ not requested" is the single rule.
REQUIRED_PIPELINE_STAGES: frozenset[str] = frozenset({"translate"})
OPTIONAL_PIPELINE_STAGES: frozenset[str] = frozenset(
    {"review", "bilingual_html", "merged_html"}
)


class StageStatus(str, Enum):
    """Derived status of a pipeline stage.

    The only terminal-success value is :data:`SUCCEEDED`. ``PARTIAL`` is
    reserved for Phase 3 (skipped chapters) and must NOT be produced by
    the calculator until skip accounting lands; today any non-success
    terminal is :data:`FAILED`.
    """

    NOT_STARTED = "not_started"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"


# Legacy stage-cache labels used by ``pipeline.stages.*.status`` inside
# ``document_runs.status_detail_json``. Every read/write path that touches
# the cache must translate between :class:`StageStatus` (derived) and the
# cache vocabulary so the UI sees a stable contract even as the derivation
# layer evolves.
_STAGE_STATUS_TO_CACHE_LABEL: dict["StageStatus", str] = {
    StageStatus.NOT_STARTED: "pending",
    StageStatus.RUNNING: "running",
    StageStatus.SUCCEEDED: "succeeded",
    StageStatus.FAILED: "failed",
    StageStatus.PARTIAL: "partial",
}


def stage_status_to_cache_label(status: "StageStatus") -> str:
    """Translate a derived :class:`StageStatus` to the legacy cache label."""
    return _STAGE_STATUS_TO_CACHE_LABEL.get(status, status.value)


@dataclass(frozen=True)
class StageEvidence:
    """Raw counts behind a derived status decision (for debug / audit)."""

    stage: str
    status: StageStatus
    total_work_items: int
    succeeded_work_items: int
    failed_work_items: int
    running_work_items: int
    total_packets: int | None = None
    translated_packets: int | None = None
    failed_packets: int | None = None

    def to_dict(self) -> dict[str, int | str | None]:
        return {
            "stage": self.stage,
            "status": self.status.value,
            "total_work_items": self.total_work_items,
            "succeeded_work_items": self.succeeded_work_items,
            "failed_work_items": self.failed_work_items,
            "running_work_items": self.running_work_items,
            "total_packets": self.total_packets,
            "translated_packets": self.translated_packets,
            "failed_packets": self.failed_packets,
        }


class StageStatusCalculator:
    """Pure read-only computation of derived stage status.

    Usage::

        calc = StageStatusCalculator(session)
        if calc.stage_status(run_id, document_id, "translate") != StageStatus.SUCCEEDED:
            return False  # gate further advancement

    The calculator intentionally takes ``run_id`` AND ``document_id`` for
    packet-based stages so the join path is explicit; callers should pull
    ``document_id`` from ``DocumentRun.document_id`` once and reuse it.
    """

    RUNNING_WI_STATUSES: frozenset[WorkItemStatus] = frozenset(
        {WorkItemStatus.PENDING, WorkItemStatus.LEASED, WorkItemStatus.RUNNING,
         WorkItemStatus.RETRYABLE_FAILED}
    )

    def __init__(self, session: Session) -> None:
        self._session = session

    # --- public API ----------------------------------------------------

    def stage_status(
        self,
        run_id: str,
        document_id: str,
        stage: str,
    ) -> StageStatus:
        return self.stage_evidence(run_id, document_id, stage).status

    def stage_evidence(
        self,
        run_id: str,
        document_id: str,
        stage: str,
    ) -> StageEvidence:
        if stage == "translate":
            return self._translate_evidence(run_id, document_id)
        if stage == "review":
            return self._work_item_stage_evidence(run_id, WorkItemStage.REVIEW, "review")
        if stage == "bilingual_html":
            return self._export_stage_evidence(run_id, ExportType.BILINGUAL_HTML, "bilingual_html")
        if stage == "merged_html":
            return self._export_stage_evidence(run_id, ExportType.MERGED_HTML, "merged_html")
        raise ValueError(f"unknown pipeline stage: {stage!r}")

    # --- internals -----------------------------------------------------

    def _translate_evidence(self, run_id: str, document_id: str) -> StageEvidence:
        total_packets = self._session.scalar(
            select(func.count(TranslationPacket.id))
            .join(Chapter, Chapter.id == TranslationPacket.chapter_id)
            .where(Chapter.document_id == document_id)
        ) or 0
        translated = self._session.scalar(
            select(func.count(TranslationPacket.id))
            .join(Chapter, Chapter.id == TranslationPacket.chapter_id)
            .where(
                Chapter.document_id == document_id,
                TranslationPacket.status == PacketStatus.TRANSLATED,
            )
        ) or 0
        failed_packets = self._session.scalar(
            select(func.count(TranslationPacket.id))
            .join(Chapter, Chapter.id == TranslationPacket.chapter_id)
            .where(
                Chapter.document_id == document_id,
                TranslationPacket.status == PacketStatus.FAILED,
            )
        ) or 0

        wi_counts = self._work_item_counts(run_id, WorkItemStage.TRANSLATE)

        # Decision table. Order matters: a terminal FAILED packet is
        # authoritative, a stale unsuccessful work-item is authoritative,
        # and only "everything aligned" earns SUCCEEDED.
        if total_packets == 0 and wi_counts.total == 0:
            status = StageStatus.NOT_STARTED
        elif failed_packets > 0 or wi_counts.failed > 0:
            status = StageStatus.FAILED
        elif (
            total_packets > 0
            and translated == total_packets
            and wi_counts.total > 0
            and wi_counts.succeeded == wi_counts.total
        ):
            status = StageStatus.SUCCEEDED
        else:
            status = StageStatus.RUNNING

        return StageEvidence(
            stage="translate",
            status=status,
            total_work_items=wi_counts.total,
            succeeded_work_items=wi_counts.succeeded,
            failed_work_items=wi_counts.failed,
            running_work_items=wi_counts.running,
            total_packets=int(total_packets),
            translated_packets=int(translated),
            failed_packets=int(failed_packets),
        )

    def _work_item_stage_evidence(
        self,
        run_id: str,
        stage_enum: WorkItemStage,
        stage_key: str,
    ) -> StageEvidence:
        counts = self._work_item_counts(run_id, stage_enum)
        status = self._wi_status_from_counts(counts)
        return StageEvidence(
            stage=stage_key,
            status=status,
            total_work_items=counts.total,
            succeeded_work_items=counts.succeeded,
            failed_work_items=counts.failed,
            running_work_items=counts.running,
        )

    def _export_stage_evidence(
        self,
        run_id: str,
        export_type: ExportType,
        stage_key: str,
    ) -> StageEvidence:
        items = list(
            self._session.scalars(
                select(WorkItem).where(
                    WorkItem.run_id == run_id,
                    WorkItem.stage == WorkItemStage.EXPORT,
                )
            ).all()
        )
        items = [
            item
            for item in items
            if (item.input_version_bundle_json or {}).get("export_type") == export_type.value
        ]
        counts = _WorkItemCounts.from_items(items)
        status = self._wi_status_from_counts(counts)
        return StageEvidence(
            stage=stage_key,
            status=status,
            total_work_items=counts.total,
            succeeded_work_items=counts.succeeded,
            failed_work_items=counts.failed,
            running_work_items=counts.running,
        )

    def _work_item_counts(
        self,
        run_id: str,
        stage_enum: WorkItemStage,
    ) -> "_WorkItemCounts":
        items = list(
            self._session.scalars(
                select(WorkItem).where(
                    WorkItem.run_id == run_id,
                    WorkItem.stage == stage_enum,
                )
            ).all()
        )
        return _WorkItemCounts.from_items(items)

    def _wi_status_from_counts(self, counts: "_WorkItemCounts") -> StageStatus:
        if counts.total == 0:
            return StageStatus.NOT_STARTED
        if counts.failed > 0:
            return StageStatus.FAILED
        if counts.succeeded == counts.total:
            return StageStatus.SUCCEEDED
        return StageStatus.RUNNING


@dataclass(frozen=True)
class _WorkItemCounts:
    total: int
    succeeded: int
    failed: int
    running: int

    @classmethod
    def from_items(cls, items: Iterable[WorkItem]) -> "_WorkItemCounts":
        active = [i for i in items if i.status != WorkItemStatus.CANCELLED]
        succeeded = sum(1 for i in active if i.status == WorkItemStatus.SUCCEEDED)
        failed = sum(1 for i in active if i.status == WorkItemStatus.TERMINAL_FAILED)
        running = sum(
            1
            for i in active
            if i.status in StageStatusCalculator.RUNNING_WI_STATUSES
        )
        return cls(
            total=len(active),
            succeeded=succeeded,
            failed=failed,
            running=running,
        )


class StageTransitionLogger:
    """Append-only writer to ``stage_transitions``.

    Call :meth:`record` inside the same ``session_scope`` / transaction
    as the underlying state change. The row is flushed with the caller's
    commit; if the transaction rolls back, so does the audit row —
    which is the intended invariant ("no audit ⇒ no change").

    ``triggered_by`` tags the operator kind (``"main_loop"`` for the
    executor run-loop, ``"lease_reaper"``, ``"reconciler"``, ``"api:<op>"``
    for external API-driven transitions). ``reason`` is a free-form but
    short human label. ``caused_by_code`` defaults to the caller's
    file:lineno for postmortem correlation.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        *,
        run_id: str,
        stage: str,
        from_status: str,
        to_status: str,
        triggered_by: str,
        reason: str = "",
        work_item_id: str | None = None,
        caused_by_code: str | None = None,
    ) -> StageTransition:
        if caused_by_code is None:
            caused_by_code = _caller_site()
        transition = StageTransition(
            run_id=run_id,
            stage=stage,
            work_item_id=work_item_id,
            from_status=from_status,
            to_status=to_status,
            triggered_by=triggered_by,
            reason=reason,
            caused_by_code=caused_by_code,
        )
        self._session.add(transition)
        return transition


class RunOutcome(str, Enum):
    """Classification of a run's terminal-transition intent, derived purely
    from per-stage :class:`StageStatus` values.

    This is the *decision* layer that sits between
    :class:`StageStatusCalculator` (physical-truth derivation) and the
    run-control services that actually flip ``document_runs.status``. It
    is deliberately side-effect-free so the same mapping can be unit
    tested in isolation and reused by read-side summaries without
    committing any transition.

    Values:

    * :data:`RUNNING` — at least one required stage is not yet terminally
      successful, or an optional stage is still running. The reconciler
      must leave the run in its current non-terminal status and loop.
    * :data:`SUCCEEDED` — every required stage is ``SUCCEEDED`` and no
      optional stage failed.
    * :data:`SUCCEEDED_WITH_WARNINGS` — every required stage is
      ``SUCCEEDED`` but at least one optional stage is ``FAILED``. The
      terminal transition today still targets ``DocumentRunStatus.SUCCEEDED``
      (P0.2c introduces the dedicated enum value), but the classifier
      flags the degraded outcome so run-control can record it in
      ``status_detail_json``.
    * :data:`FAILED` — at least one required stage is ``FAILED``.
    """

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    SUCCEEDED_WITH_WARNINGS = "succeeded_with_warnings"
    FAILED = "failed"


def classify_run_outcome(
    stage_status_by_name: Mapping[str, StageStatus],
) -> RunOutcome:
    """Pure-function reduction of per-stage status → run terminal intent.

    Decision order (a required failure is always authoritative; a
    still-running optional blocks a soft-success transition to preserve
    the invariant that SUCCEEDED_WITH_WARNINGS is a *terminal* label, not
    a transient one):

    1. Any required stage ``FAILED`` ⇒ ``FAILED``.
    2. Any required stage not ``SUCCEEDED`` ⇒ ``RUNNING``.
    3. Any optional stage ``RUNNING`` ⇒ ``RUNNING``.
    4. Any optional stage ``FAILED`` ⇒ ``SUCCEEDED_WITH_WARNINGS``.
    5. Otherwise ⇒ ``SUCCEEDED``.

    ``PARTIAL`` is not produced by the calculator today and is treated
    as ``RUNNING`` at every step so the classifier gracefully no-ops if
    Phase 3 introduces it before the classifier gets taught about it.
    """

    def status_of(stage: str) -> StageStatus | None:
        return stage_status_by_name.get(stage)

    required_failed = [
        stage
        for stage in REQUIRED_PIPELINE_STAGES
        if status_of(stage) == StageStatus.FAILED
    ]
    if required_failed:
        return RunOutcome.FAILED

    required_not_succeeded = [
        stage
        for stage in REQUIRED_PIPELINE_STAGES
        if status_of(stage) != StageStatus.SUCCEEDED
    ]
    if required_not_succeeded:
        return RunOutcome.RUNNING

    optional_running = [
        stage
        for stage in OPTIONAL_PIPELINE_STAGES
        if status_of(stage) == StageStatus.RUNNING
    ]
    if optional_running:
        return RunOutcome.RUNNING

    optional_failed = [
        stage
        for stage in OPTIONAL_PIPELINE_STAGES
        if status_of(stage) == StageStatus.FAILED
    ]
    if optional_failed:
        return RunOutcome.SUCCEEDED_WITH_WARNINGS

    return RunOutcome.SUCCEEDED


def _caller_site() -> str:
    """Return ``filename:lineno`` of the call site two frames up.

    Frame 0 is this helper, frame 1 is ``record``, frame 2 is the real
    caller — the one we want pinned in the audit trail.
    """
    try:
        frame = inspect.stack()[2]
        return f"{frame.filename}:{frame.lineno}"
    except Exception:  # pragma: no cover - defensive
        return "<unknown>"


__all__ = [
    "OPTIONAL_PIPELINE_STAGES",
    "PIPELINE_STAGES",
    "REQUIRED_PIPELINE_STAGES",
    "RunOutcome",
    "StageEvidence",
    "StageStatus",
    "StageStatusCalculator",
    "StageTransitionLogger",
    "classify_run_outcome",
    "stage_status_to_cache_label",
]
