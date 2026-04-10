from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from book_agent.app.api.router import api_router
from book_agent.app.runtime.document_run_executor import ensure_document_run_executor
from book_agent.app.ui.router import router as ui_router
from book_agent.core.config import get_settings
from book_agent.core.logging import configure_logging
from book_agent.infra.db.session import build_session_factory
from book_agent.infra.db.session import build_engine
from book_agent.workers.providers import ProviderHTTPError, ProviderNetworkError, ProviderTransportError
from book_agent.workers.factory import build_translation_worker


def _database_error_detail(*, exc: OperationalError) -> str:
    return (
        "Database unavailable. Ensure PostgreSQL is running and "
        "BOOK_AGENT_DATABASE_URL is configured correctly."
    )


def _provider_error_status_code(exc: ProviderTransportError) -> int:
    if isinstance(exc, ProviderNetworkError):
        return 503
    if isinstance(exc, ProviderHTTPError):
        if exc.code in {408, 409, 429} or 500 <= exc.code <= 599:
            return 503
        return 502
    return 502


def _ensure_database_state(app: FastAPI, *, settings) -> None:
    if getattr(app.state, "session_factory", None) is not None:
        return

    engine = build_engine(database_url=settings.database_url)
    app.state.engine = engine
    app.state.database_dialect_name = engine.dialect.name
    app.state.session_factory = build_session_factory(engine=engine)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        _ensure_database_state(app, settings=settings)
        ensure_document_run_executor(app)
        try:
            yield
        finally:
            executor = getattr(app.state, "document_run_executor", None)
            if executor is not None:
                executor.stop()
                app.state.document_run_executor = None
            engine = getattr(app.state, "engine", None)
            if engine is not None:
                engine.dispose()
                app.state.engine = None

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Long-document translation agent focused on traceable book translation.",
        lifespan=lifespan,
    )
    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allow_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["Content-Disposition"],
        )
    app.state.engine = None
    app.state.database_dialect_name = "postgresql"
    app.state.session_factory = None
    app.state.ensure_database_state = lambda: _ensure_database_state(app, settings=settings)
    app.state.export_root = str(settings.export_root)
    app.state.runtime_bundle_root = str(settings.runtime_bundle_root)
    app.state.upload_root = str(settings.upload_root)
    app.state.translation_worker = build_translation_worker(settings)
    app.state.document_run_executor = None

    @app.exception_handler(OperationalError)
    async def handle_operational_error(_request, _exc) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "detail": _database_error_detail(exc=_exc)
            },
        )

    @app.exception_handler(ProviderTransportError)
    async def handle_provider_transport_error(_request, _exc: ProviderTransportError) -> JSONResponse:
        return JSONResponse(
            status_code=_provider_error_status_code(_exc),
            content={"detail": str(_exc)},
        )

    app.include_router(ui_router)
    app.include_router(api_router, prefix=settings.api_prefix)
    return app


app = create_app()
