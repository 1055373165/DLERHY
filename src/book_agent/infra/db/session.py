from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from book_agent.core.config import AppScope, AppScopeViolation, get_settings


def build_engine(database_url: str | None = None, **kwargs) -> Engine:
    settings = get_settings()
    url = database_url or settings.database_url
    # Cross-check here as well as in create_app(): a background worker
    # or test harness that calls build_engine directly would otherwise
    # bypass the AppScope guard and happily open a Postgres connection
    # under scope=smoke.
    if settings.app_scope in {AppScope.SMOKE, AppScope.E2E} and not url.startswith("sqlite"):
        raise AppScopeViolation(
            f"build_engine refused: app_scope={settings.app_scope.value} "
            f"requires sqlite, got {url!r}."
        )
    default_kwargs: dict[str, object] = {"pool_pre_ping": True}
    if not url.startswith("sqlite"):
        default_kwargs["pool_size"] = 10
        default_kwargs["max_overflow"] = 20
    default_kwargs.update(kwargs)
    return create_engine(url, **default_kwargs)


def build_session_factory(engine: Engine | None = None, database_url: str | None = None) -> sessionmaker:
    return sessionmaker(
        bind=engine or build_engine(database_url=database_url),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


@contextmanager
def session_scope(session_factory: sessionmaker, *, commit_on_exit: bool = True) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
        if commit_on_exit:
            session.commit()
        else:
            session.rollback()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
