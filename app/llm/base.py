"""Model-provider abstraction. The conversation engine only ever talks to this interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass


class LLMError(Exception):
    """Generic provider failure."""


class LLMUnavailable(LLMError):
    """Cannot reach the model server."""


class LLMTimeout(LLMError):
    """The model took too long."""


class LLMModelMissing(LLMError):
    """The configured model is not installed on the server."""


class LLMBadResponse(LLMError):
    """The server answered with something unusable."""


@dataclass
class LLMResult:
    text: str
    model: str
    duration_ms: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    finish_reason: str | None = None
    is_truncated: bool = False


class LLMProvider(ABC):
    name: str = "base"
    model: str = ""

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        json_mode: bool = False,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResult: ...

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        json_mode: bool = False,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncGenerator[str, None]:
        """Yield text tokens as they arrive from the model."""
        res = await self.chat(messages, json_mode=json_mode, max_tokens=max_tokens, temperature=temperature)
        yield res.text

    @abstractmethod
    async def health(self) -> dict: ...

    async def warmup(self) -> None:
        return None

    async def aclose(self) -> None:
        return None
