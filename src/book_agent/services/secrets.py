"""Symmetric encryption for stored API keys.

Design:
- Provider API keys live in DB as Fernet ciphertext (bytes).
- The Fernet key is derived from ``BOOK_AGENT_SECRET_KEY`` (urlsafe-b64,
  32 bytes after decode). If the env var is missing on first use we
  generate one, write it to the project ``.env``, and warn the user to
  back it up. Losing the key only forces the user to re-enter API keys.
- Echo provider rows have no key; encrypt/decrypt are no-ops on empty
  inputs to keep the service layer simple.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


_SECRET_ENV_VAR = "BOOK_AGENT_SECRET_KEY"
_lock = threading.Lock()
_fernet_singleton: Fernet | None = None


class SecretKeyError(RuntimeError):
    """Raised when the configured secret key cannot be loaded or used."""


def _project_dotenv_path() -> Path:
    return Path(__file__).resolve().parents[3] / ".env"


def _read_dotenv_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def _write_secret_to_dotenv(value: str) -> None:
    path = _project_dotenv_path()
    lines = _read_dotenv_lines(path)
    replaced = False
    for index, line in enumerate(lines):
        if line.startswith(f"{_SECRET_ENV_VAR}="):
            lines[index] = f"{_SECRET_ENV_VAR}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{_SECRET_ENV_VAR}={value}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _read_secret_from_dotenv() -> str | None:
    path = _project_dotenv_path()
    for line in _read_dotenv_lines(path):
        if line.startswith(f"{_SECRET_ENV_VAR}="):
            value = line.split("=", 1)[1].strip().strip("'\"")
            if value:
                return value
    return None


def _ensure_secret_key() -> str:
    raw = os.environ.get(_SECRET_ENV_VAR)
    if raw and raw.strip():
        return raw.strip()
    # Fall back to reading .env directly because pydantic-settings only
    # populates Settings, not os.environ.
    from_file = _read_secret_from_dotenv()
    if from_file:
        os.environ[_SECRET_ENV_VAR] = from_file
        return from_file
    generated = Fernet.generate_key().decode("ascii")
    os.environ[_SECRET_ENV_VAR] = generated
    try:
        _write_secret_to_dotenv(generated)
    except OSError as exc:
        logger.warning(
            "Generated %s but failed to persist to .env (%s); the key will not survive a restart.",
            _SECRET_ENV_VAR,
            exc,
        )
        return generated
    logger.warning(
        "Generated a new %s and wrote it to project .env. Back this value up — losing it makes "
        "any stored provider API keys unrecoverable.",
        _SECRET_ENV_VAR,
    )
    return generated


def get_fernet() -> Fernet:
    global _fernet_singleton
    with _lock:
        if _fernet_singleton is None:
            key = _ensure_secret_key()
            try:
                _fernet_singleton = Fernet(key.encode("ascii") if isinstance(key, str) else key)
            except (ValueError, TypeError) as exc:
                raise SecretKeyError(
                    f"{_SECRET_ENV_VAR} must be a urlsafe base64-encoded 32-byte Fernet key."
                ) from exc
        return _fernet_singleton


def encrypt_secret(plaintext: str | None) -> bytes | None:
    if plaintext is None or plaintext == "":
        return None
    fernet = get_fernet()
    return fernet.encrypt(plaintext.encode("utf-8"))


def decrypt_secret(ciphertext: bytes | None) -> str | None:
    if not ciphertext:
        return None
    fernet = get_fernet()
    try:
        return fernet.decrypt(ciphertext).decode("utf-8")
    except InvalidToken as exc:
        raise SecretKeyError(
            "Failed to decrypt provider credential — the secret key may have been rotated. "
            "Re-enter the affected API keys via the providers settings page."
        ) from exc


def reset_fernet_cache_for_tests() -> None:
    """Drop the cached Fernet so tests can swap the secret env var freely."""
    global _fernet_singleton
    with _lock:
        _fernet_singleton = None
