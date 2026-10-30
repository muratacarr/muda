"""Deterministic provider for tests and local runs. Never calls the network."""

import asyncio
from collections.abc import Callable
from typing import Any

from muda.ai.provider import AIProviderError
from muda.ai.types import AIChangeRequest, AIChangeResult, TokenUsage


class FakeProvider:
    def __init__(
        self,
        respond: Callable[[AIChangeRequest], dict[str, Any]],
        *,
        usage: TokenUsage | None = None,
        delay_s: float = 0.0,
        error: AIProviderError | None = None,
    ) -> None:
        self._respond = respond
        self._usage = usage or TokenUsage(input_tokens=400, output_tokens=40, cache_hit_tokens=0)
        self._delay_s = delay_s
        self._error = error
        self.calls = 0

    async def generate_change(self, request: AIChangeRequest) -> AIChangeResult:
        self.calls += 1
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        if self._error is not None:
            raise self._error
        return AIChangeResult(
            payload=self._respond(request), usage=self._usage, model="deepseek-flash", latency_ms=1
        )
