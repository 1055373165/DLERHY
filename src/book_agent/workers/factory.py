from book_agent.core.config import Settings
from book_agent.workers.providers import OpenAICompatibleTranslationClient
from book_agent.workers.translator import EchoTranslationWorker, LLMTranslationWorker, TranslationWorker


def build_translation_worker(settings: Settings) -> TranslationWorker:
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
                "Set BOOK_AGENT_TRANSLATION_OPENAI_API_KEY before using "
                "the 'openai_compatible' translation backend."
            )
        client = OpenAICompatibleTranslationClient(
            api_key=settings.translation_openai_api_key,
            base_url=settings.translation_openai_base_url,
            timeout_seconds=settings.translation_timeout_seconds,
            max_retries=settings.translation_max_retries,
            retry_backoff_seconds=settings.translation_retry_backoff_seconds,
            input_cache_hit_cost_per_1m_tokens=settings.translation_input_cache_hit_cost_per_1m_tokens,
            input_cost_per_1m_tokens=settings.translation_input_cost_per_1m_tokens,
            output_cost_per_1m_tokens=settings.translation_output_cost_per_1m_tokens,
        )
        return LLMTranslationWorker(
            client,
            model_name=settings.translation_model,
            prompt_version=settings.translation_prompt_version,
            prompt_profile=settings.translation_prompt_profile,
            runtime_config={
                "provider": backend,
                "prompt_profile": settings.translation_prompt_profile,
                "base_url": settings.translation_openai_base_url,
                "timeout_seconds": settings.translation_timeout_seconds,
                "max_retries": settings.translation_max_retries,
                "retry_backoff_seconds": settings.translation_retry_backoff_seconds,
            },
        )
    raise ValueError(
        "Unsupported translation backend. "
        "Supported backends: 'echo', 'openai_compatible'."
    )
