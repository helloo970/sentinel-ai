"""Claude via the official Anthropic SDK (streaming, tool use, fallbacks)."""

from __future__ import annotations

import logging
from typing import Any

import anthropic

from sentinel.llm.base import LLMError, TextCallback, TurnResult, Usage

log = logging.getLogger(__name__)

# Models that support server-side fallbacks on refusals
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _usage(message: Any) -> Usage:
    usage = getattr(message, "usage", None)
    if usage is None:
        return Usage()
    return Usage(
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
    )


def _content(message: Any) -> list[dict[str, Any]]:
    return [block.model_dump(mode="json", exclude_none=True) for block in message.content]


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, *, api_key: str, model: str, effort: str = "low", fallbacks: bool = True):
        self.model = model
        self.effort = effort
        self.fallbacks = fallbacks and model in FALLBACK_MODELS
        self._client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=2, timeout=180.0)

    @property
    def available(self) -> bool:
        return True

    def _base_kwargs(self, max_tokens: int) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "output_config": {"effort": self.effort},
        }
        if self.fallbacks:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs

    async def turn(
        self,
        *,
        system: list[str],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        allow_tools: bool,
        on_text: TextCallback | None = None,
    ) -> TurnResult:
        kwargs = self._base_kwargs(16000)
        # The first block is stable and gets cached, the changing context comes after it
        blocks: list[dict[str, Any]] = []
        for index, text in enumerate(system):
            block: dict[str, Any] = {"type": "text", "text": text}
            if index == 0:
                block["cache_control"] = {"type": "ephemeral"}
            blocks.append(block)
        kwargs["system"] = blocks
        kwargs["messages"] = messages
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = {"type": "auto"} if allow_tools else {"type": "none"}
        try:
            async with self._client.beta.messages.stream(**kwargs) as stream:
                async for event in stream:
                    if event.type == "text" and on_text is not None:
                        await on_text(event.text)
                final = await stream.get_final_message()
        except Exception as exc:
            raise _translate(exc) from exc
        return TurnResult(_content(final), final.stop_reason or "", _usage(final), self.model)

    async def complete(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 4000,
        json_schema: dict[str, Any] | None = None,
    ) -> TurnResult:
        """A call without tools – for untrusted data."""
        kwargs = self._base_kwargs(max_tokens)
        kwargs["system"] = system
        kwargs["messages"] = [{"role": "user", "content": prompt}]
        if json_schema is not None:
            kwargs["output_config"] = {
                **kwargs["output_config"],
                "format": {"type": "json_schema", "schema": json_schema},
            }
        try:
            async with self._client.beta.messages.stream(**kwargs) as stream:
                final = await stream.get_final_message()
        except Exception as exc:
            raise _translate(exc) from exc
        return TurnResult(_content(final), final.stop_reason or "", _usage(final), self.model)

    async def research(
        self, *, system: str, prompt: str, allowed_domains: list[str], max_uses: int = 5
    ) -> TurnResult:
        return await _research(self, system, prompt, allowed_domains, max_uses)


WEB_SEARCH_TOOL = "web_search_20260209"


async def _research(
    provider: AnthropicProvider, system: str, prompt: str, allowed_domains: list[str], max_uses: int
) -> TurnResult:
    """Web search as the only (server-side) tool – no actions on the PC."""
    kwargs = provider._base_kwargs(8000)
    kwargs["system"] = system
    kwargs["tools"] = [
        {
            "type": WEB_SEARCH_TOOL,
            "name": "web_search",
            "max_uses": max_uses,
            "allowed_domains": allowed_domains,
        }
    ]
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    usage = Usage()
    for _ in range(3):  # continue pause_turn on long searches
        kwargs["messages"] = messages
        try:
            async with provider._client.beta.messages.stream(**kwargs) as stream:
                final = await stream.get_final_message()
        except Exception as exc:
            raise _translate(exc) from exc
        step = _usage(final)
        usage = Usage(
            usage.input_tokens + step.input_tokens,
            usage.output_tokens + step.output_tokens,
            usage.cache_read_tokens + step.cache_read_tokens,
            usage.cache_write_tokens + step.cache_write_tokens,
        )
        content = _content(final)
        if final.stop_reason != "pause_turn":
            return TurnResult(content, final.stop_reason or "", usage, provider.model)
        messages = [*messages, {"role": "assistant", "content": content}]
    return TurnResult(content, "pause_turn", usage, provider.model)


def _translate(exc: Exception) -> LLMError:
    if isinstance(exc, anthropic.AuthenticationError):
        return LLMError("The API key was rejected. Please check ANTHROPIC_API_KEY.")
    if isinstance(exc, anthropic.PermissionDeniedError):
        return LLMError("The API key has no permission for this model.")
    if isinstance(exc, anthropic.NotFoundError):
        return LLMError("Model not found – check SENTINEL_LLM_MODEL.")
    if isinstance(exc, anthropic.RateLimitError):
        return LLMError("Too many requests to the Claude API – wait a moment.")
    if isinstance(exc, anthropic.BadRequestError):
        log.warning("bad request: %s", exc)
        return LLMError("The request to Claude was invalid (details in the backend log).")
    if isinstance(exc, anthropic.APIStatusError):
        return LLMError(f"Claude API reported error {exc.status_code}.")
    if isinstance(exc, anthropic.APIConnectionError):
        return LLMError("No connection to the Claude API.")
    return LLMError(f"Unexpected error: {type(exc).__name__}")
