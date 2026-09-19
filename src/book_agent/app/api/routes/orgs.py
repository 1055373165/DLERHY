"""Organisation budget and usage: every caller may read its own; instance admins set and read any organisation's."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import Field
from sqlalchemy.orm import Session

from book_agent.app.api.access import current_principal
from book_agent.app.api.deps import get_db_session
from book_agent.domain.models.auth import DEFAULT_ORG_ID
from book_agent.schemas.common import BaseSchema
from book_agent.services import org_budget
from book_agent.services.usage_statement import usage_statement

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


def _usage_response(session: Session, org_id: str, month: str | None, format: str):
    try:
        statement = usage_statement(session, org_id, month=month)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    if format == "csv":
        return Response(
            content=statement.to_csv(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="usage-{statement.month}.csv"'},
        )
    return statement.to_json()


@router.get("/current/usage")
def get_current_org_usage(
    request: Request,
    month: str | None = Query(default=None, description="YYYY-MM (UTC); the current month when omitted."),
    format: Literal["json", "csv"] = "json",
    session: Session = Depends(get_db_session),
):
    """The caller's organisation's model usage for a month, per book: what to charge it for."""
    return _usage_response(session, current_principal(request).org_id, month, format)


@router.get("/{org_id}/usage")
def get_org_usage(
    org_id: str,
    request: Request,
    month: str | None = Query(default=None, description="YYYY-MM (UTC); the current month when omitted."),
    format: Literal["json", "csv"] = "json",
    session: Session = Depends(get_db_session),
):
    principal = current_principal(request)
    if org_id != principal.org_id and (principal.org_id != DEFAULT_ORG_ID or not principal.allows("admin")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="only instance admins read other organisations' usage")
    return _usage_response(session, org_id, month, format)
