"""Google Gemini via the native REST API (streaming, function calls, JSON, Google Search).

Internally SENTINEL works with content blocks in Claude format (text, tool_use, tool_result).
This provider translates them into Gemini "contents" and back. The key is only sent to Google
in the x-goog-api-key header, never in the URL (otherwise it would end up in logs).
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

API_BASE = "https://generativelanguage.googleapis.com/v1beta"
REFUSAL_REASONS = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"}
RETRY_STATUS = {500, 503}
RETRY_DELAYS_S = (1.5, 4.0)


class _RetryableError(Exception):
    """Temporary error at Google – try again."""


def to_contents(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Claude-style messages → Gemini contents (roles user/model)."""
    names: dict[str, str] = {}
    for message in messages:
        if message["role"] == "assistant" and isinstance(message["content"], list):
            for block in message["content"]:
                if block.get("type") == "tool_use":
                    names[block["id"]] = block["name"]
    contents: list[dict[str, Any]] = []
    for message in messages:
        role = "model" if message["role"] == "assistant" else "user"
        raw = message["content"]
        parts: list[dict[str, Any]] = []
        if isinstance(raw, str):
            parts.append({"text": raw})
        else:
            for block in raw:
                kind = block.get("type")
                if kind == "text" and block.get("text"):
                    part: dict[str, Any] = {"text": block["text"]}
                    if block.get("thought_signature"):
                        part["thoughtSignature"] = block["thought_signature"]
                    parts.append(part)
                elif kind == "tool_use":
                    part = {
                        "functionCall": {"name": block["name"], "args": block.get("input") or {}}
                    }
                    if block.get("thought_signature"):
                        part["thoughtSignature"] = block["thought_signature"]
                    parts.append(part)
                elif kind == "tool_result":
                    content = block.get("content")
                    if not isinstance(content, str):
                        content = json.dumps(content, ensure_ascii=False, default=str)
                    parts.append(
                        {
                            "functionResponse": {
                                "name": names.get(block.get("tool_use_id", ""), "tool"),
                                "response": {"content": content},
                            }
                        }
                    )
        if parts:
            contents.append({"role": role, "parts": parts})
    return contents


def to_declarations(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parametersJsonSchema": tool.get("input_schema") or {"type": "object"},
        }
        for tool in tools
    ]


class _Collector:
    """Collects streamed parts into Claude-style blocks."""

    def __init__(self) -> None:
        self.blocks: list[dict[str, Any]] = []
        self.finish = ""
        self.usage = Usage()

    async def add(self, chunk: dict[str, Any], on_text: TextCallback | None) -> None:
        meta = chunk.get("usageMetadata") or {}
        if meta:
            self.usage = Usage(
                input_tokens=int(meta.get("promptTokenCount") or 0),
                output_tokens=int(meta.get("candidatesTokenCount") or 0)
                + int(meta.get("thoughtsTokenCount") or 0),
                cache_read_tokens=int(meta.get("cachedContentTokenCount") or 0),
            )
        if (chunk.get("promptFeedback") or {}).get("blockReason"):
            self.finish = "SAFETY"
        for candidate in (chunk.get("candidates") or [])[:1]:
            self.finish = candidate.get("finishReason") or self.finish
            for part in (candidate.get("content") or {}).get("parts") or []:
                if part.get("thought"):
                    continue
                signature = part.get("thoughtSignature")
                if "functionCall" in part:
                    call = part["functionCall"]
                    block = {
                        "type": "tool_use",
                        "id": call.get("id") or f"call_{secrets.token_hex(6)}",
                        "name": call.get("name", ""),
                        "input": call.get("args") or {},
                    }
                    if signature:
                        block["thought_signature"] = signature
                    self.blocks.append(block)
                elif part.get("text"):
                    text = part["text"]
                    if self.blocks and self.blocks[-1]["type"] == "text":
                        self.blocks[-1]["text"] += text
                    else:
                        self.blocks.append({"type": "text", "text": text})
                    if signature:
                        self.blocks[-1]["thought_signature"] = signature
                    if on_text is not None:
                        await on_text(text)

    def result(self, model: str) -> TurnResult:
        if any(b["type"] == "tool_use" for b in self.blocks):
            stop = "tool_use"
        elif self.finish in REFUSAL_REASONS:
            stop = "refusal"
        elif self.finish == "MAX_TOKENS":
            stop = "max_tokens"
        else:
            stop = "end_turn"
        return TurnResult(self.blocks, stop, self.usage, model)


