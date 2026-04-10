from fastapi import APIRouter, Request
from sqlalchemy import text

from book_agent.app.api.deps import get_session_factory
from book_agent.core.config import get_settings
from book_agent.schemas.health import HealthResponse, MetaResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def healthcheck(request: Request) -> HealthResponse:
    session_factory = get_session_factory(request)
    with session_factory() as session:
        session.execute(text("SELECT 1"))
    return HealthResponse(status="ok")


@router.get("/meta", response_model=MetaResponse)
def meta() -> MetaResponse:
    settings = get_settings()
    return MetaResponse(
        app_name=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
        api_prefix=settings.api_prefix,
        docs_path=str(settings.docs_dir),
    )
