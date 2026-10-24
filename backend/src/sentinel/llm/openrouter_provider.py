"""OpenRouter (OpenAI-compatible chat API): streaming, tools, JSON, web search.

Internally the content blocks stay in Claude format (text, tool_use, tool_result); this
provider translates them into OpenAI messages and back. "reasoning_details" are passed
back to the model unchanged (otherwise it loses track during tool use).
The key is only sent to openrouter.ai in the Authorization header.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from typing import Any

import httpx

from sentinel.llm.base import LLMError, TextCallback, TurnResult, Usage

log = logging.getLogger(__name__)

API_URL = "https://openrouter.ai/api/v1/chat/completions"
MODELS_URL = "https://openrouter.ai/api/v1/models"
RETRY_STATUS = {500, 502, 503}
RETRY_DELAYS_S = (1.0, 3.0)


class _RetryableError(Exception):
    """Temporary error – try again."""


def to_messages(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Claude-style messages → OpenAI messages."""
    out: list[dict[str, Any]] = [{"role": "system", "content": system}] if system else []
    for message in messages:
        raw = message["content"]
        if isinstance(raw, str):
            out.append({"role": message["role"], "content": raw})
            continue
        if message["role"] == "assistant":
            text = "".join(b.get("text", "") for b in raw if b.get("type") == "text")
            entry: dict[str, Any] = {"role": "assistant", "content": text or None}
            calls = [
                {
                    "id": b["id"],
                    "type": "function",
                    "function": {
                        "name": b["name"],
                        "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False),
                    },
                }
                for b in raw
                if b.get("type") == "tool_use"
            ]
            if calls:
                entry["tool_calls"] = calls
            details = next(
                (b["reasoning_details"] for b in raw if b.get("reasoning_details")), None
            )
            if details:
                entry["reasoning_details"] = details
            out.append(entry)
            continue
        for block in raw:
            if block.get("type") == "tool_result":
                content = block.get("content")
                if not isinstance(content, str):
                    content = json.dumps(content, ensure_ascii=False, default=str)
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": content,
                    }
                )
            elif block.get("type") == "text":
                out.append({"role": "user", "content": block.get("text", "")})
    return out


def to_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
            },
        }
        for tool in tools
    ]


class _Collector:
    def __init__(self) -> None:
        self.text = ""
        self.calls: dict[int, dict[str, Any]] = {}
        self.reasoning: list[Any] = []
        self.finish = ""
        self.usage = Usage()

    async def add(self, chunk: dict[str, Any], on_text: TextCallback | None) -> None:
        if chunk.get("error"):
            raise LLMError(f"OpenRouter: {str(chunk['error'].get('message', ''))[:150]}")
        usage = chunk.get("usage") or {}
        if usage:
            details = usage.get("prompt_tokens_details") or {}
            self.usage = Usage(
                input_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("completion_tokens") or 0),
                cache_read_tokens=int(details.get("cached_tokens") or 0),
            )
        for choice in (chunk.get("choices") or [])[:1]:
            self.finish = choice.get("finish_reason") or self.finish
            delta = choice.get("delta") or {}
            if delta.get("content"):
                self.text += delta["content"]
                if on_text is not None:
                    await on_text(delta["content"])
            if delta.get("reasoning_details"):
                self.reasoning.extend(delta["reasoning_details"])
            for call in delta.get("tool_calls") or []:
                slot = self.calls.setdefault(
                    int(call.get("index", 0)), {"id": "", "name": "", "arguments": ""}
                )
                slot["id"] = call.get("id") or slot["id"]
                fn = call.get("function") or {}
                slot["name"] += fn.get("name") or ""
                slot["arguments"] += fn.get("arguments") or ""

    def result(self, model: str) -> TurnResult:
        blocks: list[dict[str, Any]] = []
        if self.text:
            blocks.append({"type": "text", "text": self.text})
        for _, call in sorted(self.calls.items()):
            try:
                args = json.loads(call["arguments"] or "{}")
            except json.JSONDecodeError:
                args = None  # the chat rejects invalid parameters
            blocks.append(
                {
                    "type": "tool_use",
                    "id": call["id"] or f"call_{secrets.token_hex(6)}",
                    "name": call["name"],
                    "input": args,
                }
            )
        if self.reasoning and blocks:
            blocks[0]["reasoning_details"] = self.reasoning
        if self.calls:
            stop = "tool_use"
        elif self.finish == "content_filter":
            stop = "refusal"
        elif self.finish == "length":
            stop = "max_tokens"
        else:
            stop = "end_turn"
        return TurnResult(blocks, stop, self.usage, model)