class GeminiProvider:
    name = "gemini"

    def __init__(self, *, api_key: str, model: str, client: httpx.AsyncClient | None = None):
        self.model = model
        self._key = api_key
        self._client = client

    @property
    def available(self) -> bool:
        return True

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            # Don't wait for minutes: on the free tier Google otherwise queues requests at the back
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(40.0, connect=10.0))
        return self._client

    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self._key, "Content-Type": "application/json"}

    async def _stream(self, body: dict[str, Any], on_text: TextCallback | None) -> TurnResult:
        # Overload (503) and short outages (500) at Google: retry twice
        for delay in RETRY_DELAYS_S:
            try:
                return await self._stream_once(body, on_text)
            except _RetryableError:
                await asyncio.sleep(delay)
        try:
            return await self._stream_once(body, on_text)
        except _RetryableError as exc:
            raise LLMError("Gemini is overloaded right now. Please try again in a moment.") from exc

    async def _stream_once(self, body: dict[str, Any], on_text: TextCallback | None) -> TurnResult:
        url = f"{API_BASE}/models/{self.model}:streamGenerateContent?alt=sse"
        collector = _Collector()
        try:
            async with self._http().stream("POST", url, json=body, headers=self._headers()) as resp:
                if resp.status_code in RETRY_STATUS:
                    await resp.aread()
                    raise _RetryableError(resp.status_code)
                if resp.status_code != 200:
                    await resp.aread()
                    raise _status_error(resp.status_code, resp.text)
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data:
                        continue
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    await collector.add(chunk, on_text)
        except httpx.TimeoutException as exc:
            raise LLMError(
                "Gemini is not responding (over 40 s). Usually the free quota is used up – "
                "enable billing in Google AI Studio or try again later."
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError("No connection to Google Gemini.") from exc
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
            "systemInstruction": {"parts": [{"text": "\n\n".join(system)}]},
            "contents": to_contents(messages),
            "generationConfig": {"maxOutputTokens": 8000},
        }
        if tools:
            body["tools"] = [{"functionDeclarations": to_declarations(tools)}]
            body["toolConfig"] = {
                "functionCallingConfig": {"mode": "AUTO" if allow_tools else "NONE"}
            }
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
        config: dict[str, Any] = {"maxOutputTokens": max_tokens}
        if json_schema is not None:
            config["responseMimeType"] = "application/json"
            config["responseJsonSchema"] = json_schema
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": config,
        }
        return await self._stream(body, None)

    async def research(
        self, *, system: str, prompt: str, allowed_domains: list[str], max_uses: int = 5
    ) -> TurnResult:
        """Google Search as the only tool. Gemini can't hard-limit domains – so the shop
        service checks every URL against the allowlist itself afterwards."""
        domains = ", ".join(allowed_domains)
        body = {
            "systemInstruction": {"parts": [{"text": f"{system}\n\nSearch only on: {domains}"}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "tools": [{"google_search": {}}],
            "generationConfig": {"maxOutputTokens": 8000},
        }
        return await self._stream(body, None)

    async def list_models(self) -> list[str]:
        try:
            resp = await self._http().get(
                f"{API_BASE}/models?pageSize=200", headers=self._headers()
            )
        except httpx.HTTPError as exc:
            raise LLMError("No connection to Google Gemini.") from exc
        if resp.status_code != 200:
            raise _status_error(resp.status_code, resp.text)
        return [
            m["name"].removeprefix("models/")
            for m in resp.json().get("models", [])
            if "generateContent" in (m.get("supportedGenerationMethods") or [])
        ]


def _status_error(status: int, text: str) -> LLMError:
    try:
        detail = json.loads(text).get("error", {})
    except (ValueError, AttributeError):
        detail = {}
    reason = str(detail.get("status") or "")
    log.warning("gemini error %s %s", status, reason)
    if status in (401, 403) or reason in ("UNAUTHENTICATED", "PERMISSION_DENIED"):
        return LLMError("The Gemini key was rejected. Please check GEMINI_API_KEY.")
    if status == 404:
        return LLMError("Gemini model not found – check SENTINEL_GEMINI_MODEL.")
    if status == 429:
        return LLMError(
            "Gemini quota used up. The free tier only allows a few requests per day – "
            "enable billing in Google AI Studio and Jarvis will be fast."
        )
    if status == 400:
        return LLMError("The request to Gemini was invalid (details in the backend log).")
    return LLMError(f"Gemini reported error {status}.")
