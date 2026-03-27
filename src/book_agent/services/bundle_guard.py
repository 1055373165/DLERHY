from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from book_agent.services.runtime_bundle import RuntimeBundleService


@dataclass(frozen=True, slots=True)
class BundleGuardEvaluation:
    revision_id: str
    canary_verdict: str
    rollback_performed: bool
    effective_revision_id: str
    rollback_target_revision_id: str | None
    freeze_reason: str | None
    report_json: dict[str, Any]


class BundleGuardService:
    def __init__(
        self,
        session: Session,
        bundle_service: RuntimeBundleService | None = None,
    ):
        self._session = session
        self._bundle_service = bundle_service or RuntimeBundleService(session)

    def evaluate_canary_and_maybe_rollback(
        self,
        *,
        revision_id: str,
        report_json: dict[str, Any] | None = None,
        rollout_scope_json: dict[str, Any] | None = None,
    ) -> BundleGuardEvaluation:
        normalized_report = dict(report_json or {})
        verdict = self._resolve_verdict(normalized_report)
        record = self._bundle_service.record_canary_verdict(
            revision_id,
            verdict=verdict,
            report_json=normalized_report,
        )
        effective_revision_id = record.revision.id
        rollback_target_revision_id: str | None = None
        freeze_reason: str | None = None
        rollback_performed = False

        scope_json = dict(rollout_scope_json or record.revision.rollout_scope_json or {})
        if verdict == "failed" and self._auto_rollback_allowed(scope_json):
            freeze_reason = str(normalized_report.get("freeze_reason") or normalized_report.get("signal") or "canary_regression")
            self._bundle_service.freeze_bundle(revision_id, reason=freeze_reason)
            target = self._bundle_service.rollback_bundle(revision_id, reason=freeze_reason)
            effective_revision_id = target.revision.id
            rollback_target_revision_id = target.revision.id
            rollback_performed = True

        return BundleGuardEvaluation(
            revision_id=record.revision.id,
            canary_verdict=verdict,
            rollback_performed=rollback_performed,
            effective_revision_id=effective_revision_id,
            rollback_target_revision_id=rollback_target_revision_id,
            freeze_reason=freeze_reason,
            report_json=normalized_report,
        )

    def _resolve_verdict(self, report_json: dict[str, Any]) -> str:
        explicit_verdict = report_json.get("canary_verdict")
        if isinstance(explicit_verdict, str) and explicit_verdict.strip():
            verdict = explicit_verdict.strip().lower()
            if verdict in {"pending", "passed", "failed"}:
                return verdict
        passed = report_json.get("passed")
        if isinstance(passed, bool):
            return "passed" if passed else "failed"
        return "pending"

    def _auto_rollback_allowed(self, rollout_scope_json: dict[str, Any]) -> bool:
        mode = str(rollout_scope_json.get("mode") or "").strip().lower()
        lane = str(rollout_scope_json.get("lane") or "").strip().lower()
        auto_rollback = rollout_scope_json.get("auto_rollback")
        if mode in {"dev", "test"}:
            return True
        if lane == "canary":
            return auto_rollback is not False
        return bool(auto_rollback)
