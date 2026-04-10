from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from book_agent.core.config import get_settings


def build_engine(database_url: str | None = None, **kwargs) -> Engine:
    settings = get_settings()
    url = database_url or settings.database_url
    default_kwargs: dict[str, object] = {
        "pool_pre_ping": True,
        "pool_size": 10,
        "max_overflow": 20,
    }
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
