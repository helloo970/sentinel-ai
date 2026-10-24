"""Provider-neutral interface for language models."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

TextCallback = Callable[[str], Awaitable[None]]


class LLMError(Exception):
    """Model call error with a message the user can understand."""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass
class TurnResult:
    content: list[dict[str, Any]]
    stop_reason: str
    usage: Usage = field(default_factory=Usage)
    model: str = ""

    @property
    def text(self) -> str:
        return "".join(b.get("text", "") for b in self.content if b.get("type") == "text")

    @property
    def tool_uses(self) -> list[dict[str, Any]]:
        return [b for b in self.content if b.get("type") == "tool_use"]


class LLMProvider(Protocol):
    name: str
    model: str

    @property
    def available(self) -> bool: ...

    async def turn(
        self,
        *,
        system: list[str],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        allow_tools: bool,
        on_text: TextCallback | None = None,
    ) -> TurnResult: ...

    async def complete(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 4000,
        json_schema: dict[str, Any] | None = None,
    ) -> TurnResult: ...

    async def research(
        self,
        *,
        system: str,
        prompt: str,
        allowed_domains: list[str],
        max_uses: int = 5,
    ) -> TurnResult: ...
