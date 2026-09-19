from __future__ import annotations

import threading
from dataclasses import dataclass
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session, sessionmaker

from book_agent.core.config import Settings
from book_agent.workers.providers import OpenAICompatibleTranslationClient, ProviderNotConfigured
from book_agent.workers.translator import (
    EchoTranslationWorker,
    LLMTranslationWorker,
    TranslationTask,
    TranslationWorker,
    TranslationWorkerMetadata,
)

if TYPE_CHECKING:
    from book_agent.domain.models.provider_credential import ProviderCredential

CREDENTIAL_ECHO_PROMPT_VERSION = "p0.echo.v1"
CREDENTIAL_LLM_PROMPT_VERSION = "p0.openai-compatible.v1"


def _openai_compatible_worker(
    settings: Settings,
    *,
    api_key: str,
    base_url: str,
    model_name: str,
    prompt_version: str,
    timeout_seconds: int,
    max_retries: int,
    retry_backoff_seconds: float,
    max_output_tokens: int,
    streaming: bool,
    runtime_config: dict[str, Any],
    prices: dict[str, float | None] | None = None,
    request_overrides: dict[str, Any] | None = None,
) -> LLMTranslationWorker:
    # Connection parameters, prices and request overrides come from the caller (a stored
    # credential) and fall back to settings; the prompt profile always comes from settings.
    prices = prices or {}

    def price(name: str) -> float | None:
        value = prices.get(name)
        return float(value) if value is not None else getattr(settings, f"translation_{name}")

    overrides = dict(request_overrides) if request_overrides else dict(settings.translation_openai_request_overrides)
    client = OpenAICompatibleTranslationClient(
        api_key=api_key,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
        max_output_tokens=max_output_tokens,
        input_cache_hit_cost_per_1m_tokens=price("input_cache_hit_cost_per_1m_tokens"),
        input_cost_per_1m_tokens=price("input_cost_per_1m_tokens"),
        output_cost_per_1m_tokens=price("output_cost_per_1m_tokens"),
        streaming=streaming,
        request_overrides=overrides,
        structured_output_mode=settings.translation_openai_structured_output_mode,
    )
    return LLMTranslationWorker(
        client,
        model_name=model_name,
        prompt_version=prompt_version,
        prompt_profile=settings.translation_prompt_profile,
        runtime_config={
            "provider": "openai_compatible",
            "prompt_profile": settings.translation_prompt_profile,
            "base_url": base_url,
            "timeout_seconds": timeout_seconds,
            "max_retries": max_retries,
            "retry_backoff_seconds": retry_backoff_seconds,
            "max_output_tokens": max_output_tokens,
            "request_overrides": overrides,
            **runtime_config,
        },
    )


def build_translation_worker(settings: Settings) -> TranslationWorker:
    """Build the worker described by settings (.env / environment)."""
    backend = settings.translation_backend.lower().strip()
    if backend == "echo":
        return EchoTranslationWorker(
            model_name=settings.translation_model,
            prompt_version=settings.translation_prompt_version,
        )
    if backend == "openai_compatible":
        if not settings.translation_openai_api_key:
            raise ProviderNotConfigured()
        return _openai_compatible_worker(
            settings,
            api_key=settings.translation_openai_api_key,
            base_url=settings.translation_openai_base_url,
            model_name=settings.translation_model,
            prompt_version=settings.translation_prompt_version,
            timeout_seconds=settings.translation_timeout_seconds,
            max_retries=settings.translation_max_retries,
            retry_backoff_seconds=settings.translation_retry_backoff_seconds,
            max_output_tokens=settings.translation_max_output_tokens,
            streaming=settings.translation_openai_streaming,
            runtime_config={},
        )
    raise ValueError(
        "Unsupported translation backend. "
        "Supported backends: 'echo', 'openai_compatible'."
    )


