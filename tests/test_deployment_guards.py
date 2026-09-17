"""Deployment guards: how the API key reaches the app, and what prod scope refuses."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from book_agent.core.config import AppScope, AppScopeViolation, Settings, validate_app_scope
from book_agent.domain.enums import ProviderKind
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.services import provider_credentials as credentials
from book_agent.services import secrets


def _prod_settings(**overrides) -> Settings:
    params = dict(
        _env_file=None,
        app_scope=AppScope.PROD,
        database_url="postgresql+psycopg://postgres:postgres@db:5432/book_agent",
        translation_backend="openai_compatible",
        translation_openai_api_key="sk-test",
    )
    params.update(overrides)
    return Settings(**params)


class ApiKeySourceTests(unittest.TestCase):
    def test_namespaced_environment_key_is_honoured_while_bare_key_is_ignored(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "OPENAI_API_KEY": "global-dev-key",
                "BOOK_AGENT_TRANSLATION_OPENAI_API_KEY": "container-key",
            },
            clear=False,
        ):
            settings = Settings(_env_file=None, translation_backend="openai_compatible")
        self.assertEqual(settings.translation_openai_api_key, "container-key")

        with patch.dict("os.environ", {"OPENAI_API_KEY": "global-dev-key"}, clear=False):
            os.environ.pop("BOOK_AGENT_TRANSLATION_OPENAI_API_KEY", None)
            settings = Settings(_env_file=None, translation_backend="openai_compatible")
        self.assertIsNone(settings.translation_openai_api_key)

    def test_dotenv_key_still_wins_over_bare_shell_key(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as handle:
            handle.write("OPENAI_API_KEY=dotenv-key\n")
            env_path = Path(handle.name)
        self.addCleanup(lambda: env_path.unlink(missing_ok=True))
        with patch.dict("os.environ", {"OPENAI_API_KEY": "global-dev-key"}, clear=False):
            os.environ.pop("BOOK_AGENT_TRANSLATION_OPENAI_API_KEY", None)
            settings = Settings(_env_file=env_path, translation_backend="openai_compatible")
        self.assertEqual(settings.translation_openai_api_key, "dotenv-key")


class ProdScopeTests(unittest.TestCase):
    def test_prod_scope_refuses_the_echo_backend(self) -> None:
        with self.assertRaises(AppScopeViolation) as ctx:
            validate_app_scope(_prod_settings(translation_backend="echo"))
        self.assertIn("echo", str(ctx.exception))
        validate_app_scope(_prod_settings())  # openai_compatible is fine

    def test_dev_scope_still_allows_echo(self) -> None:
        validate_app_scope(
            Settings(
                _env_file=None,
                app_scope=AppScope.DEV,
                database_url="postgresql+psycopg://postgres:postgres@db:5432/book_agent",
                translation_backend="echo",
            )
        )

    def test_prod_scope_refuses_to_start_without_a_secret_key(self) -> None:
        dotenv = Path(tempfile.mkdtemp()) / ".env"
        with patch.object(secrets, "get_settings", return_value=_prod_settings(), create=True), patch.object(
            secrets, "_project_dotenv_path", return_value=dotenv
        ), patch.dict("os.environ", {}, clear=False):
            os.environ.pop("BOOK_AGENT_SECRET_KEY", None)
            with patch("book_agent.core.config.get_settings", return_value=_prod_settings()):
                with self.assertRaises(secrets.SecretKeyError):
                    secrets.require_configured_secret_key()
                secrets.reset_fernet_cache_for_tests()
                with self.assertRaises(secrets.SecretKeyError):
                    secrets.encrypt_secret("sk-live")
        secrets.reset_fernet_cache_for_tests()
        self.assertFalse(dotenv.exists(), "prod must not write a generated key to .env")

    def test_dev_scope_generates_and_persists_a_missing_secret_key(self) -> None:
        dotenv = Path(tempfile.mkdtemp()) / ".env"
        dev_settings = Settings(
            _env_file=None,
            app_scope=AppScope.DEV,
            database_url="postgresql+psycopg://postgres:postgres@db:5432/book_agent",
        )
        with patch.object(secrets, "_project_dotenv_path", return_value=dotenv), patch.dict(
            "os.environ", {}, clear=False
        ):
            os.environ.pop("BOOK_AGENT_SECRET_KEY", None)
            with patch("book_agent.core.config.get_settings", return_value=dev_settings):
                secrets.require_configured_secret_key()  # no-op in dev
                secrets.reset_fernet_cache_for_tests()
                ciphertext = secrets.encrypt_secret("sk-live")
                self.assertEqual(secrets.decrypt_secret(ciphertext), "sk-live")
        secrets.reset_fernet_cache_for_tests()
        self.assertIn("BOOK_AGENT_SECRET_KEY=", dotenv.read_text())

    def test_prod_scope_refuses_to_activate_the_echo_provider(self) -> None:
        engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        session = build_session_factory(engine=engine)()
        self.addCleanup(engine.dispose)
        self.addCleanup(session.close)  # cleanups run last-in first-out: close before dispose
        with patch("book_agent.services.provider_credentials.get_settings", return_value=_prod_settings()):
            with self.assertRaises(ValueError) as ctx:
                credentials.create_credential(
                    session,
                    name="echo",
                    provider_kind=ProviderKind.ECHO,
                    model_name="echo-worker",
                    base_url="https://example.invalid",
                    api_key=None,
                    streaming=False,
                    max_output_tokens=64,
                    timeout_seconds=10,
                    max_retries=0,
                    retry_backoff_seconds=1.0,
                    activate=True,
                )
            self.assertIn("prod", str(ctx.exception))
            record = credentials.create_credential(
                session,
                name="real",
                provider_kind=ProviderKind.OPENAI_COMPATIBLE,
                model_name="deepseek-chat",
                base_url="https://api.deepseek.com/v1",
                api_key="sk-live",
                streaming=False,
                max_output_tokens=64,
                timeout_seconds=10,
                max_retries=0,
                retry_backoff_seconds=1.0,
                activate=True,
            )
            self.assertTrue(record.is_active)
            # Regression for the SQLite index: a second inactive credential must be allowed.
            spare = credentials.create_credential(
                session,
                name="spare",
                provider_kind=ProviderKind.OPENAI_COMPATIBLE,
                model_name="gpt-4o",
                base_url="https://api.openai.com/v1",
                api_key="sk-spare",
                streaming=False,
                max_output_tokens=64,
                timeout_seconds=10,
                max_retries=0,
                retry_backoff_seconds=1.0,
                activate=False,
            )
            session.flush()
            self.assertFalse(spare.is_active)
            self.assertEqual(credentials.get_active_credential(session).id, record.id)


if __name__ == "__main__":
    unittest.main()
