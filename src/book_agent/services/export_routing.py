from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from book_agent.core.ids import stable_id
from book_agent.domain.enums import ExportType, SourceType
from book_agent.services.runtime_bundle import RuntimeBundleRecord, RuntimeBundleService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _route_name_for(export_type: ExportType, source_type: SourceType) -> str:
    if export_type == ExportType.REBUILT_PDF:
        return f"{source_type.value}.rebuilt_pdf_via_html"
    if export_type == ExportType.REBUILT_EPUB:
        return f"{source_type.value}.rebuilt_epub_spine"
    if export_type == ExportType.ZH_EPUB:
        return f"{source_type.value}.source_preserving_epub_patch"
    if export_type == ExportType.MERGED_HTML:
        return f"{source_type.value}.merged_html"
    if export_type == ExportType.MERGED_MARKDOWN:
        return f"{source_type.value}.merged_markdown"
    if export_type == ExportType.BILINGUAL_HTML:
        return f"chapter.bilingual_html.{source_type.value}"
    if export_type == ExportType.BILINGUAL_MARKDOWN:
        return f"chapter.bilingual_markdown.{source_type.value}"
    if export_type == ExportType.REVIEW_PACKAGE:
        return f"chapter.review_package.{source_type.value}"
    return f"{source_type.value}.{export_type.value}"


def _expected_route_candidates(export_type: ExportType, source_type: SourceType) -> list[str]:
    return [_route_name_for(export_type, source_type)]


@dataclass(slots=True)
class ExportRouteDecision:
    document_id: str
    export_type: ExportType
    source_type: SourceType
    selected_route: str
    expected_route_candidates: list[str]
    policy_route_candidates: list[str]
    runtime_bundle_revision_id: str | None
    runtime_bundle_revision_name: str | None
    runtime_bundle_manifest_path: str | None
    route_policy_json: dict[str, Any]
    route_evidence_json: dict[str, Any]


class ExportRoutingError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        route_decision: ExportRouteDecision,
    ) -> None:
        super().__init__(message)
        self.route_decision = route_decision
        self.route_evidence_json = dict(route_decision.route_evidence_json)
        self.expected_route_candidates = list(route_decision.expected_route_candidates)
        self.policy_route_candidates = list(route_decision.policy_route_candidates)
        self.selected_route = route_decision.selected_route
        self.runtime_bundle_revision_id = route_decision.runtime_bundle_revision_id


