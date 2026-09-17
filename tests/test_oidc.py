"""OIDC bearer tokens: signature, issuer, audience, expiry, algorithm, key rotation, org and role claims, API wiring."""

from __future__ import annotations

import json
import os
import time
import unittest
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from sqlalchemy.pool import StaticPool

from book_agent.core.config import AppScopeViolation, Settings, get_settings, validate_app_scope
from book_agent.domain.models.auth import DEFAULT_ORG_ID
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.services import oidc
from book_agent.services.api_keys import ApiKeyService

ISSUER = "https://login.example.com/realms/books"
AUDIENCE = "book-agent"


def _key(kid: str):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(private.public_key()))
    jwk.update({"kid": kid, "alg": "RS256", "use": "sig"})
    return private, jwk


class _Issuer:
    """Discovery document and JWKS served from memory; counts fetches."""

    def __init__(self) -> None:
        self.private, jwk = _key("k1")
        self.jwks = {"keys": [jwk]}
        self.fetches: list[str] = []

    def get(self, url: str):
        self.fetches.append(url)
        if url.endswith("/.well-known/openid-configuration"):
            return {"issuer": ISSUER, "jwks_uri": f"{ISSUER}/certs"}
        if url == f"{ISSUER}/certs":
            return self.jwks
        raise AssertionError(url)

    def rotate(self) -> None:
        self.private, jwk = _key("k2")
        self.jwks = {"keys": [jwk]}

    def token(self, *, kid: str | None = None, key=None, algorithm: str = "RS256", **claims) -> str:
        payload = {"iss": ISSUER, "aud": AUDIENCE, "sub": "user-1", "exp": int(time.time()) + 300, "org": "default", "book_agent_role": "editor"}
        payload.update(claims)
        payload = {name: value for name, value in payload.items() if value is not None}
        return jwt.encode(payload, key or self.private, algorithm=algorithm, headers={"kid": kid or self.jwks["keys"][0]["kid"]})


def _settings(**overrides) -> Settings:
    values = dict(_env_file=None, auth_mode="api_key", oidc_issuer=ISSUER, oidc_audience=AUDIENCE)
    values.update(overrides)
    return Settings(**values)


class OidcVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        with self.session_factory() as session:
            ApiKeyService(session).ensure_org("default")
            session.commit()
        self.issuer = _Issuer()
        self.now = [1000.0]
        self.verifier = oidc.OidcVerifier(_settings(), http_get=self.issuer.get, clock=lambda: self.now[0])

    def _verify(self, token: str, verifier=None):
        with self.session_factory() as session:
            return (verifier or self.verifier).verify(session, token)

    def test_a_valid_token_maps_to_org_role_and_subject(self) -> None:
        identity = self._verify(self.issuer.token())
        self.assertEqual((identity.org_id, identity.role, identity.subject), (DEFAULT_ORG_ID, "editor", "user-1"))
        # Several roles: the strongest known one.
        self.assertEqual(self._verify(self.issuer.token(book_agent_role=["viewer", "admin", "owner"])).role, "admin")

    def test_wrong_audience_issuer_expiry_and_signature_are_refused(self) -> None:
        other_private, _ = _key("k1")
        for token, message in (
            (self.issuer.token(aud="another-client"), "(?i)audience"),
            (self.issuer.token(iss="https://evil.example.com"), "(?i)issuer"),
            (self.issuer.token(exp=int(time.time()) - 120), "(?i)expired"),
            (self.issuer.token(key=other_private), "(?i)signature"),
            (self.issuer.token(sub=None), "sub"),
        ):
            with self.assertRaisesRegex(oidc.OidcError, message):
                self._verify(token)

    def test_symmetric_and_none_algorithms_are_refused(self) -> None:
        public_pem = self.issuer.jwks["keys"][0]["n"]
        hs = jwt.encode({"iss": ISSUER, "aud": AUDIENCE, "sub": "x", "exp": int(time.time()) + 60}, public_pem, algorithm="HS256", headers={"kid": "k1"})
        with self.assertRaisesRegex(oidc.OidcError, "algorithm"):
            self._verify(hs)
        unsigned = jwt.encode({"iss": ISSUER, "aud": AUDIENCE, "sub": "x"}, None, algorithm="none", headers={"kid": "k1"})
        with self.assertRaisesRegex(oidc.OidcError, "algorithm"):
            self._verify(unsigned)

    def test_org_and_role_claims(self) -> None:
        with self.assertRaisesRegex(oidc.OidcError, "unknown organisation"):
            self._verify(self.issuer.token(org="nobody"))
        with self.assertRaisesRegex(oidc.OidcError, "org claim"):
            self._verify(self.issuer.token(org=None))
        with self.assertRaisesRegex(oidc.OidcError, "book_agent_role"):
            self._verify(self.issuer.token(book_agent_role="owner"))
        defaulted = oidc.OidcVerifier(_settings(oidc_default_role="viewer"), http_get=self.issuer.get)
        self.assertEqual(self._verify(self.issuer.token(book_agent_role=None), defaulted).role, "viewer")

    def test_keys_are_cached_and_reloaded_when_the_issuer_rotates(self) -> None:
        self._verify(self.issuer.token())
        self._verify(self.issuer.token())
        self.assertEqual(len(self.issuer.fetches), 2)  # discovery + JWKS once
        self.issuer.rotate()
        # Within the reload interval an unknown key id does not refetch.
        with self.assertRaisesRegex(oidc.OidcError, "unknown key"):
            self._verify(self.issuer.token())
        self.assertEqual(len(self.issuer.fetches), 2)
        self.now[0] += 31
        self.assertEqual(self._verify(self.issuer.token()).role, "editor")
        self.assertEqual(self.issuer.fetches[-1], f"{ISSUER}/certs")
        fetches = len(self.issuer.fetches)
        for _ in range(5):
            with self.assertRaisesRegex(oidc.OidcError, "unknown key"):
                self._verify(self.issuer.token(kid="k9"))
        self.assertEqual(len(self.issuer.fetches), fetches)
        # Keys also expire after the TTL.
        self.now[0] += 601
        self._verify(self.issuer.token())
        self.assertEqual(len(self.issuer.fetches), fetches + 1)

    def test_issuer_needs_an_audience(self) -> None:
        with self.assertRaisesRegex(AppScopeViolation, "oidc_audience"):
            validate_app_scope(_settings(oidc_audience=None))


