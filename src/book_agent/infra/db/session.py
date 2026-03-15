from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session, sessionmaker

from book_agent.core.config import get_settings


def build_engine(database_url: str | None = None, **kwargs) -> Engine:
    settings = get_settings()
    url = database_url or settings.database_url
    default_kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        parsed = make_url(url)
        database_path = parsed.database
        if database_path and database_path != ":memory:":
            Path(database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        connect_args = dict(default_kwargs.get("connect_args", {}))
        connect_args.setdefault("check_same_thread", False)
        default_kwargs["connect_args"] = connect_args
    default_kwargs.update(kwargs)
    engine = create_engine(url, **default_kwargs)
    if engine.dialect.name == "sqlite":
        _enable_sqlite_foreign_keys(engine)
    return engine


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        try:
            cursor.execute("PRAGMA synchronous=NORMAL")
        except Exception:
            pass
        cursor.close()


def build_session_factory(engine: Engine | None = None, database_url: str | None = None) -> sessionmaker:
    return sessionmaker(
        bind=engine or build_engine(database_url=database_url),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


@contextmanager
def session_scope(session_factory: sessionmaker) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
