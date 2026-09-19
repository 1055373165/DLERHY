# ruff: noqa: E402

import os
import sys
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(__file__))
SRC = os.path.join(ROOT, "src")
os.environ.setdefault("BOOK_AGENT_TRANSLATION_BACKEND", "echo")
os.environ.setdefault("BOOK_AGENT_TRANSLATION_MODEL", "echo-worker")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from sqlalchemy.exc import OperationalError

from book_agent.app.api.deps import get_db_session
from book_agent.app.main import _database_error_detail


class ApiDepsTests(unittest.TestCase):
    def test_get_db_session_uses_rollback_exit_for_get_requests(self) -> None:
        session_factory = object()
        request = SimpleNamespace(
            method="GET",
            app=SimpleNamespace(state=SimpleNamespace(session_factory=session_factory)),
        )
        fake_session = object()
        captured: dict[str, object] = {}

        @contextmanager
        def fake_session_scope(factory, *, commit_on_exit=True):
            captured["factory"] = factory
            captured["commit_on_exit"] = commit_on_exit
            yield fake_session

        with patch("book_agent.app.api.deps.session_scope", fake_session_scope):
            generator = get_db_session(request)
            self.assertIs(next(generator), fake_session)
            with self.assertRaises(StopIteration):
                next(generator)

        self.assertIs(captured["factory"], session_factory)
        self.assertFalse(captured["commit_on_exit"])

    def test_get_db_session_keeps_commit_exit_for_write_requests(self) -> None:
        session_factory = object()
        request = SimpleNamespace(
            method="POST",
            app=SimpleNamespace(state=SimpleNamespace(session_factory=session_factory)),
        )
        fake_session = object()
        captured: dict[str, object] = {}

        @contextmanager
        def fake_session_scope(factory, *, commit_on_exit=True):
            captured["factory"] = factory
            captured["commit_on_exit"] = commit_on_exit
            yield fake_session

        with patch("book_agent.app.api.deps.session_scope", fake_session_scope):
            generator = get_db_session(request)
            self.assertIs(next(generator), fake_session)
            with self.assertRaises(StopIteration):
                next(generator)

        self.assertIs(captured["factory"], session_factory)
        self.assertTrue(captured["commit_on_exit"])

    def test_database_error_message_keeps_pg_guidance(self) -> None:
        exc = OperationalError("SELECT 1", {}, Exception("connection refused"))
        detail = _database_error_detail(exc=exc)
        self.assertIn("BOOK_AGENT_DATABASE_URL", detail)
