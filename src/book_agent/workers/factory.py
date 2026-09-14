from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session, sessionmaker

from book_agent.core.config import Settings
from book_agent.workers.providers import OpenAICompatibleTranslationClient
from book_agent.workers.translator import EchoTranslationWorker, LLMTranslationWorker, TranslationWorker

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
) -> LLMTranslationWorker:
    # Connection parameters come from the caller (settings or a stored
    # credential); prompt profile and token prices always come from settings.
    client = OpenAICompatibleTranslationClient(
        api_key=api_key,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
        max_output_tokens=max_output_tokens,
        input_cache_hit_cost_per_1m_tokens=settings.translation_input_cache_hit_cost_per_1m_tokens,
        input_cost_per_1m_tokens=settings.translation_input_cost_per_1m_tokens,
        output_cost_per_1m_tokens=settings.translation_output_cost_per_1m_tokens,
        streaming=streaming,
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
            raise ValueError(
                "Missing OpenAI-compatible provider credentials. "
                "Set OPENAI_API_KEY in the project-root .env before using "
                "the 'openai_compatible' translation backend."
            )
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
        raise ValueError(f"Provider '{record.name}' is openai_compatible but has no API key.")
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
    )


def resolve_translation_worker(session: Session, settings: Settings) -> TranslationWorker:
    """Build the worker from the active stored credential, falling back to settings.

    Imports happen lazily to avoid an import cycle between
    workers.factory and services.provider_credentials.
    """
    from book_agent.services.provider_credentials import resolve_active_credential

    record = resolve_active_credential(session, settings)
    if record is not None:
        return build_worker_from_credential(record, settings)
    return build_translation_worker(settings)


class TranslationWorkerProvider:
    """The one place that decides which translation worker the app uses.

    The worker is built from the active provider credential and cached per
    credential revision, so activating another provider applies to the next
    request or work item without a restart.
    """

    def __init__(self, *, settings: Settings, session_factory: Callable[[], sessionmaker]) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._lock = threading.Lock()
        self._worker: TranslationWorker | None = None
        self._revision = -1

    def get(self) -> TranslationWorker:
        from book_agent.services.provider_credentials import current_revision

        revision = current_revision()
        with self._lock:
            if self._worker is not None and self._revision == revision:
                return self._worker
            with self._session_factory()() as session:
                worker = resolve_translation_worker(session, self._settings)
                # Resolving may seed a credential from settings on first use.
                session.commit()
            self._worker = worker
            self._revision = current_revision()
            return worker

    def invalidate(self) -> None:
        with self._lock:
            self._worker = None
            self._revision = -1