def build_worker_from_credential(record: ProviderCredential, settings: Settings) -> TranslationWorker:
    """Build the worker described by a stored provider credential."""
    from book_agent.domain.enums import ProviderKind
    from book_agent.services.secrets import decrypt_secret

    if record.provider_kind == ProviderKind.ECHO:
        return EchoTranslationWorker(
            model_name=record.model_name,
            prompt_version=CREDENTIAL_ECHO_PROMPT_VERSION,
        )
    if record.provider_kind != ProviderKind.OPENAI_COMPATIBLE:
        raise ValueError(f"Unsupported provider_kind: {record.provider_kind}")
    api_key = decrypt_secret(record.api_key_ciphertext) or ""
    if not api_key:
        raise ProviderNotConfigured(f"服务商「{record.name}」没有 API key：请在「服务商」页为它填入 API key。")
    return _openai_compatible_worker(
        settings,
        api_key=api_key,
        base_url=record.base_url,
        model_name=record.model_name,
        prompt_version=CREDENTIAL_LLM_PROMPT_VERSION,
        timeout_seconds=record.timeout_seconds,
        max_retries=record.max_retries,
        retry_backoff_seconds=record.retry_backoff_seconds_x10 / 10.0,
        max_output_tokens=record.max_output_tokens,
        streaming=bool(record.streaming),
        runtime_config={
            "credential_id": record.id,
            "credential_name": record.name,
            "streaming": bool(record.streaming),
        },
        prices={
            "input_cost_per_1m_tokens": record.input_cost_per_1m_tokens,
            "input_cache_hit_cost_per_1m_tokens": record.input_cache_hit_cost_per_1m_tokens,
            "output_cost_per_1m_tokens": record.output_cost_per_1m_tokens,
        },
        request_overrides=dict(record.request_overrides_json or {}),
    )


def resolve_translation_worker(session: Session, settings: Settings, org_id: str | None = None) -> TranslationWorker:
    """Build the worker from the organisation's (else the shared) active credential, falling back to settings.

    Imports happen lazily to avoid an import cycle between
    workers.factory and services.provider_credentials.
    """
    from book_agent.services.provider_credentials import resolve_active_credential

    record = resolve_active_credential(session, settings, org_id)
    try:
        if record is not None:
            return build_worker_from_credential(record, settings)
        return build_translation_worker(settings)
    except ProviderNotConfigured as exc:
        # Pages that only read the library still work; translating says what to set up.
        return UnconfiguredTranslationWorker(str(exc))


class UnconfiguredTranslationWorker:
    """Stands in until a provider is configured: every translation raises ProviderNotConfigured."""

    def __init__(self, reason: str):
        self.reason = reason
        self._metadata = TranslationWorkerMetadata(
            worker_name=self.__class__.__name__,
            model_name="unconfigured",
            prompt_version="unconfigured",
            runtime_config={},
        )

    def metadata(self) -> TranslationWorkerMetadata:
        return self._metadata

    def translate(self, task: TranslationTask):
        raise ProviderNotConfigured(self.reason)


class TranslationWorkerProvider:
    """The one place that decides which translation worker the app uses.

    Workers are built from the active provider credential of an organisation
    (its own, else the shared one) and cached per organisation scope under the
    key (process revision, credential id, credential config_revision), so
    activating or editing a provider applies to the next request or work item
    in every process without a restart. The database key is re-read at most
    every ``db_check_interval_seconds``.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: Callable[[], sessionmaker],
        db_check_interval_seconds: float = 2.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        import time

        self._settings = settings
        self._session_factory = session_factory
        self._lock = threading.Lock()
        self._entries: dict[str | None, _CachedWorker] = {}
        self._db_check_interval = max(0.0, float(db_check_interval_seconds))
        self._clock = clock or time.monotonic

    def get(self, org_id: str | None = None) -> TranslationWorker:
        from book_agent.services.provider_credentials import active_credential_key, credential_scope, current_revision

        scope = credential_scope(org_id)
        revision = current_revision()
        with self._lock:
            now = self._clock()
            entry = self._entries.get(scope)
            if entry is not None and entry.revision == revision and now - entry.checked_at < self._db_check_interval:
                return entry.worker
            with self._session_factory()() as session:
                key = active_credential_key(session, scope)
                if entry is not None and entry.revision == revision and key == entry.db_key:
                    entry.checked_at = now
                    return entry.worker
                worker = resolve_translation_worker(session, self._settings, scope)
                # Resolving may seed a credential from settings on first use.
                session.commit()
                key = active_credential_key(session, scope)
            self._entries[scope] = _CachedWorker(worker=worker, revision=current_revision(), db_key=key, checked_at=now)
            return worker

    def invalidate(self) -> None:
        with self._lock:
            self._entries.clear()


@dataclass(slots=True)
class _CachedWorker:
    worker: TranslationWorker
    revision: int
    db_key: object
    checked_at: float
