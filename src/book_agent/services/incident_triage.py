from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.core.ids import stable_id
from book_agent.domain.enums import (
    JobScopeType,
    RootCauseLayer,
    RuntimeIncidentKind,
    RuntimeIncidentStatus,
)
from book_agent.domain.models.ops import RuntimeIncident


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint_evidence(route_evidence_json: dict[str, Any]) -> dict[str, Any]:
    fingerprint_basis = route_evidence_json.get("fingerprint_basis")
    if isinstance(fingerprint_basis, dict):
        return dict(fingerprint_basis)
    normalized = dict(route_evidence_json)
    normalized.pop("captured_at", None)
    normalized.pop("observed_at", None)
    return normalized


class IncidentTriageService:
    def classify_runtime_incident_kind(
        self,
        *,
        failure_family: RootCauseLayer,
        reason_code: str,
    ) -> RuntimeIncidentKind:
        if failure_family == RootCauseLayer.REVIEW and reason_code in {
            "non_terminal_closure",
            "review_failed_without_terminality",
        }:
            return RuntimeIncidentKind.REVIEW_DEADLOCK
        if failure_family == RootCauseLayer.TRANSLATION:
            return RuntimeIncidentKind.PACKET_RUNTIME_DEFECT
        return RuntimeIncidentKind.RUNTIME_DEFECT

    def build_runtime_defect_evidence(
        self,
        *,
        run_id: str,
        scope_type: JobScopeType,
        scope_id: str,
        failure_family: RootCauseLayer,
        reason_code: str,
        runtime_bundle_revision_id: str | None,
        lane_health_state: str | None = None,
        work_item_id: str | None = None,
        chapter_run_id: str | None = None,
        review_session_id: str | None = None,
        packet_task_id: str | None = None,
        extra_json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_extra = dict(extra_json or {})
        fingerprint_basis = {
            "run_id": run_id,
            "scope_type": scope_type.value,
            "scope_id": scope_id,
            "failure_family": failure_family.value,
            "reason_code": reason_code,
            "runtime_bundle_revision_id": runtime_bundle_revision_id,
            "lane_health_state": lane_health_state,
            "chapter_run_id": chapter_run_id,
            "review_session_id": review_session_id,
            "packet_task_id": packet_task_id,
            "extra_json": normalized_extra,
        }
        evidence = {
            "evidence_version": 1,
            "run_id": run_id,
            "scope_type": scope_type.value,
            "scope_id": scope_id,
            "failure_family": failure_family.value,
            "reason_code": reason_code,
            "runtime_bundle_revision_id": runtime_bundle_revision_id,
            "lane_health_state": lane_health_state,
            "work_item_id": work_item_id,
            "chapter_run_id": chapter_run_id,
            "review_session_id": review_session_id,
            "packet_task_id": packet_task_id,
            "fingerprint_basis": fingerprint_basis,
            "captured_at": _utcnow().isoformat(),
        }
        if normalized_extra:
            evidence["extra_json"] = normalized_extra
        return evidence

    def build_route_evidence(
        self,
        *,
        run_id: str,
        scope_type: JobScopeType,
        scope_id: str,
        source_type: str,
        selected_route: str,
        runtime_bundle_revision_id: str | None,
        error_code: str,
        error_message: str,
        route_candidates: list[str] | None = None,
        extra_json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        evidence = {
            "evidence_version": 1,
            "run_id": run_id,
            "scope_type": scope_type.value,
            "scope_id": scope_id,
            "source_type": source_type,
            "selected_route": selected_route,
            "runtime_bundle_revision_id": runtime_bundle_revision_id,
            "error_code": error_code,
            "error_message": error_message,
            "route_candidates": route_candidates or [],
            "captured_at": _utcnow().isoformat(),
        }
        if extra_json:
            evidence["extra_json"] = dict(extra_json)
        return evidence

    def fingerprint_incident(
        self,
        *,
        incident_kind: RuntimeIncidentKind,
        scope_type: JobScopeType,
        scope_id: str,
        source_type: str,
        selected_route: str,
        runtime_bundle_revision_id: str | None,
        route_evidence_json: dict[str, Any],
    ) -> str:
        payload = {
            "incident_kind": incident_kind.value,
            "scope_type": scope_type.value,
            "scope_id": scope_id,
            "source_type": source_type,
            "selected_route": selected_route,
            "runtime_bundle_revision_id": runtime_bundle_revision_id,
            "route_evidence_json": _fingerprint_evidence(route_evidence_json),
        }
        digest = sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
        return stable_id("runtime-incident", digest)

    def preview_occurrence_count(
        self,
        session: Session,
        *,
        scope_type: JobScopeType,
        scope_id: str,
        incident_kind: RuntimeIncidentKind,
        source_type: str,
        selected_route: str,
        runtime_bundle_revision_id: str | None,
        route_evidence_json: dict[str, Any],
    ) -> int:
        fingerprint = self.fingerprint_incident(
            incident_kind=incident_kind,
            scope_type=scope_type,
            scope_id=scope_id,
            source_type=source_type,
            selected_route=selected_route,
            runtime_bundle_revision_id=runtime_bundle_revision_id,
            route_evidence_json=route_evidence_json,
        )
        existing = session.scalar(
            select(RuntimeIncident).where(
                RuntimeIncident.scope_type == scope_type,
                RuntimeIncident.scope_id == scope_id,
                RuntimeIncident.fingerprint == fingerprint,
            )
        )
        return int(existing.failure_count or 0) + 1 if existing is not None else 1

    def open_or_update_incident(
        self,
        session: Session,
        *,
        run_id: str,
        scope_type: JobScopeType,
        scope_id: str,
        incident_kind: RuntimeIncidentKind,
        source_type: str,
        selected_route: str,
        runtime_bundle_revision_id: str | None,
        error_code: str,
        error_message: str,
        route_evidence_json: dict[str, Any],
        latest_error_json: dict[str, Any] | None = None,
        bundle_json: dict[str, Any] | None = None,
        status_detail_json: dict[str, Any] | None = None,
        latest_work_item_id: str | None = None,
    ) -> RuntimeIncident:
        fingerprint = self.fingerprint_incident(
            incident_kind=incident_kind,
            scope_type=scope_type,
            scope_id=scope_id,
            source_type=source_type,
            selected_route=selected_route,
            runtime_bundle_revision_id=runtime_bundle_revision_id,
            route_evidence_json=route_evidence_json,
        )
        now = _utcnow()
        incident = session.scalar(
            select(RuntimeIncident).where(
                RuntimeIncident.scope_type == scope_type,
                RuntimeIncident.scope_id == scope_id,
                RuntimeIncident.fingerprint == fingerprint,
            )
        )
        error_payload = latest_error_json or {"error_code": error_code, "error_message": error_message}
        if incident is None:
            incident = RuntimeIncident(
                id=stable_id("runtime-incident-record", fingerprint, run_id),
                run_id=run_id,
                scope_type=scope_type,
                scope_id=scope_id,
                incident_kind=incident_kind,
                fingerprint=fingerprint,
                source_type=source_type,
                selected_route=selected_route,
                runtime_bundle_revision_id=runtime_bundle_revision_id,
                status=RuntimeIncidentStatus.OPEN,
                failure_count=1,
                latest_work_item_id=latest_work_item_id,
                route_evidence_json=route_evidence_json,
                latest_error_json=error_payload,
                bundle_json=bundle_json or {},
                status_detail_json=status_detail_json or {},
                resolved_at=None,
                created_at=now,
                updated_at=now,
            )
        else:
            incident.failure_count = int(incident.failure_count or 0) + 1
            incident.run_id = run_id
            incident.incident_kind = incident_kind
            incident.source_type = source_type
            incident.selected_route = selected_route
            incident.runtime_bundle_revision_id = runtime_bundle_revision_id
            incident.status = RuntimeIncidentStatus.DIAGNOSING
            incident.latest_work_item_id = latest_work_item_id or incident.latest_work_item_id
            incident.route_evidence_json = route_evidence_json
            incident.latest_error_json = error_payload
            incident.bundle_json = bundle_json or dict(incident.bundle_json or {})
            incident.status_detail_json = status_detail_json or dict(incident.status_detail_json or {})
            incident.updated_at = now
        session.add(incident)
        session.flush()
        return incident
