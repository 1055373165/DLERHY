"""Per-organisation provider credentials and monthly budgets (R1 follow-up)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

from book_agent.app.main import create_app
from book_agent.app.runtime.document_run_executor import DocumentRunExecutor
from book_agent.core.config import Settings, get_settings
from book_agent.core.run_context import bind_run_context
from book_agent.domain.enums import DocumentRunType, DocumentStatus, ProviderKind, SourceType
from book_agent.domain.event_kinds import LLM_CALL_COMPLETED
from book_agent.domain.models import Document
from book_agent.domain.models.auth import DEFAULT_ORG_ID
from book_agent.domain.models.ops import DocumentRun
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.events import emit_event
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.services import org_budget
from book_agent.services import provider_credentials as credentials
from book_agent.services.api_keys import ApiKeyService
from book_agent.services.run_control import RunControlService, RunControlTransitionError
from book_agent.services.run_execution import RunExecutionService
from book_agent.workers.factory import TranslationWorkerProvider

ROOT = Path(__file__).resolve().parents[1]


def _echo(session, name: str, model: str, *, scope: str | None, activate: bool = True):
    return credentials.create_credential(
        session,
        name=name,
        provider_kind=ProviderKind.ECHO,
        model_name=model,
        base_url="http://localhost",
        api_key=None,
        streaming=False,
        max_output_tokens=1024,
        timeout_seconds=30,
        max_retries=0,
        retry_backoff_seconds=1.0,
        activate=activate,
        scope=scope,
    )


class _Db(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(dir=ROOT / ".test-tmp")
        self.addCleanup(self.tempdir.cleanup)
        self.engine = build_engine(
            f"sqlite+pysqlite:///{Path(self.tempdir.name) / 'tenancy.db'}",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        org_budget.invalidate_cache()
        self.addCleanup(org_budget.invalidate_cache)
        with self.session_factory() as session:
            self.other_org_id = ApiKeyService(session).ensure_org("acme").id
            session.commit()

    def _document(self, org_id: str) -> str:
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"tenancy-{uuid4()}",
                source_path="/srv/books/t.epub",
                title="Tenancy",
                src_lang="en",
                tgt_lang="zh",
                status=DocumentStatus.ACTIVE,
                parser_version=1,
                segmentation_version=1,
                org_id=org_id,
            )
            session.add(document)
            session.commit()
            return document.id

    def _queued_run(self, org_id: str) -> str:
        document_id = self._document(org_id)
        with self.session_factory() as session:
            run = RunControlService(RunControlRepository(session)).create_run(
                document_id=document_id, run_type=DocumentRunType.TRANSLATE_FULL, requested_by="tenancy-test"
            )
            session.commit()
            return run.run_id

    def _spend(self, run_id: str, usd: float) -> None:
        with self.session_factory() as session:
            emit_event(session, kind=LLM_CALL_COMPLETED, run_id=run_id, payload={"call_kind": "translate", "cost_usd": usd})
            session.commit()


class CredentialScopeTests(_Db):
    def test_organisations_use_their_own_active_credential_else_the_shared_one(self) -> None:
        with self.session_factory() as session:
            _echo(session, "shared", "shared-model", scope=None)
            session.commit()
        provider = TranslationWorkerProvider(
            settings=Settings(translation_backend="echo"), session_factory=lambda: self.session_factory, db_check_interval_seconds=0
        )
        self.assertEqual(provider.get(self.other_org_id).metadata().model_name, "shared-model")
        with self.session_factory() as session:
            _echo(session, "acme", "acme-model", scope=self.other_org_id)
            session.commit()
            # Activating the organisation's credential leaves the shared one active.
            self.assertEqual(credentials.get_active_credential(session, None).name, "shared")
        self.assertEqual(provider.get(self.other_org_id).metadata().model_name, "acme-model")
        self.assertEqual(provider.get(DEFAULT_ORG_ID).metadata().model_name, "shared-model")
        self.assertEqual(provider.get(None).metadata().model_name, "shared-model")

    def test_executor_resolves_the_worker_of_the_runs_organisation(self) -> None:
        run_id = self._queued_run(self.other_org_id)
        seen: list[str | None] = []

        def resolver(org_id=None):
            seen.append(org_id)
            return None

        executor = DocumentRunExecutor(
            session_factory=self.session_factory, export_root="artifacts/exports", translation_worker=None, translation_worker_resolver=resolver
        )
        with bind_run_context(run_id):
            executor._current_translation_worker()
        executor._current_translation_worker()
        self.assertEqual(seen, [self.other_org_id, None])


class ProviderRoutesTests(_Db):
    def setUp(self) -> None:
        super().setUp()
        with self.session_factory() as session:
            service = ApiKeyService(session)
            self.keys = {
                "admin": service.create(org_id=DEFAULT_ORG_ID, name="a", role="admin").plaintext,
                "acme_admin": service.create(org_id=self.other_org_id, name="aa", role="admin").plaintext,
                "acme_editor": service.create(org_id=self.other_org_id, name="ae", role="editor").plaintext,
            }
            shared = _echo(session, "shared", "shared-model", scope=None)
            self.shared_id = shared.id
            session.commit()
        env = {"BOOK_AGENT_AUTH_MODE": "api_key", "BOOK_AGENT_RUN_EXECUTOR_ENABLED": "false"}
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        get_settings.cache_clear()
        self.addCleanup(get_settings.cache_clear)
        app = create_app()
        app.state.session_factory = self.session_factory
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def _h(self, who: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.keys[who]}"}

    def test_admins_manage_only_their_scope(self) -> None:
        body = {
            "name": "acme own",
            "provider_kind": "echo",
            "model_name": "acme-model",
            "base_url": "http://localhost",
            "activate": False,
        }
        # The organisation falls back to the shared provider, without seeing its key preview.
        active = self.client.get("/v1/providers/active", headers=self._h("acme_admin")).json()
        self.assertEqual((active["name"], active["shared"], active["api_key_preview"]), ("shared", True, None))
        created = self.client.post("/v1/providers", json=body, headers=self._h("acme_admin"))
        self.assertEqual(created.status_code, 201, created.text)
        self.assertFalse(created.json()["shared"])
        acme_list = [row["name"] for row in self.client.get("/v1/providers", headers=self._h("acme_admin")).json()]
        self.assertEqual(acme_list, ["acme own"])
        shared_list = [row["name"] for row in self.client.get("/v1/providers", headers=self._h("admin")).json()]
        self.assertEqual(shared_list, ["shared"])
        # Another organisation's (or the shared) credential is not found for this admin.
        for method, path in (
            ("patch", f"/v1/providers/{self.shared_id}"),
            ("delete", f"/v1/providers/{self.shared_id}"),
            ("post", f"/v1/providers/{self.shared_id}/activate"),
            ("post", f"/v1/providers/{self.shared_id}/test"),
        ):
            kwargs = {"json": {"name": "hijack"}} if method == "patch" else {}
            response = getattr(self.client, method)(path, headers=self._h("acme_admin"), **kwargs)
            self.assertEqual(response.status_code, 404, (method, path, response.text))
        own_id = created.json()["id"]
        self.assertEqual(self.client.post(f"/v1/providers/{own_id}/activate", headers=self._h("admin")).status_code, 404)
        self.assertEqual(self.client.post(f"/v1/providers/{own_id}/activate", headers=self._h("acme_admin")).status_code, 200)
        active = self.client.get("/v1/providers/active", headers=self._h("acme_admin")).json()
        self.assertEqual((active["name"], active["shared"]), ("acme own", False))
        self.assertEqual(self.client.get("/v1/providers/active", headers=self._h("admin")).json()["name"], "shared")

    def test_budgets_are_set_by_instance_admins_and_read_by_members(self) -> None:
        path = f"/v1/orgs/{self.other_org_id}/budget"
        self.assertEqual(self.client.put(path, json={"monthly_budget_usd": 5}, headers=self._h("acme_editor")).status_code, 403)
        self.assertEqual(self.client.put(path, json={"monthly_budget_usd": 500}, headers=self._h("acme_admin")).status_code, 403)
        updated = self.client.put(path, json={"monthly_budget_usd": 5}, headers=self._h("admin"))
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["monthly_budget_usd"], 5.0)
        run_id = self._queued_run(self.other_org_id)
        self._spend(run_id, 6.0)
        current = self.client.get("/v1/orgs/current/budget", headers=self._h("acme_editor")).json()
        self.assertEqual((current["org_name"], current["spent_usd"], current["exhausted"]), ("acme", 6.0, True))
        refused = self.client.post(f"/v1/runs/{run_id}/resume", json={"actor_id": "t"}, headers=self._h("acme_editor"))
        self.assertEqual(refused.status_code, 409)
        self.assertIn("monthly model budget", refused.json()["detail"])
        self.assertEqual(self.client.put(path, json={"monthly_budget_usd": None}, headers=self._h("admin")).status_code, 200)
        self.assertEqual(
            self.client.post(f"/v1/runs/{run_id}/resume", json={"actor_id": "t"}, headers=self._h("acme_editor")).status_code, 200
        )


class BudgetEnforcementTests(_Db):
    def test_spend_counts_this_months_run_calls_of_the_organisation_only(self) -> None:
        mine = self._queued_run(self.other_org_id)
        theirs = self._queued_run(DEFAULT_ORG_ID)
        self._spend(mine, 1.25)
        self._spend(mine, 0.75)
        self._spend(theirs, 9.0)
        with self.session_factory() as session:
            org_budget.set_monthly_budget(session, self.other_org_id, 3)
            status = org_budget.budget_status(session, self.other_org_id)
        self.assertEqual((status.spent_usd, status.remaining_usd, status.exhausted), (2.0, 1.0, False))

    def test_running_runs_pause_once_the_budget_is_used_up(self) -> None:
        run_id = self._queued_run(self.other_org_id)
        with self.session_factory() as session:
            RunControlService(RunControlRepository(session)).resume_run(run_id, actor_id="t")
            org_budget.set_monthly_budget(session, self.other_org_id, 1)
            session.commit()
        self._spend(run_id, 1.5)
        with self.session_factory() as session:
            repository = RunControlRepository(session)
            result = RunExecutionService(repository, RunControlService(repository)).enforce_budget_guardrails(run_id=run_id)
            session.commit()
        self.assertTrue(result.budget_exceeded)
        self.assertEqual(result.stop_reason, org_budget.STOP_REASON)
        with self.session_factory() as session:
            run = session.get(DocumentRun, run_id)
            self.assertEqual((run.status.value, run.stop_reason), ("paused", org_budget.STOP_REASON))
            with self.assertRaisesRegex(RunControlTransitionError, "monthly model budget"):
                RunControlService(RunControlRepository(session)).resume_run(run_id, actor_id="t")


if __name__ == "__main__":
    unittest.main()
