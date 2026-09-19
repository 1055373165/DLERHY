"""Retry policy, deadline and usage accounting of the shared provider client."""

from __future__ import annotations

import unittest

import httpx

from book_agent.workers.failures import FailureDisposition, classify_failure
from book_agent.workers.providers.openai_compatible import (
    RETRYABLE_HTTP_CODES,
    HttpxJSONTransport,
    OpenAICompatibleTranslationClient,
    ProviderDeadlineExceeded,
    ProviderHTTPError,
    ProviderNetworkError,
    is_retryable_http_status,
    parse_retry_after,
)

_OK_BODY = (
    '{"id":"resp_1","usage":{"prompt_tokens":100,"completion_tokens":10,"total_tokens":110},'
    '"choices":[{"message":{"content":"{\\"answer\\": 1}"}}]}'
)


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _client(handler, **overrides) -> tuple[OpenAICompatibleTranslationClient, _FakeClock]:
    clock = _FakeClock()
    transport = HttpxJSONTransport(client=httpx.Client(transport=httpx.MockTransport(handler)))
    params = dict(
        api_key="k",
        base_url="https://provider.example/v1",
        timeout_seconds=10,
        max_retries=3,
        retry_backoff_seconds=1.0,
        max_retry_backoff_seconds=8.0,
        transport=transport,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        random_fraction=lambda: 1.0,
    )
    params.update(overrides)
    return OpenAICompatibleTranslationClient(**params), clock


def _structured(client: OpenAICompatibleTranslationClient):
    return client.generate_structured_object(
        model_name="m",
        system_prompt="s",
        user_prompt="u",
        response_schema={"type": "object"},
    )


class RetryPolicyTests(unittest.TestCase):
    def test_retryable_status_set_is_shared_with_failure_classifier(self) -> None:
        for code in sorted(RETRYABLE_HTTP_CODES) + [500, 502, 503, 599]:
            self.assertTrue(is_retryable_http_status(code), code)
            self.assertEqual(
                classify_failure(ProviderHTTPError(code, "x")).disposition, FailureDisposition.RETRY, code
            )
        for code in (400, 404, 422):
            self.assertFalse(is_retryable_http_status(code), code)
            self.assertEqual(classify_failure(ProviderHTTPError(code, "x")).disposition, FailureDisposition.FAIL)

    def test_backoff_is_capped_exponential_with_full_jitter(self) -> None:
        client, _clock = _client(lambda request: httpx.Response(200, content=_OK_BODY), random_fraction=lambda: 0.5)
        self.assertEqual(client.backoff_seconds(1, jitter=False), 1.0)
        self.assertEqual(client.backoff_seconds(3, jitter=False), 4.0)
        self.assertEqual(client.backoff_seconds(6, jitter=False), 8.0)  # capped
        self.assertEqual(client.backoff_seconds(3), 2.0)  # jitter fraction 0.5

    def test_retry_after_header_extends_the_backoff(self) -> None:
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            if len(calls) == 1:
                return httpx.Response(429, headers={"Retry-After": "5"}, content=b"slow down")
            return httpx.Response(200, content=_OK_BODY)

        client, clock = _client(handler)
        payload, usage = _structured(client)
        self.assertEqual(payload, {"answer": 1})
        self.assertEqual(clock.sleeps, [5.0])
        self.assertEqual(usage.token_in, 100)

    def test_retry_after_is_bounded_by_the_backoff_cap(self) -> None:
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            if len(calls) == 1:
                return httpx.Response(503, headers={"Retry-After": "600"}, content=b"maintenance")
            return httpx.Response(200, content=_OK_BODY)

        client, clock = _client(handler, deadline_seconds=100)
        _structured(client)
        self.assertEqual(clock.sleeps, [8.0])

    def test_deadline_stops_retrying_before_sleeping(self) -> None:
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(502, content=b"bad gateway")

        client, clock = _client(handler, deadline_seconds=2.5)
        with self.assertRaises(ProviderDeadlineExceeded) as ctx:
            _structured(client)
        # attempt 1 fails -> sleep 1s (ok, 1.5s left) -> attempt 2 fails -> next
        # sleep would be 2s > 1.5s remaining -> stop without sleeping again.
        self.assertEqual(len(calls), 2)
        self.assertEqual(clock.sleeps, [1.0])
        self.assertIsInstance(ctx.exception.__cause__, ProviderHTTPError)

    def test_non_retryable_http_error_is_raised_immediately(self) -> None:
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(400, content=b"bad request")

        client, clock = _client(handler)
        with self.assertRaises(ProviderHTTPError) as ctx:
            _structured(client)
        self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(len(calls), 1)
        self.assertEqual(clock.sleeps, [])

    def test_network_errors_map_to_provider_network_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        client, _clock = _client(handler, max_retries=0)
        with self.assertRaises(ProviderNetworkError):
            _structured(client)

    def test_default_deadline_covers_every_attempt_and_backoff(self) -> None:
        client, _clock = _client(lambda request: httpx.Response(200, content=_OK_BODY))
        # 4 attempts x 10s + backoffs 1 + 2 + 4
        self.assertEqual(client.effective_deadline_seconds(), 47.0)

    def test_parse_retry_after_accepts_seconds_and_http_dates(self) -> None:
        self.assertEqual(parse_retry_after("7"), 7.0)
        self.assertEqual(parse_retry_after(" 0 "), 0.0)
        self.assertIsNone(parse_retry_after(None))
        self.assertIsNone(parse_retry_after("soon"))
        from datetime import datetime, timedelta, timezone

        now = datetime(2026, 9, 17, 12, 0, 0, tzinfo=timezone.utc)
        later = (now + timedelta(seconds=30)).strftime("%a, %d %b %Y %H:%M:%S GMT")
        self.assertEqual(parse_retry_after(later, now=now), 30.0)


