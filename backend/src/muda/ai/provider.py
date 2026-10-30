"""Provider-independent AI interface and normalized errors."""

from typing import Protocol

from muda.ai.types import AIChangeRequest, AIChangeResult, TokenUsage


class AIProviderError(Exception):
    """Normalized provider failure. `code` is safe to store and show; messages carry no secrets."""

    def __init__(self, code: str, usage: TokenUsage | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.usage = usage  # billed usage, when the provider reported it despite the failure


class AITimeoutError(AIProviderError):
    def __init__(self) -> None:
        super().__init__("AI_TIMEOUT")


class AIUnavailableError(AIProviderError):
    def __init__(self) -> None:
        super().__init__("AI_UNAVAILABLE")


class AIInputTooLargeError(AIProviderError):
    def __init__(self) -> None:
        super().__init__("AI_INPUT_TOO_LARGE")


class AIBadResponseError(AIProviderError):
    def __init__(self, usage: TokenUsage | None = None) -> None:
        super().__init__("AI_BAD_RESPONSE", usage)


class AIProvider(Protocol):
    async def generate_change(self, request: AIChangeRequest) -> AIChangeResult: ...