class ExportRoutingService:
    def __init__(self, *, runtime_bundle_service: RuntimeBundleService):
        self._runtime_bundle_service = runtime_bundle_service

    def resolve_document_route(
        self,
        *,
        document,
        export_type: ExportType,
        runtime_bundle_revision_id: str | None = None,
    ) -> ExportRouteDecision:
        source_type = document.source_type
        if not isinstance(source_type, SourceType):
            source_type = SourceType(str(source_type))

        if runtime_bundle_revision_id is not None:
            bundle_record = self._runtime_bundle_service.lookup_bundle(runtime_bundle_revision_id)
            return self._resolve_with_bundle(
                document_id=str(document.id),
                source_type=source_type,
                export_type=export_type,
                bundle_record=bundle_record,
            )

        try:
            bundle_record = self._runtime_bundle_service.lookup_active_bundle()
        except ValueError:
            return self._resolve_without_bundle(
                document_id=str(document.id),
                source_type=source_type,
                export_type=export_type,
            )
        return self._resolve_with_bundle(
            document_id=str(document.id),
            source_type=source_type,
            export_type=export_type,
            bundle_record=bundle_record,
        )

    def build_route_evidence(
        self,
        *,
        document_id: str,
        source_type: SourceType,
        export_type: ExportType,
        selected_route: str,
        expected_route_candidates: list[str],
        policy_route_candidates: list[str],
        runtime_bundle_revision_id: str | None,
        runtime_bundle_revision_name: str | None,
        runtime_bundle_manifest_path: str | None,
        route_policy_json: dict[str, Any],
        route_decision_source: str,
        extra_json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        evidence = {
            "evidence_version": 1,
            "document_id": document_id,
            "source_type": source_type.value,
            "export_type": export_type.value,
            "selected_route": selected_route,
            "expected_route_candidates": list(expected_route_candidates),
            "policy_route_candidates": list(policy_route_candidates),
            "runtime_bundle_revision_id": runtime_bundle_revision_id,
            "runtime_bundle_revision_name": runtime_bundle_revision_name,
            "runtime_bundle_manifest_path": runtime_bundle_manifest_path,
            "route_policy_json": dict(route_policy_json),
            "route_decision_source": route_decision_source,
            "captured_at": _utcnow().isoformat(),
        }
        if extra_json:
            evidence["extra_json"] = dict(extra_json)
        evidence["route_fingerprint"] = stable_id(
            "export-route-evidence",
            document_id,
            source_type.value,
            export_type.value,
            selected_route,
            runtime_bundle_revision_id or "none",
        )
        return evidence

    def _resolve_with_bundle(
        self,
        *,
        document_id: str,
        source_type: SourceType,
        export_type: ExportType,
        bundle_record: RuntimeBundleRecord,
    ) -> ExportRouteDecision:
        bundle_payload = _as_dict(bundle_record.manifest_json.get("manifest_json"))
        routing_policy = _as_dict(bundle_payload.get("routing_policy"))
        export_routes = _as_dict(routing_policy.get("export_routes"))
        route_policy = _as_dict(export_routes.get(export_type.value))

        expected_route_candidates = _expected_route_candidates(export_type, source_type)
        policy_route_candidates = self._policy_route_candidates(route_policy) or list(expected_route_candidates)
        selected_route = str(route_policy.get("selected_route") or expected_route_candidates[0]).strip()
        route_decision_source = "bundle_policy" if route_policy else "default"

        route_policy_source_types = [
            str(value).strip()
            for value in route_policy.get("source_types", [])
            if str(value).strip()
        ]
        if route_policy_source_types and source_type.value not in route_policy_source_types:
            decision = self._build_route_decision(
                document_id=document_id,
                source_type=source_type,
                export_type=export_type,
                selected_route=selected_route,
                expected_route_candidates=expected_route_candidates,
                policy_route_candidates=policy_route_candidates,
                bundle_record=bundle_record,
                route_policy_json=route_policy,
                route_decision_source=route_decision_source,
            )
            raise ExportRoutingError(
                f"Selected route '{selected_route}' is not compatible with source type {source_type.value}.",
                route_decision=decision,
            )

        if selected_route not in expected_route_candidates:
            decision = self._build_route_decision(
                document_id=document_id,
                source_type=source_type,
                export_type=export_type,
                selected_route=selected_route,
                expected_route_candidates=expected_route_candidates,
                policy_route_candidates=policy_route_candidates,
                bundle_record=bundle_record,
                route_policy_json=route_policy,
                route_decision_source=route_decision_source,
            )
            raise ExportRoutingError(
                f"Selected route '{selected_route}' does not match the expected route family for "
                f"{source_type.value}/{export_type.value}.",
                route_decision=decision,
            )

        return self._build_route_decision(
            document_id=document_id,
            source_type=source_type,
            export_type=export_type,
            selected_route=selected_route,
            expected_route_candidates=expected_route_candidates,
            policy_route_candidates=policy_route_candidates,
            bundle_record=bundle_record,
            route_policy_json=route_policy,
            route_decision_source=route_decision_source,
        )

    def _build_route_decision(
        self,
        *,
        document_id: str,
        source_type: SourceType,
        export_type: ExportType,
        selected_route: str,
        expected_route_candidates: list[str],
        policy_route_candidates: list[str],
        bundle_record: RuntimeBundleRecord,
        route_policy_json: dict[str, Any],
        route_decision_source: str,
    ) -> ExportRouteDecision:
        route_evidence_json = self.build_route_evidence(
            document_id=document_id,
            source_type=source_type,
            export_type=export_type,
            selected_route=selected_route,
            expected_route_candidates=expected_route_candidates,
            policy_route_candidates=policy_route_candidates,
            runtime_bundle_revision_id=bundle_record.revision.id,
            runtime_bundle_revision_name=bundle_record.revision.revision_name,
            runtime_bundle_manifest_path=str(bundle_record.manifest_path),
            route_policy_json=route_policy_json,
            route_decision_source=route_decision_source,
        )
        return ExportRouteDecision(
            document_id=document_id,
            export_type=export_type,
            source_type=source_type,
            selected_route=selected_route,
            expected_route_candidates=list(expected_route_candidates),
            policy_route_candidates=list(policy_route_candidates),
            runtime_bundle_revision_id=bundle_record.revision.id,
            runtime_bundle_revision_name=bundle_record.revision.revision_name,
            runtime_bundle_manifest_path=str(bundle_record.manifest_path),
            route_policy_json=dict(route_policy_json),
            route_evidence_json=route_evidence_json,
        )

    def _resolve_without_bundle(
        self,
        *,
        document_id: str,
        source_type: SourceType,
        export_type: ExportType,
    ) -> ExportRouteDecision:
        expected_route_candidates = _expected_route_candidates(export_type, source_type)
        selected_route = expected_route_candidates[0]
        route_policy_json: dict[str, Any] = {}
        route_evidence_json = self.build_route_evidence(
            document_id=document_id,
            source_type=source_type,
            export_type=export_type,
            selected_route=selected_route,
            expected_route_candidates=expected_route_candidates,
            policy_route_candidates=expected_route_candidates,
            runtime_bundle_revision_id=None,
            runtime_bundle_revision_name=None,
            runtime_bundle_manifest_path=None,
            route_policy_json=route_policy_json,
            route_decision_source="default",
        )
        return ExportRouteDecision(
            document_id=document_id,
            export_type=export_type,
            source_type=source_type,
            selected_route=selected_route,
            expected_route_candidates=list(expected_route_candidates),
            policy_route_candidates=list(expected_route_candidates),
            runtime_bundle_revision_id=None,
            runtime_bundle_revision_name=None,
            runtime_bundle_manifest_path=None,
            route_policy_json=route_policy_json,
            route_evidence_json=route_evidence_json,
        )

    def _policy_route_candidates(self, route_policy: dict[str, Any]) -> list[str]:
        candidates: list[str] = []
        for key in ("route_candidates", "allowed_routes"):
            raw = route_policy.get(key)
            if not isinstance(raw, list):
                continue
            for value in raw:
                candidate = str(value).strip()
                if candidate and candidate not in candidates:
                    candidates.append(candidate)
        return candidates