class UsageAccountingTests(unittest.TestCase):
    def _priced_client(self, usage: dict) -> OpenAICompatibleTranslationClient:
        body = '{"id":"r","usage":%s,"choices":[{"message":{"content":"{\\"answer\\": 1}"}}]}' % (
            __import__("json").dumps(usage)
        )
        client, _clock = _client(
            lambda request: httpx.Response(200, content=body.encode("utf-8")),
            input_cost_per_1m_tokens=1.0,
            input_cache_hit_cost_per_1m_tokens=0.1,
            output_cost_per_1m_tokens=2.0,
        )
        return client

    def test_deepseek_cache_fields(self) -> None:
        client = self._priced_client(
            {"prompt_tokens": 1_000_000, "completion_tokens": 0, "prompt_cache_hit_tokens": 750_000, "prompt_cache_miss_tokens": 250_000}
        )
        _payload, usage = _structured(client)
        self.assertAlmostEqual(usage.cost_usd, 0.075 + 0.25)

    def test_openai_cached_tokens_details(self) -> None:
        client = self._priced_client(
            {"prompt_tokens": 1_000_000, "completion_tokens": 0, "prompt_tokens_details": {"cached_tokens": 600_000}}
        )
        _payload, usage = _structured(client)
        self.assertAlmostEqual(usage.cost_usd, 0.06 + 0.4)

    def test_anthropic_style_cache_read_is_a_hit_and_creation_is_a_miss(self) -> None:
        client = self._priced_client(
            {
                "prompt_tokens": 1_000_000,
                "completion_tokens": 0,
                "cache_read_input_tokens": 900_000,
                "cache_creation_input_tokens": 100_000,
            }
        )
        _payload, usage = _structured(client)
        self.assertAlmostEqual(usage.cost_usd, 0.09 + 0.1)

    def test_no_cache_fields_bills_everything_at_the_miss_price(self) -> None:
        client = self._priced_client({"prompt_tokens": 1_000_000, "completion_tokens": 500_000})
        _payload, usage = _structured(client)
        self.assertAlmostEqual(usage.cost_usd, 1.0 + 1.0)


if __name__ == "__main__":
    unittest.main()