class OidcApiTests(unittest.TestCase):
    def test_bearer_tokens_work_next_to_api_keys(self) -> None:
        from fastapi.testclient import TestClient

        from book_agent.app.main import create_app

        engine = build_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        session_factory = build_session_factory(engine=engine)
        with session_factory() as session:
            ApiKeyService(session).ensure_org("default")
            api_key = ApiKeyService(session).create(org_id=DEFAULT_ORG_ID, name="k", role="viewer").plaintext
            session.commit()
        issuer = _Issuer()
        env = {
            "BOOK_AGENT_AUTH_MODE": "api_key",
            "BOOK_AGENT_OIDC_ISSUER": ISSUER,
            "BOOK_AGENT_OIDC_AUDIENCE": AUDIENCE,
            "BOOK_AGENT_RUN_EXECUTOR_ENABLED": "false",
        }
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        get_settings.cache_clear()
        self.addCleanup(get_settings.cache_clear)
        http_patch = patch.object(oidc, "_http_get_json", side_effect=issuer.get)
        http_patch.start()
        self.addCleanup(http_patch.stop)
        # A fresh process-wide verifier picks up the patched fetcher.
        oidc._verifier = None
        self.addCleanup(setattr, oidc, "_verifier", None)
        app = create_app()
        app.state.session_factory = session_factory
        with TestClient(app) as client:
            viewer = {"Authorization": f"Bearer {issuer.token(book_agent_role='viewer')}"}
            self.assertEqual(client.get("/v1/documents/history", headers=viewer).status_code, 200)
            self.assertEqual(client.get("/v1/providers", headers=viewer).status_code, 403)
            admin = {"Authorization": f"Bearer {issuer.token(book_agent_role='admin')}"}
            self.assertEqual(client.get("/v1/providers", headers=admin).status_code, 200)
            expired = {"Authorization": f"Bearer {issuer.token(exp=int(time.time()) - 600)}"}
            response = client.get("/v1/documents/history", headers=expired)
            self.assertEqual(response.status_code, 401)
            self.assertIn("expired", response.json()["detail"])
            self.assertEqual(client.get("/v1/documents/history", headers={"Authorization": f"Bearer {api_key}"}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
