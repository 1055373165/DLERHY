import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[3]


class _SanitizedEnvSettingsSource:
    def __init__(self, delegate: Any):
        self._delegate = delegate

    def __call__(self) -> dict[str, Any]:
        payload = dict(self._delegate())
        payload.pop("translation_openai_api_key", None)
        payload.pop("BOOK_AGENT_TRANSLATION_OPENAI_API_KEY", None)
        payload.pop("OPENAI_API_KEY", None)
        return payload


class Settings(BaseSettings):
    app_name: str = "book-agent"
    app_version: str = "0.1.0"
    environment: str = "development"
    api_prefix: str = "/v1"
    log_level: str = "INFO"
    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:55432/book_agent",
    )
    docs_dir: Path = ROOT_DIR / "docs"
    export_root: Path = Path("artifacts/exports")
    runtime_bundle_root: Path = Path("artifacts/runtime-bundles")
    upload_root: Path = Path("artifacts/uploads")
    runtime_repair_transport_command: str | None = None
    runtime_repair_transport_http_url: str | None = None
    runtime_repair_transport_http_timeout_seconds: int = 60
    runtime_repair_transport_http_bearer_token: str | None = None
    cors_allow_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    translation_backend: str = "echo"
    translation_model: str = "echo-worker"
    translation_prompt_version: str = "p0.echo.v1"
    translation_prompt_profile: str = "tech-column-meta-v1"
    translation_timeout_seconds: int = 60
    translation_max_retries: int = 1
    translation_retry_backoff_seconds: float = 1.5
    translation_max_output_tokens: int = 8192
    translation_input_cache_hit_cost_per_1m_tokens: float | None = None
    translation_input_cost_per_1m_tokens: float | None = None
    translation_output_cost_per_1m_tokens: float | None = None
    translation_openai_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "BOOK_AGENT_TRANSLATION_OPENAI_API_KEY",
            "OPENAI_API_KEY",
        ),
    )
    translation_openai_base_url: str = Field(
        default="https://api.openai.com/v1/responses",
        validation_alias=AliasChoices(
            "BOOK_AGENT_TRANSLATION_OPENAI_BASE_URL",
            "OPENAI_BASE_URL",
        ),
    )

    model_config = SettingsConfigDict(
        env_prefix="BOOK_AGENT_",
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: Any,
        env_settings: Any,
        dotenv_settings: Any,
        file_secret_settings: Any,
    ) -> tuple[Any, ...]:
        return (
            init_settings,
            _SanitizedEnvSettingsSource(env_settings),
            dotenv_settings,
            file_secret_settings,
        )

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def _parse_cors_allow_origins(cls, value: Any) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            if text.startswith("["):
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
            return [item.strip() for item in text.split(",") if item.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        raise TypeError("cors_allow_origins must be a list or comma-separated string")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
