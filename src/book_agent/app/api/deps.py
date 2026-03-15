from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker

from book_agent.infra.db.session import build_session_factory, session_scope


def get_session_factory(request: Request) -> sessionmaker:
    session_factory = getattr(request.app.state, "session_factory", None)
    if session_factory is None:
        session_factory = build_session_factory()
        request.app.state.session_factory = session_factory
    return session_factory


def get_db_session(request: Request) -> Iterator[Session]:
    session_factory = get_session_factory(request)
    with session_scope(session_factory) as session:
        yield session
