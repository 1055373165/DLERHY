"""Organisation budget: every caller may read its own; instance admins set any organisation's."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import Field
from sqlalchemy.orm import Session

from book_agent.app.api.access import current_principal
from book_agent.app.api.deps import get_db_session
from book_agent.domain.models.auth import DEFAULT_ORG_ID
from book_agent.schemas.common import BaseSchema
from book_agent.services import org_budget

router = APIRouter()


class OrgBudgetResponse(BaseSchema):
    org_id: str
    org_name: str
    monthly_budget_usd: float | None = Field(description="Monthly model spend cap in USD; null means unlimited.")
    spent_usd: float = Field(description="Model spend attributed to the organisation's runs this calendar month (UTC).")
    remaining_usd: float | None = None
    exhausted: bool
    period_start: str


class OrgBudgetUpdate(BaseSchema):
    monthly_budget_usd: float | None = Field(default=None, ge=0, description="Null removes the cap.")


def _response(value: org_budget.OrgBudgetStatus) -> OrgBudgetResponse:
    return OrgBudgetResponse(**value.to_json())


@router.get("/current/budget", response_model=OrgBudgetResponse)
def get_current_org_budget(request: Request, session: Session = Depends(get_db_session)) -> OrgBudgetResponse:
    return _response(org_budget.budget_status(session, current_principal(request).org_id))


@router.put("/{org_id}/budget", response_model=OrgBudgetResponse)
def set_org_budget(
    org_id: str,
    payload: OrgBudgetUpdate,
    request: Request,
    session: Session = Depends(get_db_session),
) -> OrgBudgetResponse:
    principal = current_principal(request)
    # An organisation's own admins must not raise their own cap: only admins of the default
    # (operator) organisation set budgets.
    if principal.org_id != DEFAULT_ORG_ID or not principal.allows("admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="only instance admins set organisation budgets")
    try:
        org_budget.set_monthly_budget(session, org_id, payload.monthly_budget_usd)
        return _response(org_budget.budget_status(session, org_id))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