class OpenRouterProvider:
    name = "openrouter"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        reasoning: bool = False,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = model
        self.reasoning = reasoning
        self._key = api_key
        self._client = client

    @property
    def available(self) -> bool:
        return True

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))
        return self._client

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
            "X-Title": "SENTINEL AI",
        }

    async def _stream(self, body: dict[str, Any], on_text: TextCallback | None) -> TurnResult:
        body = {
            "model": self.model,
            "stream": True,
            "usage": {"include": True},
            # Some models (e.g. Opus 5.5) require thinking – then as little as possible
            "reasoning": {"enabled": True} if self.reasoning else {"effort": "low"},
            **body,
        }
        for delay in RETRY_DELAYS_S:
            try:
                return await self._stream_once(body, on_text)
            except _RetryableError:
                await asyncio.sleep(delay)
        try:
            return await self._stream_once(body, on_text)
        except _RetryableError as exc:
            raise LLMError("OpenRouter is overloaded right now – try again in a moment.") from exc

    async def _stream_once(self, body: dict[str, Any], on_text: TextCallback | None) -> TurnResult:
        collector = _Collector()
        try:
            async with self._http().stream(
                "POST", API_URL, json=body, headers=self._headers()
            ) as resp:
                if resp.status_code in RETRY_STATUS:
                    await resp.aread()
                    raise _RetryableError(resp.status_code)
                if resp.status_code != 200:
                    await resp.aread()
                    raise _status_error(resp.status_code, resp.text)
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue  # comments like ": OPENROUTER PROCESSING"
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    await collector.add(chunk, on_text)
        except httpx.TimeoutException as exc:
            raise LLMError("OpenRouter is not responding (over 60 s).") from exc
        except httpx.HTTPError as exc:
            raise LLMError("No connection to OpenRouter.") from exc
        return collector.result(self.model)

    async def turn(
        self,
        *,
        system: list[str],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        allow_tools: bool,
        on_text: TextCallback | None = None,
    ) -> TurnResult:
        body: dict[str, Any] = {
            "messages": to_messages("\n\n".join(system), messages),
            "max_tokens": 8000,
        }
        if tools:
            body["tools"] = to_tools(tools)
            body["tool_choice"] = "auto" if allow_tools else "none"
        return await self._stream(body, on_text)

    async def complete(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 4000,
        json_schema: dict[str, Any] | None = None,
    ) -> TurnResult:
        """A call without tools – for untrusted data."""
        body: dict[str, Any] = {
            "messages": to_messages(system, [{"role": "user", "content": prompt}]),
            "max_tokens": max_tokens,
        }
        if json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "result", "strict": True, "schema": json_schema},
            }
        return await self._stream(body, None)

    async def research(
        self, *, system: str, prompt: str, allowed_domains: list[str], max_uses: int = 5
    ) -> TurnResult:
        """OpenRouter web search. Domains can't be hard-limited – the shop service
        checks every URL against the allowlist itself afterwards."""
        domains = ", ".join(allowed_domains)
        body = {
            "messages": to_messages(
                f"{system}\n\nSearch only on: {domains}",
                [{"role": "user", "content": prompt}],
            ),
            "plugins": [{"id": "web", "max_results": max_uses}],
            "max_tokens": 8000,
        }
        return await self._stream(body, None)

    async def check_model(self) -> bool:
        try:
            resp = await self._http().get(MODELS_URL)
        except httpx.HTTPError as exc:
            raise LLMError("No connection to OpenRouter.") from exc
        return any(
            m.get("id") == self.model and "tools" in (m.get("supported_parameters") or [])
            for m in resp.json().get("data", [])
        )


def _status_error(status: int, text: str) -> LLMError:
    try:
        message = str(json.loads(text).get("error", {}).get("message", ""))[:150]
    except (ValueError, AttributeError):
        message = ""
    log.warning("openrouter error %s", status)
    if status == 401:
        return LLMError("The OpenRouter key was rejected. Please enter it again.")
    if status == 402:
        return LLMError("OpenRouter credits used up – top up on openrouter.ai.")
    if status == 404:
        return LLMError("OpenRouter model not found – check SENTINEL_OPENROUTER_MODEL.")
    if status == 429:
        return LLMError("Too many requests to OpenRouter – wait a moment.")
    if status == 400:
        return LLMError(f"The request to OpenRouter was invalid: {message}")
    return LLMError(f"OpenRouter reported error {status}.")
