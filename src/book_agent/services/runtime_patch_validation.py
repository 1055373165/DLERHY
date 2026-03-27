from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from book_agent.domain.enums import RuntimePatchProposalStatus
from book_agent.domain.models.ops import RuntimePatchProposal


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class RuntimePatchValidationResult:
    proposal_id: str
    status: RuntimePatchProposalStatus
    passed: bool
    report_json: dict[str, Any]


class RuntimePatchValidationService:
    def __init__(self, session: Session):
        self.session = session

    def begin_validation(self, *, proposal_id: str) -> RuntimePatchProposal:
        proposal = self._get_proposal(proposal_id)
        proposal.status = RuntimePatchProposalStatus.VALIDATING
        proposal.updated_at = _utcnow()
        self.session.add(proposal)
        self.session.flush()
        return proposal

    def record_validation_result(
        self,
        *,
        proposal_id: str,
        passed: bool,
        report_json: dict[str, Any] | None = None,
    ) -> RuntimePatchValidationResult:
        proposal = self._get_proposal(proposal_id)
        merged_report = dict(report_json or {})
        merged_report.setdefault("passed", passed)
        merged_report.setdefault("canary_verdict", "passed" if passed else "failed")
        proposal.validation_report_json = merged_report
        proposal.status = (
            RuntimePatchProposalStatus.VALIDATED if passed else RuntimePatchProposalStatus.REJECTED
        )
        proposal.updated_at = _utcnow()
        self.session.add(proposal)
        self.session.flush()
        return RuntimePatchValidationResult(
            proposal_id=proposal.id,
            status=proposal.status,
            passed=passed,
            report_json=merged_report,
        )

    def _get_proposal(self, proposal_id: str) -> RuntimePatchProposal:
        proposal = self.session.get(RuntimePatchProposal, proposal_id)
        if proposal is None:
            raise ValueError(f"RuntimePatchProposal not found: {proposal_id}")
        return proposal
