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
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.services.url_guard import UnsafeUrlError, ensure_public_http_url
from book_agent.core.config import AppScope, Settings, get_settings
from book_agent.domain.enums import ProviderKind, ProviderTestStatus
from book_agent.domain.models.provider_credential import ProviderCredential
from book_agent.services.secrets import decrypt_secret, encrypt_secret
from book_agent.translation.contracts import TranslationUsage
from book_agent.workers.llm_calls import CALL_KIND_PROVIDER_TEST, observed_llm_call, record_llm_usage
from book_agent.workers.providers.openai_compatible import (
    OpenAICompatibleTranslationClient,
    ProviderHTTPError,
    ProviderNetworkError,
    ProviderTransportError,
)

logger = logging.getLogger(__name__)


_NOT_FOUND_MESSAGE = "provider credential not found"


@dataclass(slots=True)
class TestOutcome:
    status: ProviderTestStatus
    message: str | None
    elapsed_ms: int | None
    sample_output: str | None
    # Set when a network round-trip happened, so the smoke test is billed like
    # every other model call once the outcome is recorded.
    usage: TranslationUsage | None = None
    error: Exception | None = None
    model_name: str | None = None


def credential_scope(org_id: str | None) -> str | None:
    """The credential scope an organisation manages: None (shared) for the default organisation."""
    from book_agent.domain.models.auth import DEFAULT_ORG_ID

    if org_id is None or str(org_id) == DEFAULT_ORG_ID:
        return None
    return str(org_id)


def _in_scope(scope: str | None):
    return ProviderCredential.org_id.is_(None) if scope is None else ProviderCredential.org_id == scope


def list_credentials(session: Session, scope: str | None = None) -> list[ProviderCredential]:
    """Credentials of one scope: shared (None) or an organisation's own."""
    rows = session.execute(
        select(ProviderCredential)
        .where(_in_scope(scope))
        .order_by(
            ProviderCredential.is_active.desc(),
            ProviderCredential.updated_at.desc(),
        )
    ).scalars().all()
    return list(rows)


def get_credential(session: Session, credential_id: str, scope: str | None | object = ...) -> ProviderCredential:
    """``scope`` given: a credential of another scope is reported as not found."""
    row = session.get(ProviderCredential, credential_id)
    if row is None or (scope is not ... and row.org_id != scope):
        raise LookupError(_NOT_FOUND_MESSAGE)
    return row


def get_active_credential(session: Session, scope: str | None = None) -> ProviderCredential | None:
    return session.execute(
        select(ProviderCredential).where(ProviderCredential.is_active.is_(True), _in_scope(scope)).limit(1)
    ).scalar_one_or_none()


def check_provider_base_url(base_url: str, provider_kind: ProviderKind) -> None:
    """With auth on or in prod, a provider may only point at a public http(s) host (SSRF guard, R1)."""
    from book_agent.core.config import get_settings

    settings = get_settings()
    if provider_kind == ProviderKind.ECHO or not settings.hardened or settings.provider_allow_private_hosts:
        return
    ensure_public_http_url(base_url)


PRICE_FIELDS = ("input_cost_per_1m_tokens", "input_cache_hit_cost_per_1m_tokens", "output_cost_per_1m_tokens")
# Request fields the client builds itself; overriding them would break prompts, tools or parsing.
RESERVED_REQUEST_FIELDS = frozenset(
    {"model", "messages", "input", "tools", "tool_choice", "stream", "response_format", "text", "max_tokens"}
)


def validate_request_overrides(overrides: dict | None) -> dict:
    if overrides is None:
        return {}
    if not isinstance(overrides, dict):
        raise ValueError("request_overrides must be a JSON object")
    reserved = sorted(RESERVED_REQUEST_FIELDS & set(overrides))
    if reserved:
        raise ValueError(f"request_overrides cannot set {', '.join(reserved)}: the client builds these fields")
    return dict(overrides)


def _set_prices(record: ProviderCredential, prices: dict[str, float | None]) -> None:
    for field_name, value in prices.items():
        if field_name not in PRICE_FIELDS:
            raise ValueError(f"unknown price field: {field_name}")
        if value is not None and value < 0:
            raise ValueError(f"{field_name} must be >= 0")
        setattr(record, field_name, None if value is None else Decimal(str(value)))


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
    scope: str | None = None,
    prices: dict[str, float | None] | None = None,
    request_overrides: dict | None = None,
) -> ProviderCredential:
    check_provider_base_url(base_url, provider_kind)
    overrides = validate_request_overrides(request_overrides)
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
        org_id=scope,
        request_overrides_json=overrides,
    )
    _set_prices(record, prices or {})
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
    prices: dict[str, float | None] | None = None,  # present keys are set; None clears
    request_overrides: dict | None = None,  # None leaves alone, {} clears
) -> ProviderCredential:
    record = get_credential(session, credential_id)
    if prices:
        _set_prices(record, prices)
    if request_overrides is not None:
        record.request_overrides_json = validate_request_overrides(request_overrides)
    if name is not None:
        record.name = name
    if provider_kind is not None:
        record.provider_kind = provider_kind
    if model_name is not None:
        record.model_name = model_name
    if base_url is not None:
        check_provider_base_url(base_url, provider_kind or record.provider_kind)
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
        _bump_revision(record)
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
    if record.provider_kind == ProviderKind.ECHO and get_settings().app_scope == AppScope.PROD:
        raise ValueError(
            "the echo provider cannot be activated in prod scope: it copies the source "
            "text instead of translating it"
        )
    # Step 1: deactivate the rest of the scope inside the same transaction so
    # the partial unique index never sees two active rows.
    session.query(ProviderCredential).filter(
        ProviderCredential.is_active.is_(True),
        ProviderCredential.id != record.id,
        _in_scope(record.org_id),
    ).update({ProviderCredential.is_active: False}, synchronize_session=False)
    record.is_active = True
    _bump_revision(record)
    session.flush()


