from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from book_agent.app.api.router import api_router
from book_agent.app.runtime.document_run_executor import DocumentRunExecutor
from book_agent.app.ui.router import router as ui_router
from book_agent.core.config import get_settings
from book_agent.core.logging import configure_logging
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_session_factory
from book_agent.infra.db.session import build_engine
from book_agent.workers.factory import build_translation_worker


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Long-document translation agent focused on traceable book translation.",
    )
    engine = build_engine(database_url=settings.database_url)
    if engine.dialect.name == "sqlite":
        Base.metadata.create_all(engine)
    app.state.session_factory = build_session_factory(engine=engine)
    app.state.export_root = str(settings.export_root)
    app.state.upload_root = str(settings.upload_root)
    app.state.translation_worker = build_translation_worker(settings)
    app.state.document_run_executor = None

    @app.exception_handler(OperationalError)
    async def handle_operational_error(_request, _exc) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "detail": (
                    "Database unavailable. Start PostgreSQL if you configured one, "
                    "or remove BOOK_AGENT_DATABASE_URL to use the local SQLite default."
                )
            },
        )

    @app.on_event("startup")
    async def startup_run_executor() -> None:
        executor = DocumentRunExecutor(
            session_factory=app.state.session_factory,
            export_root=app.state.export_root,
            translation_worker=app.state.translation_worker,
        )
        executor.start()
        app.state.document_run_executor = executor

    @app.on_event("shutdown")
    async def shutdown_run_executor() -> None:
        executor = getattr(app.state, "document_run_executor", None)
        if executor is not None:
            executor.stop()
            app.state.document_run_executor = None

    app.include_router(ui_router)
    app.include_router(api_router, prefix=settings.api_prefix)
    return app


app = create_app()
