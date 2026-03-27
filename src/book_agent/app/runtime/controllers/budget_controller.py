from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.domain.models.ops import DocumentRun, RunBudget


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class AutoPatchBudgetDecision:
    allowed: bool
    reason: str | None
    patch_surface: str
    max_auto_patch_attempts: int | None
    current_auto_patch_attempt_count: int
    allowed_patch_surfaces: list[str]


class BudgetController:
    def __init__(self, *, session: Session):
        self._session = session

    def evaluate_auto_patch(
        self,
        *,
        run_id: str,
        patch_surface: str,
    ) -> AutoPatchBudgetDecision:
        run = self._get_run(run_id)
        budget = self._get_budget(run_id)
        runtime_v2 = dict((run.status_detail_json or {}).get("runtime_v2") or {})
        allowed_patch_surfaces = list(runtime_v2.get("allowed_patch_surfaces") or [])
        current_auto_patch_attempt_count = int(runtime_v2.get("auto_patch_attempt_count") or 0)
        max_auto_patch_attempts = budget.max_auto_followup_attempts if budget is not None else None

        if allowed_patch_surfaces and patch_surface not in allowed_patch_surfaces:
            return AutoPatchBudgetDecision(
                allowed=False,
                reason="patch_surface_not_allowlisted",
                patch_surface=patch_surface,
                max_auto_patch_attempts=max_auto_patch_attempts,
                current_auto_patch_attempt_count=current_auto_patch_attempt_count,
                allowed_patch_surfaces=allowed_patch_surfaces,
            )
        if (
            max_auto_patch_attempts is not None
            and current_auto_patch_attempt_count >= int(max_auto_patch_attempts)
        ):
            return AutoPatchBudgetDecision(
                allowed=False,
                reason="max_auto_patch_attempts_exhausted",
                patch_surface=patch_surface,
                max_auto_patch_attempts=max_auto_patch_attempts,
                current_auto_patch_attempt_count=current_auto_patch_attempt_count,
                allowed_patch_surfaces=allowed_patch_surfaces,
            )
        return AutoPatchBudgetDecision(
            allowed=True,
            reason=None,
            patch_surface=patch_surface,
            max_auto_patch_attempts=max_auto_patch_attempts,
            current_auto_patch_attempt_count=current_auto_patch_attempt_count,
            allowed_patch_surfaces=allowed_patch_surfaces,
        )

    def record_auto_patch_attempt(
        self,
        *,
        run_id: str,
        patch_surface: str,
    ) -> AutoPatchBudgetDecision:
        decision = self.evaluate_auto_patch(run_id=run_id, patch_surface=patch_surface)
        if not decision.allowed:
            return decision

        run = self._get_run(run_id)
        runtime_v2 = dict((run.status_detail_json or {}).get("runtime_v2") or {})
        runtime_v2["auto_patch_attempt_count"] = decision.current_auto_patch_attempt_count + 1
        runtime_v2["last_auto_patch_surface"] = patch_surface
        runtime_v2["last_auto_patch_at"] = _utcnow().isoformat()
        status_detail = dict(run.status_detail_json or {})
        status_detail["runtime_v2"] = runtime_v2
        run.status_detail_json = status_detail
        run.updated_at = _utcnow()
        self._session.add(run)
        self._session.flush()
        return AutoPatchBudgetDecision(
            allowed=True,
            reason=None,
            patch_surface=patch_surface,
            max_auto_patch_attempts=decision.max_auto_patch_attempts,
            current_auto_patch_attempt_count=int(runtime_v2["auto_patch_attempt_count"]),
            allowed_patch_surfaces=decision.allowed_patch_surfaces,
        )

    def _get_run(self, run_id: str) -> DocumentRun:
        run = self._session.get(DocumentRun, run_id)
        if run is None:
            raise ValueError(f"DocumentRun not found: {run_id}")
        return run

    def _get_budget(self, run_id: str) -> RunBudget | None:
        return self._session.scalar(select(RunBudget).where(RunBudget.run_id == run_id))
