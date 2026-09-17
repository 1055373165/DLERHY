"""OpenAI-compatible provider client.

One client serves every model call in the system (packet translation and the
structured-object calls made by terminology, concept resolution and provider
smoke tests). It owns transport, retries and usage accounting so those
concerns are implemented once:

* ``HttpxJSONTransport`` keeps a pooled ``httpx.Client`` (keep-alive across
  the 8 parallel translation threads) and maps every failure to one of the
  ``Provider*Error`` types below.
* ``_request_with_retries`` retries only the HTTP codes in
  :data:`RETRYABLE_HTTP_CODES` (shared with ``workers.failures`` so the
  in-client and work-item retry policies agree), backs off exponentially with
  full jitter under a cap, honours ``Retry-After`` and never runs past the
  per-call deadline.
* ``_extract_usage`` normalises provider usage payloads, including prompt
  cache accounting, into :class:`TranslationUsage`.
"""

from __future__ import annotations

import json
import random
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx

from book_agent.translation.contracts import (
    TranslationUsage,
    TranslationWorkerOutput,
    TranslationWorkerResult,
)
from book_agent.workers.translator import TranslationModelClient, TranslationPromptRequest

# HTTP statuses worth another attempt: request timeout, conflict, too early,
# rate limit and every server-side error. Both the client retry loop and the
# work-item failure classifier use this set.
RETRYABLE_HTTP_CODES: frozenset[int] = frozenset({408, 409, 425, 429})

DEFAULT_MAX_RETRY_BACKOFF_SECONDS = 30.0


def is_retryable_http_status(code: int) -> bool:
    return code in RETRYABLE_HTTP_CODES or 500 <= code <= 599


class ProviderTransportError(RuntimeError):
    pass


class ProviderHTTPError(ProviderTransportError):
    def __init__(self, code: int, detail: str, *, retry_after_seconds: float | None = None) -> None:
        self.code = code
        self.detail = detail
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Provider returned HTTP {code}: {detail}")


class ProviderResponseFormatError(RuntimeError):
    """The provider answered, but not with the structured payload we asked for."""


