"""DeepSeek adapter. All provider-specific HTTP, parsing and error mapping lives here."""

import asyncio
import json
import time
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr

from muda.ai.prompt import build_messages
from muda.ai.provider import (
    AIBadResponseError,
    AIInputTooLargeError,
    AIProviderError,
    AITimeoutError,
    AIUnavailableError,
)
from muda.ai.types import AIChangeRequest, AIChangeResult, TokenUsage


class DeepSeekConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    api_key: SecretStr
    model: str
    base_url: str = "https://api.deepseek.com"
    timeout_s: float = 30.0
    max_retries: int = 1
    max_output_tokens: int = 400
    max_input_chars: int = 6000


_REJECTED = {401: "AI_AUTH", 403: "AI_AUTH", 402: "AI_INSUFFICIENT_BALANCE"}


def _usage(data: dict[str, Any]) -> TokenUsage:
    u = data.get("usage")
    if not isinstance(u, dict):
        return TokenUsage()

    def num(key: str) -> int | None:
        v = u.get(key)
        return v if isinstance(v, int) and not isinstance(v, bool) else None

    return TokenUsage(
        input_tokens=num("prompt_tokens"),
        output_tokens=num("completion_tokens"),
        cache_hit_tokens=num("prompt_cache_hit_tokens"),
        cache_miss_tokens=num("prompt_cache_miss_tokens"),
    )


class DeepSeekProvider:
    def __init__(
        self,
        config: DeepSeekConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_delay_s: float = 1.0,
    ) -> None:
        self._cfg = config
        self._transport = transport
        self._retry_delay_s = retry_delay_s

    async def generate_change(self, request: AIChangeRequest) -> AIChangeResult:
        messages = build_messages(request)
        if sum(len(m["content"]) for m in messages) > self._cfg.max_input_chars:
            raise AIInputTooLargeError
        body = {
            "model": self._cfg.model,
            "messages": messages,
            "max_tokens": self._cfg.max_output_tokens,
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "temperature": 0,
            "stream": False,
        }
        started = time.monotonic()
        response = await self._post_with_retries(body)
        latency_ms = int((time.monotonic() - started) * 1000)
        return self._parse(response, latency_ms)

    async def _post_with_retries(self, body: dict[str, Any]) -> httpx.Response:
        last: AIProviderError = AIUnavailableError()
        headers = {"Authorization": f"Bearer {self._cfg.api_key.get_secret_value()}"}
        # follow_redirects stays off: the key must never be sent to another host.
        async with httpx.AsyncClient(
            base_url=self._cfg.base_url,
            timeout=self._cfg.timeout_s,
            transport=self._transport,
        ) as client:
            for attempt in range(self._cfg.max_retries + 1):
                if attempt:
                    await asyncio.sleep(self._retry_delay_s * attempt)
                try:
                    resp = await client.post("/chat/completions", json=body, headers=headers)
                except httpx.TimeoutException:
                    last = AITimeoutError()
                    continue
                except httpx.TransportError:
                    last = AIUnavailableError()
                    continue
                if resp.status_code == 429 or resp.status_code >= 500:
                    last = AIUnavailableError()
                    continue
                if resp.status_code != 200:
                    raise AIProviderError(_REJECTED.get(resp.status_code, "AI_REQUEST_REJECTED"))
                return resp
        raise last

    def _parse(self, response: httpx.Response, latency_ms: int) -> AIChangeResult:
        try:
            data = response.json()
        except ValueError:
            raise AIBadResponseError from None
        if not isinstance(data, dict):
            raise AIBadResponseError
        usage = _usage(data)  # billed even if the content turns out to be unusable
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
            finish = choice.get("finish_reason")
        except (KeyError, IndexError, TypeError):
            raise AIBadResponseError(usage) from None
        if finish == "length" or not isinstance(content, str) or not content.strip():
            raise AIBadResponseError(usage)
        try:
            payload = json.loads(content)
        except ValueError:
            raise AIBadResponseError(usage) from None
        if not isinstance(payload, dict):
            raise AIBadResponseError(usage)
        return AIChangeResult(
            payload=payload, usage=usage, model=self._cfg.model, latency_ms=latency_ms
        )
