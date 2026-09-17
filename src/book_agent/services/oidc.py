"""OIDC bearer tokens: verify a JWT from the configured issuer and map it to an organisation and role.

The API does not run a login flow. A gateway (for example oauth2-proxy with
``--pass-authorization-header``) or a client that already holds an ID or
access token sends it as ``Authorization: Bearer <jwt>``. The token must be
signed by a key in the issuer's JWKS (RS/ES/PS algorithms only), be unexpired
and carry the configured audience and issuer. The organisation claim names an
existing organisation (created with ``book-agent create-api-key --org`` or
the API); tokens for unknown organisations are refused rather than creating
one.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.core.config import Settings
from book_agent.domain.models.auth import Org

ALLOWED_ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256", "PS384", "PS512"]
ROLES = ("viewer", "editor", "admin")


class OidcError(Exception):
    """The token is not acceptable; the message is safe to return to the caller."""


@dataclass(frozen=True, slots=True)
class OidcIdentity:
    subject: str
    org_id: str
    role: str


def looks_like_jwt(token: str) -> bool:
    return token.count(".") == 2 and not token.startswith("bak_")


class OidcVerifier:
    def __init__(
        self,
        settings: Settings,
        *,
        http_get=None,
        jwks_ttl_seconds: float = 600.0,
        min_reload_seconds: float = 30.0,
        clock=None,
    ) -> None:
        self.issuer = (settings.oidc_issuer or "").rstrip("/")
        self.audience = settings.oidc_audience
        self.jwks_url = settings.oidc_jwks_url
        self.org_claim = settings.oidc_org_claim
        self.role_claim = settings.oidc_role_claim
        self.default_role = settings.oidc_default_role
        self._http_get = http_get or _http_get_json
        self._jwks_ttl = jwks_ttl_seconds
        # Tokens with made-up key ids must not turn every request into a JWKS fetch.
        self._min_reload = min_reload_seconds
        self._clock = clock or time.monotonic
        self._keys: dict[str, Any] = {}
        self._keys_loaded_at: float | None = None
        self._lock = threading.Lock()

    # --- keys ------------------------------------------------------------------------

    def _discover_jwks_url(self) -> str:
        if self.jwks_url:
            return self.jwks_url
        document = self._http_get(f"{self.issuer}/.well-known/openid-configuration")
        url = document.get("jwks_uri") if isinstance(document, dict) else None
        if not url:
            raise OidcError("the issuer's discovery document has no jwks_uri")
        self.jwks_url = str(url)
        return self.jwks_url

    def _load_keys(self) -> None:
        jwks = self._http_get(self._discover_jwks_url())
        keys: dict[str, Any] = {}
        for entry in (jwks or {}).get("keys", []):
            try:
                key = jwt.PyJWK.from_dict(entry)
            except (jwt.PyJWKError, jwt.InvalidKeyError):
                continue
            keys[str(entry.get("kid") or "")] = key
        self._keys = keys
        self._keys_loaded_at = self._clock()

    def _key_for(self, kid: str) -> Any:
        with self._lock:
            age = None if self._keys_loaded_at is None else self._clock() - self._keys_loaded_at
            if age is None or age > self._jwks_ttl or (kid not in self._keys and age >= self._min_reload):
                # Unknown kid: the issuer may have rotated keys; reload, at most once per min_reload.
                self._load_keys()
            key = self._keys.get(kid)
        if key is None:
            raise OidcError("token signed with an unknown key")
        return key

    # --- verification ----------------------------------------------------------------

    def verify(self, session: Session, token: str) -> OidcIdentity:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise OidcError("malformed token") from exc
        if header.get("alg") not in ALLOWED_ALGORITHMS:
            raise OidcError("token algorithm not allowed")
        key = self._key_for(str(header.get("kid") or ""))
        try:
            claims = jwt.decode(
                token,
                key=key.key,
                algorithms=ALLOWED_ALGORITHMS,
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
                leeway=30,
            )
        except jwt.PyJWTError as exc:
            raise OidcError(f"invalid token: {exc}") from exc
        role = claims.get(self.role_claim) or self.default_role
        if isinstance(role, list):
            # Several roles: the strongest known one wins.
            known = [item for item in role if item in ROLES]
            role = max(known, key=ROLES.index) if known else None
        if role not in ROLES:
            raise OidcError(f"token has no usable {self.role_claim} claim")
        org_name = claims.get(self.org_claim)
        if not isinstance(org_name, str) or not org_name.strip():
            raise OidcError(f"token has no {self.org_claim} claim")
        org = session.scalar(select(Org).where(Org.name == org_name.strip()))
        if org is None:
            raise OidcError("unknown organisation")
        return OidcIdentity(subject=str(claims["sub"]), org_id=str(org.id), role=str(role))


def _http_get_json(url: str) -> dict[str, Any]:
    try:
        response = httpx.get(url, timeout=10.0, follow_redirects=False)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OidcError("could not load the issuer's keys") from exc


_verifier: OidcVerifier | None = None
_verifier_key: tuple[Any, ...] | None = None
_verifier_lock = threading.Lock()


def verifier_for(settings: Settings) -> OidcVerifier | None:
    """A process-wide verifier (it caches the JWKS), rebuilt when the OIDC settings change."""
    global _verifier, _verifier_key
    if not settings.oidc_issuer:
        return None
    key = (
        settings.oidc_issuer,
        settings.oidc_audience,
        settings.oidc_jwks_url,
        settings.oidc_org_claim,
        settings.oidc_role_claim,
        settings.oidc_default_role,
    )
    with _verifier_lock:
        if _verifier is None or _verifier_key != key:
            _verifier = OidcVerifier(settings)
            _verifier_key = key
        return _verifier
