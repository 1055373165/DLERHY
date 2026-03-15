from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "book-agent"
    app_version: str = "0.1.0"
    environment: str = "development"
    api_prefix: str = "/v1"
    log_level: str = "INFO"
    database_url: str = Field(
        default="sqlite+pysqlite:///./artifacts/book-agent.db",
    )
    docs_dir: Path = Path("/Users/smy/project/book-agent/docs")
    export_root: Path = Path("artifacts/exports")
    upload_root: Path = Path("artifacts/uploads")
    translation_backend: str = "echo"
    translation_model: str = "echo-worker"
    translation_prompt_version: str = "p0.echo.v1"
    translation_timeout_seconds: int = 60
    translation_max_retries: int = 1
    translation_retry_backoff_seconds: float = 1.5
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
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
