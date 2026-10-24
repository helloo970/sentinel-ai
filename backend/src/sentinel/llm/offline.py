"""Fallback provider without an API key and scripted provider for tests."""

from __future__ import annotations

from typing import Any

from sentinel.llm.base import TextCallback, TurnResult, Usage

OFFLINE_TEXT = (
    "I'm offline right now: no ANTHROPIC_API_KEY is set. "
    "Security Center, network, memory and routines still work."
)


class OfflineProvider:
    name = "offline"
    model = "offline"

    @property
    def available(self) -> bool:
        return False

    async def turn(
        self,
        *,
        system: list[str],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        allow_tools: bool,
        on_text: TextCallback | None = None,
    ) -> TurnResult:
        if on_text:
            await on_text(OFFLINE_TEXT)
        return TurnResult([{"type": "text", "text": OFFLINE_TEXT}], "end_turn", Usage(), self.model)

    async def complete(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 4000,
        json_schema: dict[str, Any] | None = None,
    ) -> TurnResult:
        return TurnResult([{"type": "text", "text": OFFLINE_TEXT}], "end_turn", Usage(), self.model)

    async def research(
        self, *, system: str, prompt: str, allowed_domains: list[str], max_uses: int = 5
    ) -> TurnResult:
        from sentinel.llm.base import LLMError

        raise LLMError("Product search needs the Claude API (no ANTHROPIC_API_KEY set).")


class ScriptedProvider:
    """Returns predefined answers one after another and records all calls."""

    name = "scripted"
    model = "scripted"

    def __init__(
        self, turns: list[list[dict[str, Any]]] | None = None, completions: list[str] | None = None
    ) -> None:
        self.turns = list(turns or [])
        self.completions = list(completions or [])
        self.calls: list[dict[str, Any]] = []

    @property
    def available(self) -> bool:
        return True

    async def turn(
        self,
        *,
        system: list[str],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        allow_tools: bool,
        on_text: TextCallback | None = None,
    ) -> TurnResult:
        self.calls.append(
            {
                "system": system,
                "messages": [dict(m) for m in messages],
                "tools": tools,
                "allow_tools": allow_tools,
            }
        )
        content = self.turns.pop(0) if self.turns else [{"type": "text", "text": "ok"}]
        for block in content:
            if block.get("type") == "text" and on_text:
                await on_text(block["text"])
        stop = "tool_use" if any(b.get("type") == "tool_use" for b in content) else "end_turn"
        return TurnResult(content, stop, Usage(100, 20), self.model)

    async def complete(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 4000,
        json_schema: dict[str, Any] | None = None,
    ) -> TurnResult:
        self.calls.append({"system": system, "prompt": prompt, "json_schema": json_schema})
        text = self.completions.pop(0) if self.completions else "ok"
        return TurnResult([{"type": "text", "text": text}], "end_turn", Usage(100, 20), self.model)

    async def research(
        self, *, system: str, prompt: str, allowed_domains: list[str], max_uses: int = 5
    ) -> TurnResult:
        self.calls.append({"system": system, "prompt": prompt, "research": allowed_domains})
        text = self.completions.pop(0) if self.completions else "{}"
        return TurnResult([{"type": "text", "text": text}], "end_turn", Usage(100, 20), self.model)
