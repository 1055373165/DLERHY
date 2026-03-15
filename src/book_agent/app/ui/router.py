from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from book_agent.app.ui.page import build_homepage_html
from book_agent.core.config import get_settings


router = APIRouter(include_in_schema=False)


@router.get("/", response_class=HTMLResponse)
def homepage(_request: Request) -> HTMLResponse:
    settings = get_settings()
    return HTMLResponse(
        build_homepage_html(
            app_name=settings.app_name,
            app_version=settings.app_version,
            api_prefix=settings.api_prefix,
        )
    )
