"""A connection pool whose requests keep timing out is replaced, so a wedged pool cannot stall a run."""

from __future__ import annotations

import unittest

import httpx

from book_agent.workers.failures import classify_failure
from book_agent.workers.providers.openai_compatible import (
    HttpxJSONTransport,
    ProviderHTTPError,
    ProviderNetworkError,
)


class _Clients:
    """Hands the transport a new httpx client each time it asks; every client times out until told not to."""

    def __init__(self) -> None:
        self.made = 0
        self.healthy = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if not self.healthy:
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(200, json={"ok": True})


class TransportRecycleTests(unittest.TestCase):
    def _transport(self, clients: _Clients) -> HttpxJSONTransport:
        transport = HttpxJSONTransport(client=httpx.Client(transport=httpx.MockTransport(clients.handler)))
        original = transport._http

        def http() -> httpx.Client:
            if transport._client is None:
                clients.made += 1
                transport._client = httpx.Client(transport=httpx.MockTransport(clients.handler))
            return original()

        transport._http = http  # type: ignore[method-assign]
        return transport

    def _post(self, transport: HttpxJSONTransport):
        return transport.post_json(url="https://provider.test/chat", headers={}, payload={}, timeout_seconds=5)

    def test_repeated_timeouts_open_a_fresh_pool(self) -> None:
        clients = _Clients()
        transport = self._transport(clients)
        for _ in range(HttpxJSONTransport._RECYCLE_AFTER_TIMEOUTS):
            with self.assertRaises(ProviderNetworkError):
                self._post(transport)
        self.assertEqual(transport.recycle_count, 1)

        clients.healthy = True
        self.assertEqual(self._post(transport), {"ok": True})
        self.assertEqual(clients.made, 1)

    def test_a_success_resets_the_count(self) -> None:
        clients = _Clients()
        transport = self._transport(clients)
        for _ in range(HttpxJSONTransport._RECYCLE_AFTER_TIMEOUTS - 1):
            with self.assertRaises(ProviderNetworkError):
                self._post(transport)
        clients.healthy = True
        self._post(transport)
        clients.healthy = False
        with self.assertRaises(ProviderNetworkError):
            self._post(transport)
        self.assertEqual(transport.recycle_count, 0)


class ProviderOutageClassificationTests(unittest.TestCase):
    def test_unreachable_or_overloaded_is_an_outage(self) -> None:
        self.assertTrue(classify_failure(ProviderNetworkError("timed out")).provider_outage)
        self.assertTrue(classify_failure(ProviderHTTPError(503, "busy")).provider_outage)
        self.assertTrue(classify_failure(ProviderHTTPError(429, "slow down")).provider_outage)

    def test_a_bad_request_is_not(self) -> None:
        self.assertFalse(classify_failure(ProviderHTTPError(400, "bad")).provider_outage)
        self.assertFalse(classify_failure(ValueError("x")).provider_outage)


if __name__ == "__main__":
    unittest.main()
