"""Self-hosted PatchProposal review workflow.

Exposes list/approve/reject over ``RuntimePatchProposal``. The flow:

- Repair planner proposes a patch and (optionally) marks
  ``requires_human_review=True``. The validator moves it to
  ``VALIDATED`` as usual; the controller checks the flag and refuses
  to publish until ``approved_by`` is populated here.
- Reviewers call :meth:`approve_patch_proposal`; the subsequent
  ``publish_validated_patch`` invocation then proceeds normally.
- Rejection transitions the proposal to ``REJECTED`` terminally.

All state changes emit onto the events bus so SSE/telemetry pick them up.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from book_agent.domain.enums import RuntimePatchProposalStatus
from book_agent.domain.event_kinds import PATCH_APPROVED, PATCH_REJECTED
from book_agent.domain.models.ops import RuntimeIncident, RuntimePatchProposal
from book_agent.infra.repositories.events import emit_event
from book_agent.infra.repositories.runtime_resources import RuntimeResourcesRepository


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PatchReviewError(ValueError):
    """Raised when a patch review transition is invalid."""


@dataclass(slots=True, frozen=True)
class PatchProposalSummary:
    proposal_id: str
    incident_id: str
    run_id: str | None
    status: str
    patch_surface: str | None
    requires_human_review: bool
    proposed_by: str | None
    approved_by: str | None
    approved_at: datetime | None
    rejected_by: str | None
    rejected_at: datetime | None
    review_notes: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True, frozen=True)
class PatchProposalDetail(PatchProposalSummary):
    diff_manifest_json: dict[str, Any]
    validation_report_json: dict[str, Any]
    status_detail_json: dict[str, Any]


class PatchReviewService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self._repo = RuntimeResourcesRepository(session)

    # --- queries -------------------------------------------------------------

    def list_proposals(
        self,
        *,
        statuses: list[str] | None = None,
        run_id: str | None = None,
        requires_human_review: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[PatchProposalSummary]:
        rows = self._repo.list_runtime_patch_proposals(
            statuses=statuses,
            run_id=run_id,
            requires_human_review=requires_human_review,
            limit=limit,
            offset=offset,
        )
        return [self._to_summary(p, incident) for p, incident in rows]

    def get_proposal(self, proposal_id: str) -> PatchProposalDetail:
        proposal = self._repo.get_runtime_patch_proposal(proposal_id)
        incident = self._repo.get_runtime_incident(proposal.incident_id)
        return self._to_detail(proposal, incident)

    # --- transitions ---------------------------------------------------------

    def approve_proposal(
        self,
        proposal_id: str,
        *,
        reviewer_id: str,
        notes: str | None = None,
    ) -> PatchProposalDetail:
        reviewer = reviewer_id.strip()
        if not reviewer:
            raise PatchReviewError("reviewer_id must not be empty")

        proposal = self._repo.get_runtime_patch_proposal(proposal_id)
        if proposal.status not in {
            RuntimePatchProposalStatus.PROPOSED,
            RuntimePatchProposalStatus.VALIDATING,
            RuntimePatchProposalStatus.VALIDATED,
        }:
            raise PatchReviewError(
                f"Patch proposal {proposal_id} is {proposal.status.value}; cannot approve."
            )

        now = _utcnow()
        proposal.approved_by = reviewer
        proposal.approved_at = now
        proposal.review_notes = notes
        proposal.updated_at = now

        incident = self._repo.get_runtime_incident(proposal.incident_id)
        emit_event(
            self.session,
            kind=PATCH_APPROVED,
            run_id=incident.run_id,
            actor_kind="user",
            actor_id=reviewer,
            correlation_id=f"patch:{proposal.id}",
            payload={
                "proposal_id": proposal.id,
                "incident_id": proposal.incident_id,
                "patch_surface": proposal.patch_surface,
                "notes": notes,
                "proposal_status": proposal.status.value,
            },
        )
        self.session.flush()
        return self._to_detail(proposal, incident)

    def reject_proposal(
        self,
        proposal_id: str,
        *,
        reviewer_id: str,
        reason: str,
    ) -> PatchProposalDetail:
        reviewer = reviewer_id.strip()
        if not reviewer:
            raise PatchReviewError("reviewer_id must not be empty")
        if not reason.strip():
            raise PatchReviewError("reason must not be empty")

        proposal = self._repo.get_runtime_patch_proposal(proposal_id)
        if proposal.status in {
            RuntimePatchProposalStatus.PUBLISHED,
            RuntimePatchProposalStatus.ROLLED_BACK,
            RuntimePatchProposalStatus.REJECTED,
        }:
            raise PatchReviewError(
                f"Patch proposal {proposal_id} is {proposal.status.value}; cannot reject."
            )

        now = _utcnow()
        proposal.status = RuntimePatchProposalStatus.REJECTED
        proposal.rejected_by = reviewer
        proposal.rejected_at = now
        proposal.review_notes = reason
        proposal.updated_at = now

        incident = self._repo.get_runtime_incident(proposal.incident_id)
        emit_event(
            self.session,
            kind=PATCH_REJECTED,
            run_id=incident.run_id,
            actor_kind="user",
            actor_id=reviewer,
            correlation_id=f"patch:{proposal.id}",
            payload={
                "proposal_id": proposal.id,
                "incident_id": proposal.incident_id,
                "patch_surface": proposal.patch_surface,
                "reason": reason,
            },
        )
        self.session.flush()
        return self._to_detail(proposal, incident)

    # --- mappers -------------------------------------------------------------

    @staticmethod
    def _to_summary(
        proposal: RuntimePatchProposal, incident: RuntimeIncident
    ) -> PatchProposalSummary:
        return PatchProposalSummary(
            proposal_id=proposal.id,
            incident_id=proposal.incident_id,
            run_id=incident.run_id,
            status=proposal.status.value,
            patch_surface=proposal.patch_surface,
            requires_human_review=bool(proposal.requires_human_review),
            proposed_by=proposal.proposed_by,
            approved_by=proposal.approved_by,
            approved_at=proposal.approved_at,
            rejected_by=proposal.rejected_by,
            rejected_at=proposal.rejected_at,
            review_notes=proposal.review_notes,
            created_at=proposal.created_at,
            updated_at=proposal.updated_at,
        )

    @classmethod
    def _to_detail(
        cls, proposal: RuntimePatchProposal, incident: RuntimeIncident
    ) -> PatchProposalDetail:
        summary = cls._to_summary(proposal, incident)
        return PatchProposalDetail(
            **asdict(summary),
            diff_manifest_json=dict(proposal.diff_manifest_json or {}),
            validation_report_json=dict(proposal.validation_report_json or {}),
            status_detail_json=dict(proposal.status_detail_json or {}),
        )
