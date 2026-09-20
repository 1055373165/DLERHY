"""The glossary a reader can see and fix: GET/PUT /v1/documents/{id}/terms."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

from book_agent.domain.enums import ApprovalStatus, DocumentStatus, SourceType, TermType
from book_agent.domain.models import Document
from book_agent.domain.models.agent import Approval
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.services.glossary_service import GlossaryService


class TermsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        import book_agent.domain.models  # noqa: F401
        from book_agent.core.config import get_settings

        self.engine = build_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        patcher = patch.dict(
            os.environ,
            {
                "BOOK_AGENT_APP_SCOPE": "smoke",
                "BOOK_AGENT_DATABASE_URL": "sqlite+pysqlite:///:memory:",
                "BOOK_AGENT_AUTH_MODE": "disabled",
                "BOOK_AGENT_RUN_EXECUTOR_ENABLED": "false",
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        get_settings.cache_clear()
        self.addCleanup(get_settings.cache_clear)
        from book_agent.app.main import create_app

        app = create_app()
        app.state.session_factory = self.session_factory
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        with self.session_factory() as session:
            document = Document(source_type=SourceType.WEB, file_fingerprint="terms", title="Harness", status=DocumentStatus.ACTIVE)
            session.add(document)
            session.flush()
            self.document_id = document.id
            GlossaryService(session).lock_term(document.id, "harness", "驾驭框架", term_type=TermType.CONCEPT)
            session.add(
                Approval(
                    document_id=document.id,
                    kind="lock_term",
                    status=ApprovalStatus.AUTO_APPROVED,
                    policy_id="auto.fast_reading_mode",
                    decided_by="auto.fast_reading_mode",
                    payload_json={"tool": "lock_term", "arguments": {"source_term": "harness", "target_term": "驾驭框架"}},
                )
            )
            session.commit()

    def _terms(self) -> dict[str, dict]:
        response = self.client.get(f"/v1/documents/{self.document_id}/terms")
        self.assertEqual(response.status_code, 200, response.text)
        return {item["source_term"]: item for item in response.json()}

    def test_a_term_fast_reading_locked_can_be_seen_changed_and_unlocked(self) -> None:
        term = self._terms()["harness"]
        self.assertEqual((term["target_term"], term["lock_level"], term["locked_by"]), ("驾驭框架", "locked", "auto.fast_reading_mode"))

        changed = self.client.put(
            f"/v1/documents/{self.document_id}/terms",
            json={"source_term": "harness", "target_term": "运行框架", "lock": True, "decided_by": "human:alice"},
        )
        self.assertEqual(changed.status_code, 200, changed.text)
        term = self._terms()["harness"]
        self.assertEqual((term["target_term"], term["locked_by"]), ("运行框架", "human:alice"))
        with self.session_factory() as session:
            self.assertEqual(GlossaryService(session).get_locked_terms(self.document_id), {"harness": "运行框架"})

        unlocked = self.client.put(
            f"/v1/documents/{self.document_id}/terms",
            json={"source_term": "harness", "lock": False, "decided_by": "human:alice"},
        )
        self.assertEqual(unlocked.status_code, 200, unlocked.text)
        self.assertNotIn("harness", self._terms())
        with self.session_factory() as session:
            self.assertEqual(GlossaryService(session).get_locked_terms(self.document_id), {})

    def test_unknown_terms_and_missing_targets_are_refused(self) -> None:
        missing = self.client.put(
            f"/v1/documents/{self.document_id}/terms",
            json={"source_term": "nothing", "target_term": "无", "decided_by": "human:alice"},
        )
        self.assertEqual(missing.status_code, 404)
        no_target = self.client.put(
            f"/v1/documents/{self.document_id}/terms",
            json={"source_term": "harness", "lock": True, "decided_by": "human:alice"},
        )
        self.assertEqual(no_target.status_code, 422)


if __name__ == "__main__":
    unittest.main()
