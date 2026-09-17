"""Classify work-item failures by exception type.

Replaces matching substrings of exception messages, which silently broke
whenever a provider or client reworded an error.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from book_agent.workers.providers.openai_compatible import (
    ProviderHTTPError,
    ProviderResponseFormatError,
    ProviderTransportError,
    is_retryable_http_status,
)


class FailureDisposition(StrEnum):
    RETRY = "retry"
    PAUSE = "pause"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class FailureClassification:
    disposition: FailureDisposition
    reason: str
    pause_reason: str | None = None

    @property
    def retryable(self) -> bool:
        return self.disposition == FailureDisposition.RETRY


def classify_failure(exc: BaseException) -> FailureClassification:
    if isinstance(exc, ProviderHTTPError):
        return _classify_http_error(exc)
    if isinstance(exc, ProviderResponseFormatError):
        return FailureClassification(FailureDisposition.RETRY, "provider.malformed_response")
    if isinstance(exc, ProviderTransportError):
        return FailureClassification(FailureDisposition.RETRY, "provider.transport")
    if isinstance(exc, OperationalError):
        return FailureClassification(FailureDisposition.RETRY, "database.operational")
    if isinstance(exc, SQLAlchemyTimeoutError):
        # Connection pool exhausted: a capacity problem, not a bad work item.
        return FailureClassification(FailureDisposition.RETRY, "database.pool_timeout")
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return FailureClassification(FailureDisposition.RETRY, "network")
    return FailureClassification(FailureDisposition.FAIL, "unclassified")


def _classify_http_error(exc: ProviderHTTPError) -> FailureClassification:
    code = exc.code
    if code == 402:
        # Retrying cannot succeed until the account is topped up; pause so the
        # run resumes where it stopped.
        return FailureClassification(
            FailureDisposition.PAUSE,
            "provider.http_402",
            pause_reason="provider.insufficient_balance",
        )
    if code in {401, 403}:
        return FailureClassification(
            FailureDisposition.PAUSE,
            f"provider.http_{code}",
            pause_reason="provider.authentication_failed",
        )
    if is_retryable_http_status(code):
        return FailureClassification(FailureDisposition.RETRY, f"provider.http_{code}")
    return FailureClassification(FailureDisposition.FAIL, f"provider.http_{code}")