class ProviderNetworkError(ProviderTransportError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Provider request failed: {reason}")


class ProviderDeadlineExceeded(ProviderTransportError):
    """The per-call deadline ran out before a retry could be attempted."""


class JSONTransport(Protocol):
    def post_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        ...

    def post_stream_chat_completion(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        ...


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Seconds to wait from a ``Retry-After`` header (delay-seconds or HTTP-date)."""
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        try:
            when = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        seconds = (when - current).total_seconds()
    return max(0.0, seconds)


class HttpxJSONTransport:
    """Pooled HTTP transport shared by every client in the process.

    ``httpx.Client`` is thread-safe for concurrent requests, so one instance
    carries all translation threads; ``timeout_seconds`` bounds connect, read,
    write and pool acquisition for each request.
    """

    _CONNECT_TIMEOUT_SECONDS = 10.0

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        max_connections: int = 32,
        max_keepalive_connections: int = 16,
    ) -> None:
        self._client = client
        self._lock = threading.Lock()
        self._limits = httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive_connections,
        )

    def _http(self) -> httpx.Client:
        if self._client is None:
            with self._lock:
                if self._client is None:
                    self._client = httpx.Client(limits=self._limits, follow_redirects=False)
        return self._client

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None

    def _timeout(self, timeout_seconds: int) -> httpx.Timeout:
        total = max(1.0, float(timeout_seconds))
        return httpx.Timeout(
            connect=min(self._CONNECT_TIMEOUT_SECONDS, total),
            read=total,
            write=total,
            pool=total,
        )

    def post_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        try:
            response = self._http().post(
                url,
                content=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", **headers},
                timeout=self._timeout(timeout_seconds),
            )
            raw = response.text
        except httpx.RemoteProtocolError as exc:
            raise ProviderTransportError(
                "Provider disconnected before sending a complete response."
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderNetworkError(f"timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderNetworkError(str(exc) or type(exc).__name__) from exc
        if response.status_code >= 400:
            raise ProviderHTTPError(
                response.status_code,
                raw or response.reason_phrase,
                retry_after_seconds=parse_retry_after(response.headers.get("retry-after")),
            )
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderTransportError("Provider returned non-JSON response.") from exc

    def post_stream_chat_completion(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        # Some providers (e.g. NVIDIA NIM serving deepseek-v3.1) only respond
        # reliably with stream=true. We POST as SSE, accumulate delta.content
        # chunks, and reassemble a synthetic non-streaming chat-completion
        # response so the rest of the client can consume it normally.
        streaming_payload = dict(payload)
        streaming_payload["stream"] = True
        accumulated_content: list[str] = []
        accumulated_reasoning: list[str] = []
        finish_reason: str | None = None
        usage_payload: dict[str, Any] = {}
        last_id: str | None = None
        last_model: str | None = None
        try:
            with self._http().stream(
                "POST",
                url,
                content=json.dumps(streaming_payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "Accept": "text/event-stream", **headers},
                timeout=self._timeout(timeout_seconds),
            ) as response:
                if response.status_code >= 400:
                    detail = response.read().decode("utf-8", errors="replace")
                    raise ProviderHTTPError(
                        response.status_code,
                        detail or response.reason_phrase,
                        retry_after_seconds=parse_retry_after(response.headers.get("retry-after")),
                    )
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data_chunk = line[len("data:") :].strip()
                    if not data_chunk or data_chunk == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data_chunk)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(chunk, dict):
                        continue
                    if isinstance(chunk.get("id"), str):
                        last_id = chunk["id"]
                    if isinstance(chunk.get("model"), str):
                        last_model = chunk["model"]
                    choices = chunk.get("choices")
                    if isinstance(choices, list):
                        for choice in choices:
                            if not isinstance(choice, dict):
                                continue
                            delta = choice.get("delta")
                            if isinstance(delta, dict):
                                text = delta.get("content")
                                if isinstance(text, str):
                                    accumulated_content.append(text)
                                reasoning = delta.get("reasoning_content")
                                if isinstance(reasoning, str):
                                    accumulated_reasoning.append(reasoning)
                            chunk_finish = choice.get("finish_reason")
                            if isinstance(chunk_finish, str):
                                finish_reason = chunk_finish
                    chunk_usage = chunk.get("usage")
                    if isinstance(chunk_usage, dict):
                        usage_payload = chunk_usage
        except httpx.RemoteProtocolError as exc:
            raise ProviderTransportError(
                "Provider disconnected before sending a complete response."
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderNetworkError(f"timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderNetworkError(str(exc) or type(exc).__name__) from exc

        full_content = "".join(accumulated_content)
        if not full_content and not usage_payload:
            raise ProviderTransportError("Streaming provider returned no content.")
        synthetic_response: dict[str, Any] = {
            "id": last_id,
            "model": last_model,
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": full_content,
                    },
                    "finish_reason": finish_reason,
                }
            ],
        }
        if accumulated_reasoning:
            synthetic_response["choices"][0]["message"]["reasoning_content"] = "".join(
                accumulated_reasoning
            )
        if usage_payload:
            synthetic_response["usage"] = usage_payload
        return synthetic_response


_SHARED_TRANSPORT = HttpxJSONTransport()


def shared_transport() -> HttpxJSONTransport:
    """The process-wide pooled transport used by every client by default."""
    return _SHARED_TRANSPORT


@dataclass(slots=True)
class OpenAICompatibleTranslationClient(TranslationModelClient):
    api_key: str
    base_url: str = "https://api.openai.com/v1/responses"
    timeout_seconds: int = 60
    max_retries: int = 0
    retry_backoff_seconds: float = 1.0
    max_retry_backoff_seconds: float = DEFAULT_MAX_RETRY_BACKOFF_SECONDS
    # Wall-clock budget for one logical call including retries and backoff.
    # ``None`` derives it from timeout_seconds, max_retries and the backoff cap.
    deadline_seconds: float | None = None
    max_output_tokens: int | None = 8192
    input_cache_hit_cost_per_1m_tokens: float | None = None
    input_cost_per_1m_tokens: float | None = None
    output_cost_per_1m_tokens: float | None = None
    transport: JSONTransport = field(default_factory=shared_transport)
    extra_headers: dict[str, str] = field(default_factory=dict)
    # Some OpenAI-compatible endpoints (notably NVIDIA NIM serving
    # deepseek-v3.1-terminus) only respond reliably to streaming requests;
    # non-streaming POSTs hang past timeout. Enable this flag to send
    # stream=true via SSE and reassemble client-side. The flag is opt-in
    # because OpenAI's own /v1/responses endpoint is fine without it.
    streaming: bool = False
    # Provider-specific top-level request fields merged into every payload,
    # e.g. {"thinking": {"type": "disabled"}} to turn off DeepSeek reasoning.
    request_overrides: dict[str, Any] = field(default_factory=dict)
    # Injection points for tests; production uses the real clock.
    sleep: Callable[[float], None] = field(default=time.sleep)
    monotonic: Callable[[], float] = field(default=time.monotonic)
    random_fraction: Callable[[], float] = field(default=random.random)

    def generate_translation(self, request: TranslationPromptRequest) -> TranslationWorkerResult:
        endpoint_url, api_mode = self._resolve_endpoint()
        request_started_at = time.perf_counter()
        response = self._request_with_retries(
            url=endpoint_url,
            payload={**self._build_payload(request, api_mode=api_mode), **self.request_overrides},
        )
        latency_ms = max(1, round((time.perf_counter() - request_started_at) * 1000))
        output_payload = self._extract_output_payload(response, api_mode=api_mode)
        output_payload = self._normalize_translation_payload(output_payload)
        try:
            output = TranslationWorkerOutput.model_validate(output_payload)
        except Exception as exc:
            raise ProviderResponseFormatError("Provider response did not match TranslationWorkerOutput schema.") from exc
        return TranslationWorkerResult(
            output=output,
            usage=self._extract_usage(response, api_mode=api_mode, latency_ms=latency_ms),
        )

    def generate_structured_object(
        self,
        *,
        model_name: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
        schema_name: str = "structured_output",
    ) -> tuple[dict[str, Any], TranslationUsage]:
        endpoint_url, api_mode = self._resolve_endpoint()
        request_started_at = time.perf_counter()
        response = self._request_with_retries(
            url=endpoint_url,
            payload={
                **self._build_structured_payload(
                    model_name=model_name,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    response_schema=response_schema,
                    schema_name=schema_name,
                    api_mode=api_mode,
                ),
                **self.request_overrides,
            },
        )
        latency_ms = max(1, round((time.perf_counter() - request_started_at) * 1000))
        payload = self._extract_generic_output_payload(response, api_mode=api_mode)
        usage = self._extract_usage(response, api_mode=api_mode, latency_ms=latency_ms)
        return payload, usage

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            **self.extra_headers,
        }

    def _resolve_endpoint(self) -> tuple[str, str]:
        normalized = self.base_url.rstrip("/")
        if normalized.endswith("/responses"):
            return normalized, "responses"
        if normalized.endswith("/chat/completions"):
            return normalized, "chat_completions"
        if normalized.endswith("/v1"):
            return f"{normalized}/chat/completions", "chat_completions"
        return f"{normalized}/chat/completions", "chat_completions"

    # --- retry policy -------------------------------------------------------

    def effective_deadline_seconds(self) -> float:
        if self.deadline_seconds is not None:
            return float(self.deadline_seconds)
        attempts = max(0, int(self.max_retries)) + 1
        backoff_budget = sum(self.backoff_seconds(attempt, jitter=False) for attempt in range(1, attempts))
        return float(self.timeout_seconds) * attempts + backoff_budget

    def backoff_seconds(self, attempt: int, *, jitter: bool = True) -> float:
        """Delay before retry number ``attempt`` (1-based): capped exponential, full jitter."""
        base = float(self.retry_backoff_seconds) * (2 ** (attempt - 1))
        capped = min(base, float(self.max_retry_backoff_seconds))
        if capped <= 0:
            return 0.0
        if not jitter:
            return capped
        return capped * self.random_fraction()

    def _request_with_retries(
        self,
        *,
        url: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        request_payload = self._prepare_request_payload(payload)
        deadline_at = self.monotonic() + self.effective_deadline_seconds()
        attempt = 0
        while True:
            try:
                if self.streaming and self._is_chat_completions_url(url):
                    return self.transport.post_stream_chat_completion(
                        url=url,
                        headers=self._headers(),
                        payload=request_payload,
                        timeout_seconds=self.timeout_seconds,
                    )
                return self.transport.post_json(
                    url=url,
                    headers=self._headers(),
                    payload=request_payload,
                    timeout_seconds=self.timeout_seconds,
                )
            except ProviderTransportError as exc:
                if isinstance(exc, ProviderHTTPError) and not self._should_retry_http(exc.code):
                    raise
                if attempt >= self.max_retries:
                    raise
                attempt += 1
                delay = self.backoff_seconds(attempt)
                retry_after = getattr(exc, "retry_after_seconds", None)
                if retry_after is not None:
                    delay = max(delay, min(float(retry_after), float(self.max_retry_backoff_seconds)))
                remaining = deadline_at - self.monotonic()
                if delay > remaining:
                    raise ProviderDeadlineExceeded(
                        f"Provider call deadline exceeded after {attempt} attempt(s); "
                        f"next retry would need {delay:.1f}s but only {max(0.0, remaining):.1f}s remain."
                    ) from exc
            if delay > 0:
                self.sleep(delay)

    def _is_chat_completions_url(self, url: str) -> bool:
        return url.rstrip("/").endswith("/chat/completions")

    def _prepare_request_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        # NVIDIA NIM rejects (or hangs on) chat_completions requests that
        # combine response_format with stream=true. Drop response_format
        # when streaming; the prompt asks the model to emit JSON and the
        # client parses fenced or balanced JSON from text.
        if not self.streaming:
            return payload
        if "response_format" not in payload:
            return payload
        scrubbed = dict(payload)
        scrubbed.pop("response_format", None)
        return scrubbed

    def _should_retry_http(self, code: int) -> bool:
        return is_retryable_http_status(code)

    def _build_payload(self, request: TranslationPromptRequest, *, api_mode: str) -> dict[str, Any]:
        if api_mode == "chat_completions":
            payload = {
                "model": request.model_name,
                "messages": [
                    {"role": "system", "content": request.system_prompt},
                    {"role": "user", "content": self._chat_completions_user_prompt(request)},
                ],
                "response_format": {"type": "json_object"},
            }
            if self.max_output_tokens is not None:
                payload["max_tokens"] = self.max_output_tokens
            return payload
        return {
            "model": request.model_name,
            "input": [
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": request.system_prompt}],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": request.user_prompt}],
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "translation_worker_output",
                    "schema": request.response_schema,
                }
            },
        }

    def _build_structured_payload(
        self,
        *,
        model_name: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
        schema_name: str,
        api_mode: str,
    ) -> dict[str, Any]:
        if api_mode == "chat_completions":
            payload = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "response_format": {"type": "json_object"},
            }
            if self.max_output_tokens is not None:
                payload["max_tokens"] = self.max_output_tokens
            return payload
        return {
            "model": model_name,
            "input": [
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": system_prompt}],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": user_prompt}],
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "schema": response_schema,
                }
            },
        }

    def _chat_completions_user_prompt(self, request: TranslationPromptRequest) -> str:
        schema_json = json.dumps(request.response_schema, ensure_ascii=False, separators=(",", ":"))
        output_contract = (
            "Return exactly one JSON object with these top-level keys only: "
            "packet_id, target_segments, alignment_suggestions, low_confidence_flags, notes.\n"
            f"packet_id must equal: {request.packet_id}\n"
            "Do not use top-level keys like translation or translations.\n"
            "Every current source sentence must be covered through target_segments and alignment_suggestions.\n"
            "When confidence is normal, low_confidence_flags should be []. notes may be [].\n"
            "Required JSON schema:\n"
            f"{schema_json}"
        )
        return f"{request.user_prompt}\n{output_contract}"

    def _extract_output_payload(self, response: dict[str, Any], *, api_mode: str) -> dict[str, Any]:
        if api_mode == "chat_completions":
            return self._extract_chat_completions_payload(response)
        if isinstance(response.get("output_parsed"), dict):
            return response["output_parsed"]

        output_blocks = response.get("output")
        if isinstance(output_blocks, list):
            for block in output_blocks:
                if not isinstance(block, dict):
                    continue
                for content in block.get("content", []):
                    payload = self._extract_payload_from_content(content)
                    if payload is not None:
                        return payload

        raise ProviderResponseFormatError("Provider response did not include a structured JSON output payload.")

    def _extract_generic_output_payload(self, response: dict[str, Any], *, api_mode: str) -> dict[str, Any]:
        if api_mode == "chat_completions":
            choices = response.get("choices")
            if not isinstance(choices, list) or not choices:
                raise ProviderResponseFormatError("Provider response did not include chat completion choices.")
            message = choices[0].get("message")
            if not isinstance(message, dict):
                raise ProviderResponseFormatError("Provider response did not include a chat completion message.")
            payload = self._extract_json_object_from_content(message.get("content"))
            if payload is not None:
                return payload
            raise ProviderResponseFormatError("Provider response did not include a structured JSON output payload.")
        if isinstance(response.get("output_parsed"), dict):
            return response["output_parsed"]

        output_blocks = response.get("output")
        if isinstance(output_blocks, list):
            for block in output_blocks:
                if not isinstance(block, dict):
                    continue
                payload = self._extract_json_object_from_content(block.get("content", []))
                if payload is not None:
                    return payload

        raise ProviderResponseFormatError("Provider response did not include a structured JSON output payload.")

    def _extract_chat_completions_payload(self, response: dict[str, Any]) -> dict[str, Any]:
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderResponseFormatError("Provider response did not include chat completion choices.")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise ProviderResponseFormatError("Provider response did not include a chat completion message.")
        content = message.get("content")
        payload = self._extract_payload_from_content(content)
        if payload is not None:
            return payload
        raise ProviderResponseFormatError("Provider response did not include a structured JSON output payload.")

    def _extract_usage(self, response: dict[str, Any], *, api_mode: str, latency_ms: int) -> TranslationUsage:
        usage_payload = response.get("usage")
        raw_usage = usage_payload if isinstance(usage_payload, dict) else {}
        if api_mode == "chat_completions":
            token_in = self._coerce_int(raw_usage.get("prompt_tokens"))
            token_out = self._coerce_int(raw_usage.get("completion_tokens"))
            total_tokens = self._coerce_int(raw_usage.get("total_tokens")) or (token_in + token_out)
        else:
            token_in = self._coerce_int(raw_usage.get("input_tokens"))
            token_out = self._coerce_int(raw_usage.get("output_tokens"))
            total_tokens = self._coerce_int(raw_usage.get("total_tokens")) or (token_in + token_out)

        prompt_cache_hit_tokens, prompt_cache_miss_tokens = self._prompt_cache_tokens(
            raw_usage, token_in=token_in
        )

        return TranslationUsage(
            token_in=token_in,
            token_out=token_out,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            cost_usd=self._estimate_cost_usd(
                token_in=token_in,
                token_out=token_out,
                prompt_cache_hit_tokens=prompt_cache_hit_tokens,
                prompt_cache_miss_tokens=prompt_cache_miss_tokens,
            ),
            provider_request_id=self._coerce_str(response.get("id")),
            raw_usage=raw_usage,
        )

    def _estimate_cost_usd(
        self,
        *,
        token_in: int,
        token_out: int,
        prompt_cache_hit_tokens: int,
        prompt_cache_miss_tokens: int,
    ) -> float | None:
        if self.output_cost_per_1m_tokens is None:
            return None
        if self.input_cost_per_1m_tokens is None and self.input_cache_hit_cost_per_1m_tokens is None:
            return None
        cache_hit_price = self.input_cache_hit_cost_per_1m_tokens
        miss_price = self.input_cost_per_1m_tokens
        if miss_price is None:
            miss_price = 0.0
        if cache_hit_price is None:
            cache_hit_price = miss_price
        input_cost = (prompt_cache_hit_tokens / 1_000_000) * cache_hit_price
        input_cost += (prompt_cache_miss_tokens / 1_000_000) * miss_price
        output_cost = (token_out / 1_000_000) * self.output_cost_per_1m_tokens
        return round(input_cost + output_cost, 8)

    def _prompt_cache_tokens(self, raw_usage: dict[str, Any], *, token_in: int) -> tuple[int, int]:
        """Split prompt tokens into (cache hits, cache misses).

        Providers report caching differently: DeepSeek gives explicit
        ``prompt_cache_hit_tokens`` / ``prompt_cache_miss_tokens``; OpenAI nests
        ``prompt_tokens_details.cached_tokens`` (chat) or
        ``input_tokens_details.cached_tokens`` (responses); Anthropic-style
        gateways expose ``cache_read_input_tokens`` (hits) and
        ``cache_creation_input_tokens`` (written, billed as misses). Whatever
        is missing is derived from the prompt total.
        """
        hit = self._coerce_int(raw_usage.get("prompt_cache_hit_tokens"))
        miss = self._coerce_int(raw_usage.get("prompt_cache_miss_tokens"))
        if hit == 0:
            hit = self._coerce_int(raw_usage.get("cache_read_input_tokens"))
        if hit == 0:
            for details_key in ("prompt_tokens_details", "input_tokens_details"):
                details = raw_usage.get(details_key)
                if isinstance(details, dict):
                    hit = self._coerce_int(details.get("cached_tokens"))
                    if hit:
                        break
        if miss == 0:
            miss = self._coerce_int(raw_usage.get("cache_creation_input_tokens"))
        hit = max(0, min(hit, token_in)) if token_in else max(0, hit)
        if miss == 0:
            miss = max(0, token_in - hit)
        return hit, miss

    def _coerce_int(self, value: Any) -> int:
        if value is None:
            return 0
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _coerce_str(self, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return str(value)

    def _extract_payload_from_content(self, content: Any) -> dict[str, Any] | None:
        if isinstance(content, list):
            for item in content:
                payload = self._extract_payload_from_content(item)
                if payload is not None:
                    return payload
            return None
        if not isinstance(content, dict):
            if isinstance(content, str):
                return self._extract_payload_from_text(content)
            return None

        json_payload = content.get("json")
        if isinstance(json_payload, dict):
            return self._unwrap_payload_candidate(json_payload)

        text = content.get("text")
        if isinstance(text, str):
            return self._extract_payload_from_text(text)

        return None

    def _extract_payload_from_text(self, text: str) -> dict[str, Any] | None:
        parsed = self._parse_json_object_candidate(text)
        if parsed is not None:
            return parsed

        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fence_match is not None:
            parsed = self._parse_json_object_candidate(fence_match.group(1))
            if parsed is not None:
                return parsed

        return self._extract_balanced_json_object(text)

    def _extract_json_object_from_content(self, content: Any) -> dict[str, Any] | None:
        if isinstance(content, list):
            for item in content:
                payload = self._extract_json_object_from_content(item)
                if payload is not None:
                    return payload
            return None
        if not isinstance(content, dict):
            if isinstance(content, str):
                return self._extract_json_object_from_text(content)
            return None

        json_payload = content.get("json")
        if isinstance(json_payload, dict):
            return json_payload

        text = content.get("text")
        if isinstance(text, str):
            return self._extract_json_object_from_text(text)

        return None

    def _extract_json_object_from_text(self, text: str) -> dict[str, Any] | None:
        parsed = self._parse_json_dict_candidate(text)
        if parsed is not None:
            return parsed

        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fence_match is not None:
            parsed = self._parse_json_dict_candidate(fence_match.group(1))
            if parsed is not None:
                return parsed

        return self._extract_balanced_json_dict(text)

    def _parse_json_dict_candidate(self, text: str) -> dict[str, Any] | None:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        return parsed

    def _parse_json_object_candidate(self, text: str) -> dict[str, Any] | None:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        return self._unwrap_payload_candidate(parsed)

    def _unwrap_payload_candidate(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        if self._looks_like_translation_payload(payload):
            return payload

        for key in ("translation", "output", "result", "data", "response"):
            nested = payload.get(key)
            if isinstance(nested, dict) and self._looks_like_translation_payload(nested):
                return nested
        return None

    def _looks_like_translation_payload(self, payload: dict[str, Any]) -> bool:
        required_keys = {"packet_id", "target_segments", "alignment_suggestions"}
        return required_keys.issubset(payload.keys())

    def _normalize_translation_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        normalized.setdefault("low_confidence_flags", [])
        normalized.setdefault("notes", [])
        normalized["target_segments"] = [
            self._normalize_target_segment(item)
            for item in list(normalized.get("target_segments") or [])
            if isinstance(item, dict)
        ]
        normalized["alignment_suggestions"] = [
            self._normalize_alignment_suggestion(item)
            for item in list(normalized.get("alignment_suggestions") or [])
            if isinstance(item, dict)
        ]
        normalized["low_confidence_flags"] = [
            self._normalize_low_confidence_flag(item)
            for item in list(normalized.get("low_confidence_flags") or [])
            if isinstance(item, dict)
        ]
        return normalized

    def _normalize_target_segment(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        if "segment_type" not in normalized:
            for alias in ("type", "target_type"):
                value = normalized.get(alias)
                if value is not None:
                    normalized["segment_type"] = value
                    break
        normalized["source_sentence_ids"] = self._normalize_id_list(
            normalized.get("source_sentence_ids"),
            fallback_keys=("source_sentence_id", "source_ids", "source_id"),
            payload=normalized,
        )
        for alias in ("source_sentence_id", "source_ids", "source_id", "type", "target_type"):
            normalized.pop(alias, None)
        return normalized

    def _normalize_alignment_suggestion(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        normalized["source_sentence_ids"] = self._normalize_id_list(
            normalized.get("source_sentence_ids"),
            fallback_keys=("source_sentence_id", "source_ids", "source_id"),
            payload=normalized,
        )
        normalized["target_temp_ids"] = self._normalize_id_list(
            normalized.get("target_temp_ids"),
            fallback_keys=("target_temp_id", "target_ids", "target_id"),
            payload=normalized,
        )
        for alias in ("source_sentence_id", "source_ids", "source_id", "target_temp_id", "target_ids", "target_id"):
            normalized.pop(alias, None)
        return normalized

    def _normalize_low_confidence_flag(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        if normalized.get("sentence_id") is None:
            for alias in ("source_sentence_id", "source_id"):
                value = normalized.get(alias)
                if value is not None:
                    normalized["sentence_id"] = value
                    break
        for alias in ("source_sentence_id", "source_id"):
            normalized.pop(alias, None)
        return normalized

    def _normalize_id_list(
        self,
        value: Any,
        *,
        fallback_keys: tuple[str, ...],
        payload: dict[str, Any] | None = None,
    ) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value if item is not None and str(item).strip()]
        if value is not None and str(value).strip():
            return [str(value)]
        if payload is not None:
            for key in fallback_keys:
                fallback_value = payload.get(key)
                if isinstance(fallback_value, list):
                    return [str(item) for item in fallback_value if item is not None and str(item).strip()]
                if fallback_value is not None and str(fallback_value).strip():
                    return [str(fallback_value)]
        return []

    def _extract_balanced_json_object(self, text: str) -> dict[str, Any] | None:
        return self._extract_balanced_json_with_parser(text, self._parse_json_object_candidate)

    def _extract_balanced_json_dict(self, text: str) -> dict[str, Any] | None:
        return self._extract_balanced_json_with_parser(text, self._parse_json_dict_candidate)

    def _extract_balanced_json_with_parser(
        self,
        text: str,
        parser,
    ) -> dict[str, Any] | None:
        in_string = False
        escape = False
        depth = 0
        start_index: int | None = None

        for index, char in enumerate(text):
            if escape:
                escape = False
                continue
            if char == "\\" and in_string:
                escape = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == "{":
                if depth == 0:
                    start_index = index
                depth += 1
                continue
            if char != "}" or depth == 0:
                continue
            depth -= 1
            if depth != 0 or start_index is None:
                continue
            candidate = text[start_index : index + 1]
            parsed = parser(candidate)
            if parsed is not None:
                return parsed
            start_index = None

        return None
