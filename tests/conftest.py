"""Shared pytest fixtures for new-style tests.

The existing unittest modules build their own engines; new tests should take
these fixtures instead of copying that setup. Environment bootstrapping (echo
backend, fixed secret key, per-process temp root, parse-IR root) still lives
in ``tests/__init__.py`` because it must run before any book_agent import,
including when a module is executed directly with ``python -m unittest``.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory


@pytest.fixture
def tmp_root() -> Iterator[Path]:
    """A per-test directory under the repo's .test-tmp (never /tmp: the exports
    path CHECK constraint rejects OS temp prefixes)."""
    with tempfile.TemporaryDirectory(prefix="book-agent-test-") as path:
        yield Path(path)


@pytest.fixture
def sqlite_session_factory(tmp_root: Path) -> Iterator[sessionmaker]:
    """A file-backed SQLite database with the ORM schema, one per test.

    File-backed rather than in-memory so executor threads can open their own
    connections; StaticPool sharing one connection across threads is what
    used to crash the API tests.
    """
    engine = build_engine(
        f"sqlite+pysqlite:///{tmp_root / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    try:
        yield build_session_factory(engine=engine)
    finally:
        engine.dispose()


@pytest.fixture
def session(sqlite_session_factory: sessionmaker) -> Iterator[Session]:
    with sqlite_session_factory() as session:
        yield session


@pytest.fixture
def echo_worker():
    from book_agent.workers.translator import EchoTranslationWorker

    return EchoTranslationWorker(model_name="echo-worker", prompt_version="p0.echo.v1")
