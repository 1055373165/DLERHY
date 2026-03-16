from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from book_agent.workers.contracts import TranslationUsage, TranslationWorkerOutput, TranslationWorkerResult
from book_agent.workers.translator import TranslationModelClient, TranslationPromptRequest


class ProviderTransportError(RuntimeError):
    pass


class ProviderHTTPError(ProviderTransportError):
    def __init__(self, code: int, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"Provider returned HTTP {code}: {detail}")


class ProviderNetworkError(ProviderTransportError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Provider request failed: {reason}")


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


class UrllibJSONTransport:
    def post_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            url=url,
            data=body,
            headers={
                "Content-Type": "application/json",
                **headers,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ProviderHTTPError(exc.code, detail or str(exc.reason)) from exc
        except URLError as exc:
            raise ProviderNetworkError(str(exc.reason)) from exc

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderTransportError("Provider returned non-JSON response.") from exc


@dataclass(slots=True)
class OpenAICompatibleTranslationClient(TranslationModelClient):
    api_key: str
    base_url: str = "https://api.openai.com/v1/responses"
    timeout_seconds: int = 60
    max_retries: int = 0
    retry_backoff_seconds: float = 1.0
    input_cache_hit_cost_per_1m_tokens: float | None = None
    input_cost_per_1m_tokens: float | None = None
    output_cost_per_1m_tokens: float | None = None
    transport: JSONTransport = field(default_factory=UrllibJSONTransport)
    extra_headers: dict[str, str] = field(default_factory=dict)

    def generate_translation(self, request: TranslationPromptRequest) -> TranslationWorkerResult:
        endpoint_url, api_mode = self._resolve_endpoint()
        request_started_at = time.perf_counter()
        response = self._request_with_retries(
            url=endpoint_url,
            payload=self._build_payload(request, api_mode=api_mode),
        )
        latency_ms = max(1, round((time.perf_counter() - request_started_at) * 1000))
        output_payload = self._extract_output_payload(response, api_mode=api_mode)
        try:
            output = TranslationWorkerOutput.model_validate(output_payload)
        except Exception as exc:
            raise RuntimeError("Provider response did not match TranslationWorkerOutput schema.") from exc
        return TranslationWorkerResult(
            output=output,
            usage=self._extract_usage(response, api_mode=api_mode, latency_ms=latency_ms),
        )

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

    def _request_with_retries(
        self,
        *,
        url: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        attempt = 0
        while True:
            try:
                return self.transport.post_json(
                    url=url,
                    headers=self._headers(),
                    payload=payload,
                    timeout_seconds=self.timeout_seconds,
                )
            except ProviderHTTPError as exc:
                if not self._should_retry_http(exc.code) or attempt >= self.max_retries:
                    raise RuntimeError(str(exc)) from exc
            except ProviderNetworkError as exc:
                if attempt >= self.max_retries:
                    raise RuntimeError(str(exc)) from exc
            except ProviderTransportError as exc:
                if attempt >= self.max_retries:
                    raise RuntimeError(str(exc)) from exc
            attempt += 1
            time.sleep(self.retry_backoff_seconds * (2 ** (attempt - 1)))

    def _should_retry_http(self, code: int) -> bool:
        return code in {408, 409, 429} or 500 <= code <= 599

    def _build_payload(self, request: TranslationPromptRequest, *, api_mode: str) -> dict[str, Any]:
        if api_mode == "chat_completions":
            return {
                "model": request.model_name,
                "messages": [
                    {"role": "system", "content": request.system_prompt},
                    {"role": "user", "content": self._chat_completions_user_prompt(request)},
                ],
                "response_format": {"type": "json_object"},
            }
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

        raise RuntimeError("Provider response did not include a structured JSON output payload.")

    def _extract_chat_completions_payload(self, response: dict[str, Any]) -> dict[str, Any]:
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("Provider response did not include chat completion choices.")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise RuntimeError("Provider response did not include a chat completion message.")
        content = message.get("content")
        payload = self._extract_payload_from_content(content)
        if payload is not None:
            return payload
        raise RuntimeError("Provider response did not include a structured JSON output payload.")

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

        prompt_cache_hit_tokens = self._coerce_int(
            raw_usage.get("prompt_cache_hit_tokens")
            or raw_usage.get("cache_creation_input_tokens")
        )
        prompt_cache_miss_tokens = self._coerce_int(
            raw_usage.get("prompt_cache_miss_tokens")
            or raw_usage.get("cache_read_input_tokens")
        )
        if prompt_cache_hit_tokens == 0 and prompt_cache_miss_tokens == 0:
            prompt_cache_miss_tokens = token_in

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
        if prompt_cache_hit_tokens == 0 and prompt_cache_miss_tokens == 0 and self.input_cost_per_1m_tokens is not None:
            input_cost = (token_in / 1_000_000) * self.input_cost_per_1m_tokens
        output_cost = (token_out / 1_000_000) * self.output_cost_per_1m_tokens
        return round(input_cost + output_cost, 8)

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

    def _extract_balanced_json_object(self, text: str) -> dict[str, Any] | None:
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
            parsed = self._parse_json_object_candidate(candidate)
            if parsed is not None:
                return parsed
            start_index = None

        return None
