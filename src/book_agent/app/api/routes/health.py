from fastapi import APIRouter

from book_agent.core.config import get_settings
from book_agent.schemas.health import HealthResponse, MetaResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def healthcheck() -> HealthResponse:
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