def record_test_outcome(
    session: Session,
    credential_id: str,
    outcome: TestOutcome,
) -> ProviderCredential:
    record = get_credential(session, credential_id)
    record.last_test_status = outcome.status
    record.last_test_at = datetime.now(timezone.utc)
    record.last_test_message = outcome.message
    _record_test_call(session, record, outcome)
    session.flush()
    return record


def _record_test_call(session: Session, record: ProviderCredential, outcome: TestOutcome) -> None:
    if outcome.usage is None and outcome.error is None:
        return
    model = outcome.model_name or record.model_name
    payload = {"credential_id": record.id, "credential_name": record.name}
    from book_agent.domain.models.auth import DEFAULT_ORG_ID

    # A smoke test is spent by the scope that owns the credential.
    org_id = record.org_id or DEFAULT_ORG_ID
    if outcome.error is not None:
        try:
            with observed_llm_call(session, call_kind=CALL_KIND_PROVIDER_TEST, model=model, payload=payload, org_id=org_id):
                raise outcome.error
        except Exception:
            return
    record_llm_usage(
        session,
        call_kind=CALL_KIND_PROVIDER_TEST,
        model=model,
        usage=outcome.usage,
        payload=payload,
        org_id=org_id,
    )


def api_key_preview(record: ProviderCredential) -> str | None:
    if not record.api_key_ciphertext:
        return None
    plain = decrypt_secret(record.api_key_ciphertext) or ""
    if not plain:
        return None
    if len(plain) <= 6:
        return "*" * len(plain)
    return f"{plain[:3]}{'*' * 4}{plain[-4:]}"


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
    try:
        check_provider_base_url(record.base_url, record.provider_kind)
    except UnsafeUrlError as exc:
        return TestOutcome(status=ProviderTestStatus.FAILED, message=str(exc), elapsed_ms=0, sample_output=None)
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
        payload, usage = client.generate_structured_object(
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
            error=exc,
            model_name=record.model_name,
        )
    except ProviderNetworkError as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        return TestOutcome(
            status=ProviderTestStatus.FAILED,
            message=f"Network error: {exc.reason}",
            elapsed_ms=elapsed,
            sample_output=None,
            error=exc,
            model_name=record.model_name,
        )
    except ProviderTransportError as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        return TestOutcome(
            status=ProviderTestStatus.FAILED,
            message=f"Transport error: {exc}",
            elapsed_ms=elapsed,
            sample_output=None,
            error=exc,
            model_name=record.model_name,
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
            error=exc,
            model_name=record.model_name,
        )
    except Exception as exc:  # pragma: no cover - defensive: any wrapper bug
        elapsed = int((time.perf_counter() - started) * 1000)
        return TestOutcome(
            status=ProviderTestStatus.FAILED,
            message=f"{type(exc).__name__}: {str(exc)[:200]}",
            elapsed_ms=elapsed,
            sample_output=None,
            error=exc,
            model_name=record.model_name,
        )
    elapsed = int((time.perf_counter() - started) * 1000)
    translation = payload.get("translation") if isinstance(payload, dict) else None
    sample = str(translation)[:120] if translation else None
    return TestOutcome(
        status=ProviderTestStatus.OK,
        message="connection ok",
        elapsed_ms=elapsed,
        sample_output=sample,
        usage=usage,
        model_name=record.model_name,
    )




# ---------------------------------------------------------------------------
# Worker resolution + caching
# ---------------------------------------------------------------------------

# Two invalidation signals for worker caches. The process-local counter makes a
# change visible in this process at once (before commit, for the request that
# made it); ``config_revision`` on the active row makes it visible to every
# other process once committed (see ``active_credential_key``).
_revision = 0


def _bump_revision(record: ProviderCredential | None = None) -> None:
    global _revision
    _revision += 1
    if record is not None:
        record.config_revision = int(record.config_revision or 1) + 1


def current_revision() -> int:
    return _revision


def active_credential_key(session: Session, org_id: str | None = None) -> tuple[str, int] | None:
    """(id, config_revision) of the credential ``org_id`` would use; changes whenever any process changes it."""
    scope = credential_scope(org_id)
    scopes = [scope, None] if scope is not None else [None]
    for candidate in scopes:
        row = session.execute(
            select(ProviderCredential.id, ProviderCredential.config_revision)
            .where(ProviderCredential.is_active.is_(True), _in_scope(candidate))
            .limit(1)
        ).first()
        if row is not None:
            return (str(row[0]), int(row[1] or 1))
    return None


def resolve_active_credential(
    session: Session, settings: Settings, org_id: str | None = None
) -> ProviderCredential | None:
    """The organisation's own active credential, else the shared one (seeded from .env on first run)."""
    scope = credential_scope(org_id)
    if scope is not None:
        own = get_active_credential(session, scope)
        if own is not None:
            return own
    active = get_active_credential(session, None)
    if active is not None:
        return active
    bootstrapped = _bootstrap_from_settings(session, settings)
    if bootstrapped is not None:
        return bootstrapped
    return None


def _bootstrap_from_settings(
    session: Session, settings: Settings
) -> ProviderCredential | None:
    """Seed the shared scope from .env on first launch so users keep working without manual setup."""
    if list_credentials(session, None):
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
