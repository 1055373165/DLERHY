from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from book_agent.domain.enums import ProviderKind, ProviderTestStatus


class ProviderCredentialBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    provider_kind: ProviderKind
    model_name: str = Field(..., min_length=1, max_length=200)
    base_url: str = Field(..., min_length=1, max_length=500)
    streaming: bool = False
    max_output_tokens: int = Field(8192, ge=1, le=131072)
    timeout_seconds: int = Field(120, ge=1, le=3600)
    max_retries: int = Field(2, ge=0, le=10)
    retry_backoff_seconds: float = Field(2.0, ge=0.0, le=60.0)


class ProviderCredentialCreate(ProviderCredentialBase):
    api_key: str | None = Field(default=None, max_length=500)
    activate: bool = False


class ProviderCredentialUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    provider_kind: ProviderKind | None = None
    model_name: str | None = Field(default=None, min_length=1, max_length=200)
    base_url: str | None = Field(default=None, min_length=1, max_length=500)
    api_key: str | None = Field(default=None, max_length=500)
    # Allow callers to clear the stored key (e.g. switching to echo) by passing
    # an empty string; ``None`` means "leave the existing value alone".
    streaming: bool | None = None
    max_output_tokens: int | None = Field(default=None, ge=1, le=131072)
    timeout_seconds: int | None = Field(default=None, ge=1, le=3600)
    max_retries: int | None = Field(default=None, ge=0, le=10)
    retry_backoff_seconds: float | None = Field(default=None, ge=0.0, le=60.0)


class ProviderCredentialRead(ProviderCredentialBase):
    """Public view; never includes the plaintext key."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    is_active: bool
    api_key_preview: str | None = Field(
        default=None,
        description="Masked summary of the stored API key, e.g. 'sk-****-1234'. None when no key is stored.",
    )
    last_test_status: ProviderTestStatus
    last_test_at: datetime | None = None
    last_test_message: str | None = None
    created_at: datetime
    updated_at: datetime


class ProviderTestResult(BaseModel):
    status: ProviderTestStatus
    message: str | None = None
    elapsed_ms: int | None = None
    sample_output: str | None = Field(
        default=None,
        description="Tiny sample translation produced by the provider during the connection test.",
    )
