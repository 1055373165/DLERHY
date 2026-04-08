from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from sqlalchemy.engine.url import make_url

from book_agent.app.api.router import api_router
from book_agent.app.runtime.document_run_executor import ensure_document_run_executor
from book_agent.app.ui.router import router as ui_router
from book_agent.core.config import get_settings
from book_agent.core.logging import configure_logging
from book_agent.infra.db.base import Base
from book_agent.infra.db.legacy_backfill import backfill_legacy_history
from book_agent.infra.db.session import build_session_factory
from book_agent.infra.db.session import build_engine
from book_agent.infra.db.sqlite_schema_backfill import ensure_sqlite_schema_compat
from book_agent.workers.factory import build_translation_worker


def _database_error_detail(*, dialect_name: str, exc: OperationalError) -> str:
    if dialect_name == "sqlite" and "database is locked" in str(exc).lower():
        return (
            "SQLite is busy with another write. Wait for the active run to finish or retry in a "
            "moment, or switch to PostgreSQL for concurrent long-running work."
        )
    return (
        "Database unavailable. Start PostgreSQL if you configured one, "
        "or remove BOOK_AGENT_DATABASE_URL to use the local SQLite default."
    )


def _database_dialect_name(database_url: str) -> str:
    return make_url(database_url).drivername.split("+", 1)[0]


def _ensure_database_state(app: FastAPI, *, settings) -> None:
    if getattr(app.state, "session_factory", None) is not None:
        return

    engine = build_engine(database_url=settings.database_url)
    if engine.dialect.name == "sqlite":
        Base.metadata.create_all(engine)
        ensure_sqlite_schema_compat(settings.database_url)
        backfill_legacy_history(settings.database_url)
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
    app.state.database_dialect_name = _database_dialect_name(settings.database_url)
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
                "detail": _database_error_detail(
                    dialect_name=getattr(app.state, "database_dialect_name", "sqlite"),
                    exc=_exc,
                )
            },
        )

    app.include_router(ui_router)
    app.include_router(api_router, prefix=settings.api_prefix)
    return app


app = create_app()
