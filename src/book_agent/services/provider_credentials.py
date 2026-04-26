"""CRUD + active-resolution service for runtime translation providers.

Frontend talks to this through the ``/v1/providers`` routes; the worker
factory uses :func:`resolve_active_credential` so swapping models takes
effect on the next packet without a server restart.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.core.config import Settings
from book_agent.domain.enums import ProviderKind, ProviderTestStatus
from book_agent.domain.models.provider_credential import ProviderCredential
from book_agent.services.secrets import decrypt_secret, encrypt_secret
from book_agent.workers.providers.openai_compatible import (
    OpenAICompatibleTranslationClient,
    ProviderHTTPError,
    ProviderNetworkError,
    ProviderTransportError,
)
from book_agent.workers.translator import (
    EchoTranslationWorker,
    LLMTranslationWorker,
    TranslationWorker,
)

logger = logging.getLogger(__name__)


_NOT_FOUND_MESSAGE = "provider credential not found"


@dataclass(slots=True)
class TestOutcome:
    status: ProviderTestStatus
    message: str | None
    elapsed_ms: int | None
    sample_output: str | None


def list_credentials(session: Session) -> list[ProviderCredential]:
    rows = session.execute(
        select(ProviderCredential).order_by(
            ProviderCredential.is_active.desc(),
            ProviderCredential.updated_at.desc(),
        )
    ).scalars().all()
    return list(rows)


def get_credential(session: Session, credential_id: str) -> ProviderCredential:
    row = session.get(ProviderCredential, credential_id)
    if row is None:
        raise LookupError(_NOT_FOUND_MESSAGE)
    return row


def get_active_credential(session: Session) -> ProviderCredential | None:
    return session.execute(
        select(ProviderCredential).where(ProviderCredential.is_active.is_(True)).limit(1)
    ).scalar_one_or_none()


def create_credential(
    session: Session,
    *,
    name: str,
    provider_kind: ProviderKind,
    model_name: str,
    base_url: str,
    api_key: str | None,
    streaming: bool,
    max_output_tokens: int,
    timeout_seconds: int,
    max_retries: int,
    retry_backoff_seconds: float,
    activate: bool,
) -> ProviderCredential:
    ciphertext = encrypt_secret(api_key) if api_key else None
    record = ProviderCredential(
        name=name,
        provider_kind=provider_kind,
        model_name=model_name,
        base_url=base_url,
        api_key_ciphertext=ciphertext,
        streaming=streaming,
        max_output_tokens=max_output_tokens,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        retry_backoff_seconds_x10=int(round(retry_backoff_seconds * 10)),
        is_active=False,
    )
    session.add(record)
    session.flush()
    if activate:
        _activate_internal(session, record)
    return record


def update_credential(
    session: Session,
    credential_id: str,
    *,
    name: str | None = None,
    provider_kind: ProviderKind | None = None,
    model_name: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,  # "" clears, None leaves alone
    streaming: bool | None = None,
    max_output_tokens: int | None = None,
    timeout_seconds: int | None = None,
    max_retries: int | None = None,
    retry_backoff_seconds: float | None = None,
) -> ProviderCredential:
    record = get_credential(session, credential_id)
    if name is not None:
        record.name = name
    if provider_kind is not None:
        record.provider_kind = provider_kind
    if model_name is not None:
        record.model_name = model_name
    if base_url is not None:
        record.base_url = base_url
    if api_key is not None:
        record.api_key_ciphertext = encrypt_secret(api_key) if api_key else None
    if streaming is not None:
        record.streaming = streaming
    if max_output_tokens is not None:
        record.max_output_tokens = max_output_tokens
    if timeout_seconds is not None:
        record.timeout_seconds = timeout_seconds
    if max_retries is not None:
        record.max_retries = max_retries
    if retry_backoff_seconds is not None:
        record.retry_backoff_seconds_x10 = int(round(retry_backoff_seconds * 10))
    # Any change to the active provider's config invalidates the worker cache
    # so the next request picks the new values.
    if record.is_active:
        _bump_revision()
    session.flush()
    return record


def delete_credential(session: Session, credential_id: str) -> None:
    record = get_credential(session, credential_id)
    if record.is_active:
        raise ValueError("cannot delete the active provider; activate another one first")
    session.delete(record)
    session.flush()


def activate_credential(session: Session, credential_id: str) -> ProviderCredential:
    record = get_credential(session, credential_id)
    _activate_internal(session, record)
    return record


def _activate_internal(session: Session, record: ProviderCredential) -> None:
    # Step 1: deactivate everyone else inside the same transaction so the
    # partial unique index never sees two active rows.
    session.query(ProviderCredential).filter(
        ProviderCredential.is_active.is_(True),
        ProviderCredential.id != record.id,
    ).update({ProviderCredential.is_active: False}, synchronize_session=False)
    record.is_active = True
    session.flush()
    _bump_revision()


def record_test_outcome(
    session: Session,
    credential_id: str,
    outcome: TestOutcome,
) -> ProviderCredential:
    record = get_credential(session, credential_id)
    record.last_test_status = outcome.status
    record.last_test_at = datetime.now(timezone.utc)
    record.last_test_message = outcome.message
    session.flush()
    return record


def api_key_preview(record: ProviderCredential) -> str | None:
    if not record.api_key_ciphertext:
        return None
    plain = decrypt_secret(record.api_key_ciphertext) or ""
    if not plain:
        return None
    if len(plain) <= 6:
        return "*" * len(plain)
    return f"{plain[:3]}{'*' * 4}{plain[-4:]}"


def build_worker_from_credential(record: ProviderCredential) -> TranslationWorker:
    """Materialize a TranslationWorker from a stored credential."""
    if record.provider_kind == ProviderKind.ECHO:
        return EchoTranslationWorker(
            model_name=record.model_name,
            prompt_version="p0.echo.v1",
        )
    if record.provider_kind != ProviderKind.OPENAI_COMPATIBLE:
        raise ValueError(f"Unsupported provider_kind: {record.provider_kind}")
    api_key = decrypt_secret(record.api_key_ciphertext) or ""
    if not api_key:
        raise ValueError(
            f"Provider '{record.name}' is openai_compatible but has no API key."
        )
    client = OpenAICompatibleTranslationClient(
        api_key=api_key,
        base_url=record.base_url,
        timeout_seconds=record.timeout_seconds,
        max_retries=record.max_retries,
        retry_backoff_seconds=record.retry_backoff_seconds_x10 / 10.0,
        max_output_tokens=record.max_output_tokens,
        streaming=bool(record.streaming),
    )
    return LLMTranslationWorker(
        client,
        model_name=record.model_name,
        prompt_version="p0.openai-compatible.v1",
        prompt_profile="tech-column-meta-v1",
        runtime_config={
            "provider": "openai_compatible",
            "credential_id": record.id,
            "credential_name": record.name,
            "base_url": record.base_url,
            "streaming": bool(record.streaming),
            "timeout_seconds": record.timeout_seconds,
            "max_retries": record.max_retries,
            "retry_backoff_seconds": record.retry_backoff_seconds_x10 / 10.0,
            "max_output_tokens": record.max_output_tokens,
        },
    )


def test_credential_connection(record: ProviderCredential) -> TestOutcome:
    """Issue a tiny round-trip request to confirm the provider works."""
    started = time.perf_counter()
    if record.provider_kind == ProviderKind.ECHO:
        return TestOutcome(
            status=ProviderTestStatus.OK,
            message="echo provider — no network call performed",
            elapsed_ms=0,
            sample_output="Echo provider always succeeds.",
        )
    api_key = decrypt_secret(record.api_key_ciphertext) or ""
    if not api_key:
        return TestOutcome(
            status=ProviderTestStatus.FAILED,
            message="No API key stored for this credential.",
            elapsed_ms=0,
            sample_output=None,
        )
    client = OpenAICompatibleTranslationClient(
        api_key=api_key,
        base_url=record.base_url,
        # Cap the test request well below the configured packet timeout —
        # we want fast feedback, not a 2-minute hang for a smoke check.
        timeout_seconds=min(int(record.timeout_seconds), 60),
        max_retries=0,
        max_output_tokens=64,
        streaming=bool(record.streaming),
    )
    schema = {
        "type": "object",
        "required": ["translation"],
        "properties": {"translation": {"type": "string"}},
    }
    try:
        payload, _usage = client.generate_structured_object(
            model_name=record.model_name,
            system_prompt=(
                "You are a translation engine. Reply with a JSON object that has "
                "exactly one key 'translation' whose value is the Simplified Chinese "
                "rendering of the user's text. No other text."
            ),
            user_prompt="Translate to Chinese, return JSON {\"translation\": \"...\"}: Hello, world.",
            response_schema=schema,
            schema_name="provider_smoke_test",
        )
    except ProviderHTTPError as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        return TestOutcome(
            status=ProviderTestStatus.FAILED,
            message=f"HTTP {exc.code}: {exc.detail[:200]}",
            elapsed_ms=elapsed,
            sample_output=None,
        )
    except ProviderNetworkError as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        return TestOutcome(
            status=ProviderTestStatus.FAILED,
            message=f"Network error: {exc.reason}",
            elapsed_ms=elapsed,
            sample_output=None,
        )
    except ProviderTransportError as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        return TestOutcome(
            status=ProviderTestStatus.FAILED,
            message=f"Transport error: {exc}",
            elapsed_ms=elapsed,
            sample_output=None,
        )
    except RuntimeError as exc:
        # Network round-trip succeeded but parsing failed. Surface as "ok"
        # because connectivity/auth is fine — schema mismatch is a content
        # issue we'd see again during real packets but isn't a config bug.
        elapsed = int((time.perf_counter() - started) * 1000)
        return TestOutcome(
            status=ProviderTestStatus.OK,
            message=f"connected, response parse warning: {str(exc)[:120]}",
            elapsed_ms=elapsed,
            sample_output=None,
        )
    except Exception as exc:  # pragma: no cover - defensive: any wrapper bug
        elapsed = int((time.perf_counter() - started) * 1000)
        return TestOutcome(
            status=ProviderTestStatus.FAILED,
            message=f"{type(exc).__name__}: {str(exc)[:200]}",
            elapsed_ms=elapsed,
            sample_output=None,
        )
    elapsed = int((time.perf_counter() - started) * 1000)
    translation = payload.get("translation") if isinstance(payload, dict) else None
    sample = str(translation)[:120] if translation else None
    return TestOutcome(
        status=ProviderTestStatus.OK,
        message="connection ok",
        elapsed_ms=elapsed,
        sample_output=sample,
    )




# ---------------------------------------------------------------------------
# Worker resolution + caching
# ---------------------------------------------------------------------------

# A cheap monotonic revision counter; bumped whenever any active-affecting
# change is committed, so app-state caches can invalidate.
_revision = 0


def _bump_revision() -> None:
    global _revision
    _revision += 1


def current_revision() -> int:
    return _revision


def resolve_active_credential(
    session: Session, settings: Settings
) -> ProviderCredential | None:
    """Return the active credential, auto-migrating from .env on first run."""
    active = get_active_credential(session)
    if active is not None:
        return active
    bootstrapped = _bootstrap_from_settings(session, settings)
    if bootstrapped is not None:
        return bootstrapped
    return None


def _bootstrap_from_settings(
    session: Session, settings: Settings
) -> ProviderCredential | None:
    """Seed the table from .env on first launch so users keep working without manual setup."""
    if list_credentials(session):
        return None
    backend = (settings.translation_backend or "").lower().strip()
    if backend == "echo":
        record = create_credential(
            session,
            name="Built-in echo (offline)",
            provider_kind=ProviderKind.ECHO,
            model_name=settings.translation_model or "echo-worker",
            base_url=settings.translation_openai_base_url,
            api_key=None,
            streaming=False,
            max_output_tokens=settings.translation_max_output_tokens,
            timeout_seconds=settings.translation_timeout_seconds,
            max_retries=settings.translation_max_retries,
            retry_backoff_seconds=settings.translation_retry_backoff_seconds,
            activate=True,
        )
        return record
    if backend == "openai_compatible" and settings.translation_openai_api_key:
        record = create_credential(
            session,
            name="Imported from .env",
            provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            model_name=settings.translation_model,
            base_url=settings.translation_openai_base_url,
            api_key=settings.translation_openai_api_key,
            streaming=bool(settings.translation_openai_streaming),
            max_output_tokens=settings.translation_max_output_tokens,
            timeout_seconds=settings.translation_timeout_seconds,
            max_retries=settings.translation_max_retries,
            retry_backoff_seconds=settings.translation_retry_backoff_seconds,
            activate=True,
        )
        return record
    return None
