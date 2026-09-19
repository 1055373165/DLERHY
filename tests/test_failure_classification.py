import socket
import unittest

from sqlalchemy.exc import OperationalError

from book_agent.workers.failures import FailureDisposition, classify_failure
from book_agent.workers.providers import (
    ProviderHTTPError,
    ProviderNetworkError,
    ProviderResponseFormatError,
    ProviderTransportError,
)


class FailureClassificationTests(unittest.TestCase):
    def assertDisposition(self, exc: BaseException, disposition: FailureDisposition) -> None:
        self.assertEqual(classify_failure(exc).disposition, disposition, exc)

    def test_transient_provider_failures_are_retried(self) -> None:
        for code in (408, 409, 429, 500, 502, 503, 504):
            self.assertDisposition(ProviderHTTPError(code, "busy"), FailureDisposition.RETRY)
        self.assertDisposition(ProviderNetworkError("connection reset"), FailureDisposition.RETRY)
        self.assertDisposition(
            ProviderTransportError("Provider returned non-JSON response."), FailureDisposition.RETRY
        )
        self.assertDisposition(
            ProviderResponseFormatError("Provider response did not match TranslationWorkerOutput schema."),
            FailureDisposition.RETRY,
        )
        self.assertDisposition(socket.timeout("timed out"), FailureDisposition.RETRY)
        self.assertDisposition(
            OperationalError("SELECT 1", {}, Exception("database is locked")), FailureDisposition.RETRY
        )

    def test_account_problems_pause_the_run(self) -> None:
        balance = classify_failure(ProviderHTTPError(402, "Insufficient Balance"))
        self.assertEqual(balance.disposition, FailureDisposition.PAUSE)
        self.assertEqual(balance.pause_reason, "provider.insufficient_balance")
        self.assertFalse(balance.retryable)

        auth = classify_failure(ProviderHTTPError(401, "invalid api key"))
        self.assertEqual(auth.disposition, FailureDisposition.PAUSE)
        self.assertEqual(auth.pause_reason, "provider.authentication_failed")

    def test_other_failures_are_terminal(self) -> None:
        self.assertDisposition(ProviderHTTPError(400, "bad request"), FailureDisposition.FAIL)
        self.assertDisposition(ProviderHTTPError(404, "no such model"), FailureDisposition.FAIL)
        # Classification is by type, not by message wording.
        self.assertDisposition(RuntimeError("Provider returned HTTP 429"), FailureDisposition.FAIL)
        self.assertDisposition(ValueError("unexpected"), FailureDisposition.FAIL)


if __name__ == "__main__":
    unittest.main()
