"""API keys, roles, organisation scoping, bootstrap path and provider URL guards (R1)."""

from __future__ import annotations

import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

from book_agent.app.main import create_app
from book_agent.core.config import AppScopeViolation, Settings, get_settings, validate_app_scope
from book_agent.domain.models.auth import DEFAULT_ORG_ID
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.services.api_keys import ApiKeyService
from book_agent.services.url_guard import UnsafeUrlError, ensure_public_http_url
from tests.test_translation_worker_abstraction import CHAPTER_XHTML, CONTAINER_XML, CONTENT_OPF, NAV_XHTML

ROOT = Path(__file__).resolve().parents[1]


class UrlGuardTests(unittest.TestCase):
    def test_only_public_http_hosts_pass(self) -> None:
        ensure_public_http_url("https://api.example.com/v1", resolver=lambda host: ["93.184.216.34"])
        for url, addresses in (
            ("http://localhost:8000", ["127.0.0.1"]),
            ("http://internal", ["10.0.0.7"]),
            ("http://metadata", ["169.254.169.254"]),
            ("http://v6", ["::1"]),
            ("http://mixed", ["93.184.216.34", "192.168.1.2"]),
        ):
            with self.assertRaises(UnsafeUrlError, msg=url):
                ensure_public_http_url(url, resolver=lambda host, addresses=addresses: addresses)
        with self.assertRaisesRegex(UnsafeUrlError, "http"):
            ensure_public_http_url("file:///etc/passwd", resolver=lambda host: ["93.184.216.34"])

    def test_prod_scope_requires_api_keys(self) -> None:
        settings = Settings(
            _env_file=None,
            app_scope="prod",
            database_url="postgresql+psycopg://u:p@db/x",
            translation_backend="openai_compatible",
            auth_mode="disabled",
        )
        with self.assertRaisesRegex(AppScopeViolation, "auth_mode"):
            validate_app_scope(settings)


class ApiAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(dir=ROOT / ".test-tmp")
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        engine = build_engine(f"sqlite+pysqlite:///{root / 'auth.db'}", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        self.session_factory = build_session_factory(engine=engine)
        with patch.dict(os.environ, {"BOOK_AGENT_AUTH_MODE": "api_key"}):
            get_settings.cache_clear()
            app = create_app()
        self.addCleanup(get_settings.cache_clear)
        self.env = patch.dict(os.environ, {"BOOK_AGENT_AUTH_MODE": "api_key"})
        self.env.start()
        self.addCleanup(self.env.stop)
        get_settings.cache_clear()
        app.state.session_factory = self.session_factory
        app.state.export_root = str(root / "exports")
        app.state.upload_root = str(root / "uploads")
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.addCleanup(self._stop_executor, app)
        with self.session_factory() as session:
            service = ApiKeyService(session)
            other = service.ensure_org("other")
            self.keys = {
                "viewer": service.create(org_id=DEFAULT_ORG_ID, name="v", role="viewer").plaintext,
                "editor": service.create(org_id=DEFAULT_ORG_ID, name="e", role="editor").plaintext,
                "admin": service.create(org_id=DEFAULT_ORG_ID, name="a", role="admin").plaintext,
                "other_editor": service.create(org_id=other.id, name="oe", role="editor").plaintext,
            }
            session.commit()
        self.epub = root / "book.epub"
        with zipfile.ZipFile(self.epub, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", CONTENT_OPF)
            archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
            archive.writestr("OEBPS/chapter1.xhtml", CHAPTER_XHTML)

    @staticmethod
    def _stop_executor(app) -> None:
        executor = getattr(app.state, "document_run_executor", None)
        if executor is not None:
            executor.stop()

    def _h(self, who: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.keys[who]}"}

    def _upload(self, who: str):
        with self.epub.open("rb") as handle:
            return self.client.post("/v1/documents/bootstrap-upload", files={"source_file": ("book.epub", handle, "application/epub+zip")}, headers=self._h(who))

    def test_keys_roles_and_public_health(self) -> None:
        self.assertEqual(self.client.get("/v1/documents/history").status_code, 401)
        self.assertEqual(self.client.get("/v1/documents/history", headers={"Authorization": "Bearer bak_nope"}).status_code, 401)
        self.assertNotEqual(self.client.get("/v1/health").status_code, 401)
        self.assertEqual(self.client.get("/v1/documents/history", headers={"X-API-Key": self.keys["viewer"]}).status_code, 200)
        self.assertEqual(self._upload("viewer").status_code, 403)
        self.assertEqual(self.client.get("/v1/providers", headers=self._h("editor")).status_code, 403)
        self.assertEqual(self.client.get("/v1/providers", headers=self._h("admin")).status_code, 200)

    def test_documents_are_scoped_to_the_key_org(self) -> None:
        mine = self._upload("editor")
        self.assertEqual(mine.status_code, 201, mine.text)
        document_id = mine.json()["document_id"]
        self.assertEqual(self.client.get(f"/v1/documents/{document_id}", headers=self._h("viewer")).status_code, 200)
        self.assertEqual(self.client.get(f"/v1/documents/{document_id}", headers=self._h("other_editor")).status_code, 404)
        self.assertEqual(self.client.get(f"/v1/documents/{document_id}/issues", headers=self._h("other_editor")).status_code, 404)
        create_run = self.client.post(
            "/v1/runs",
            json={"document_id": document_id, "run_type": "translate_full", "requested_by": "x"},
            headers=self._h("other_editor"),
        )
        self.assertEqual(create_run.status_code, 404)
        other_history = self.client.get("/v1/documents/history", headers=self._h("other_editor")).json()
        self.assertEqual(other_history["entries"], [])
        theirs = self._upload("other_editor")
        self.assertEqual(theirs.status_code, 201, theirs.text)
        self.assertNotEqual(theirs.json()["document_id"], document_id)
        self.assertEqual(len(self.client.get("/v1/documents/history", headers=self._h("viewer")).json()["entries"]), 1)

    def test_bootstrap_paths_are_confined_to_the_upload_root(self) -> None:
        response = self.client.post("/v1/documents/bootstrap", json={"source_path": str(self.epub)}, headers=self._h("editor"))
        self.assertEqual(response.status_code, 403)

    def test_admin_manages_keys_and_revocation_takes_effect(self) -> None:
        created = self.client.post("/v1/api-keys", json={"name": "ci", "role": "viewer"}, headers=self._h("admin"))
        self.assertEqual(created.status_code, 201)
        body = created.json()
        self.assertTrue(body["key"].startswith("bak_"))
        headers = {"Authorization": f"Bearer {body['key']}"}
        self.assertEqual(self.client.get("/v1/documents/history", headers=headers).status_code, 200)
        listed = self.client.get("/v1/api-keys", headers=self._h("admin")).json()
        self.assertNotIn("key", listed[0])
        self.assertIn("ci", [key["name"] for key in listed])
        self.assertEqual(self.client.post(f"/v1/api-keys/{body['id']}/revoke", headers=self._h("admin")).status_code, 200)
        self.assertEqual(self.client.get("/v1/documents/history", headers=headers).status_code, 401)
        self.assertEqual(self.client.post("/v1/api-keys", json={"name": "x", "role": "viewer"}, headers=self._h("editor")).status_code, 403)

    def test_event_stream_accepts_a_query_token(self) -> None:
        document_id = self._upload("editor").json()["document_id"]
        run = self.client.post(
            "/v1/runs", json={"document_id": document_id, "run_type": "translate_full", "requested_by": "x"}, headers=self._h("editor")
        )
        run_id = run.json()["run_id"]
        self.assertEqual(self.client.get(f"/v1/runs/{run_id}/stream").status_code, 401)
        # SQLite cannot stream (501), which proves authentication passed.
        self.assertEqual(self.client.get(f"/v1/runs/{run_id}/stream", params={"access_token": self.keys["viewer"]}).status_code, 501)
        self.assertEqual(self.client.get(f"/v1/runs/{run_id}", params={"access_token": self.keys["viewer"]}).status_code, 401)


class CreateApiKeyCliTests(unittest.TestCase):
    def test_creates_org_and_key_without_a_translation_provider(self) -> None:
        import contextlib
        import io
        import json

        from book_agent.cli import main

        with tempfile.TemporaryDirectory(dir=ROOT / ".test-tmp") as tmp:
            url = f"sqlite+pysqlite:///{Path(tmp) / 'cli.db'}"
            engine = build_engine(url)
            Base.metadata.create_all(engine)
            engine.dispose()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["--database-url", url, "create-api-key", "--name", "ops", "--org", "acme", "--role", "editor"]), 0)
            payload = json.loads(out.getvalue())
            self.assertTrue(payload["key"].startswith("bak_"))
            self.assertEqual((payload["org"], payload["role"]), ("acme", "editor"))
            engine = build_engine(url)
            try:
                with build_session_factory(engine=engine)() as session:
                    self.assertIsNotNone(ApiKeyService(session).authenticate(payload["key"]))
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
