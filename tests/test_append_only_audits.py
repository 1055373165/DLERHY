"""Audit and event rows are insert-only.

Deterministic audit ids used to be written with ``session.merge``, so a second
execution of the same action for the same issue silently overwrote the first
audit row.
"""

import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from book_agent.domain.enums import ActorType
from book_agent.domain.models import AuditEvent
from book_agent.domain.models.ops import AppendOnlyViolation
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory, session_scope
from book_agent.infra.repositories.ops import OpsRepository


class AppendOnlyAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        self.engine = build_engine(f"sqlite+pysqlite:///{Path(tempdir.name) / 'audit.db'}")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _audit(self) -> AuditEvent:
        return AuditEvent(
            object_type="chapter",
            object_id=str(uuid4()),
            action="review.auto_followup.executed",
            actor_type=ActorType.SYSTEM,
            actor_id="test",
            payload_json={"attempt": 1},
        )

    def test_saving_audits_twice_keeps_both_rows(self) -> None:
        with session_scope(self.session_factory) as session:
            repository = OpsRepository(session)
            repository.save_audits([self._audit()])
            repository.save_audits([self._audit()])

        with self.session_factory() as session:
            self.assertEqual(session.query(AuditEvent).count(), 2)

    def test_updating_an_audit_row_is_rejected(self) -> None:
        with session_scope(self.session_factory) as session:
            audit = self._audit()
            session.add(audit)
        with self.session_factory() as session:
            stored = session.get(AuditEvent, audit.id)
            stored.payload_json = {"attempt": 2}
            with self.assertRaises(AppendOnlyViolation):
                session.flush()


if __name__ == "__main__":
    unittest.main()
