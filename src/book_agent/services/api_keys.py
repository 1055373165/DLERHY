"""API keys: creation (plaintext shown once), hashing, authentication, revocation."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.domain.models.auth import API_KEY_ROLES, DEFAULT_ORG_ID, ApiKey, Org

KEY_PREFIX = "bak_"
# last_used_at is a hint for operators; do not write it on every request.
LAST_USED_WRITE_INTERVAL = timedelta(minutes=5)


def hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CreatedKey:
    key: ApiKey
    plaintext: str


class ApiKeyService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def ensure_org(self, name: str) -> Org:
        org = self.session.scalar(select(Org).where(Org.name == name))
        if org is None:
            org = Org(name=name) if name != "default" else Org(id=DEFAULT_ORG_ID, name=name)
            self.session.add(org)
            self.session.flush()
        return org

    def create(self, *, org_id: str, name: str, role: str) -> CreatedKey:
        if role not in API_KEY_ROLES:
            raise ValueError(f"role must be one of {', '.join(API_KEY_ROLES)}")
        if not name.strip():
            raise ValueError("name is required")
        plaintext = KEY_PREFIX + secrets.token_urlsafe(32)
        key = ApiKey(org_id=org_id, name=name.strip(), key_prefix=plaintext[:12], key_hash=hash_key(plaintext), role=role)
        self.session.add(key)
        self.session.flush()
        return CreatedKey(key=key, plaintext=plaintext)

    def authenticate(self, plaintext: str) -> ApiKey | None:
        if not plaintext or not plaintext.startswith(KEY_PREFIX):
            return None
        key = self.session.scalar(select(ApiKey).where(ApiKey.key_hash == hash_key(plaintext)))
        if key is None or key.revoked_at is not None:
            return None
        now = datetime.now(timezone.utc)
        last = key.last_used_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if last is None or now - last > LAST_USED_WRITE_INTERVAL:
            key.last_used_at = now
            self.session.flush()
        return key

    def list(self, org_id: str) -> list[ApiKey]:
        return list(self.session.scalars(select(ApiKey).where(ApiKey.org_id == org_id).order_by(ApiKey.created_at)).all())

    def revoke(self, *, org_id: str, key_id: str) -> ApiKey:
        key = self.session.get(ApiKey, key_id)
        if key is None or key.org_id != org_id:
            raise LookupError("api key not found")
        if key.revoked_at is None:
            key.revoked_at = datetime.now(timezone.utc)
            self.session.flush()
        return key
