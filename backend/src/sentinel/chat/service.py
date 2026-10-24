"""Jarvis chat: tool-use loop through the permission engine.

Security rules in the flow:
- Tool calls go exclusively to engine.request() (source ASSISTANT).
- If a tool returns untrusted data, the model gets it marked and no more tools for the
  rest of the run (tool_choice = none).
- If the history contains such data, actions only run with confirmation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from sentinel.chat import quick
from sentinel.llm.base import LLMError
from sentinel.llm.prompts import jarvis_context, jarvis_persona
from sentinel.llm.untrusted import UNTRUSTED_RULES, wrap_untrusted
from sentinel.memory.extract import extract_candidates
from sentinel.memory.store import MemoryRejected
from sentinel.permissions.models import Source
from sentinel.voice.stopword import is_stop_command

if TYPE_CHECKING:
    from sentinel.services import Services

log = logging.getLogger(__name__)

Emit = Callable[[dict[str, Any]], Awaitable[None]]
CONVERSATION_RE = re.compile(r"^[a-z0-9\-]{1,40}$")
MAX_ITERATIONS = 6
HISTORY_MESSAGES = 20
MAX_MESSAGE_CHARS = 8000


async def _noop(_: dict[str, Any]) -> None:
    return None


class ChatService:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self._locks: dict[str, asyncio.Lock] = {}

    # ---------------------------------------------------------------- Tools

    def tools(self) -> list[dict[str, Any]]:
        tools = []
        for spec in self.svc.registry.all():
            if not spec.llm_exposed:
                continue
            schema = spec.params.model_json_schema()
            schema.pop("title", None)
            schema.setdefault("properties", {})
            tools.append(
                {
                    "name": spec.name,
                    "description": f"[{spec.risk}] {spec.description}",
                    "input_schema": schema,
                }
            )
        tools.sort(key=lambda t: t["name"])  # fixed order for prompt caching
        return tools

    # ---------------------------------------------------------------- History

    def history(self, conversation: str) -> list[dict[str, Any]]:
        rows = self.svc.db.query(
            "SELECT * FROM (SELECT * FROM chat_messages WHERE conversation = ? "
            "ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
            (conversation, HISTORY_MESSAGES),
        )
        return rows

    def clear(self, conversation: str) -> None:
        self.svc.db.execute("DELETE FROM chat_messages WHERE conversation = ?", (conversation,))

    def _save(self, conversation: str, role: str, content: str, tainted: bool = False) -> int:
        return self.svc.db.execute(
            "INSERT INTO chat_messages(conversation, role, content, tainted, ts) VALUES(?,?,?,?,?)",
            (conversation, role, content, int(tainted), time.time()),
        )

    # ---------------------------------------------------------------- Flow

    async def send(
        self, text: str, *, conversation: str = "default", emit: Emit = _noop, via: str = "chat"
    ) -> dict[str, Any]:
        if not CONVERSATION_RE.match(conversation):
            raise ValueError("invalid conversation id")
        text = text.strip()[:MAX_MESSAGE_CHARS]
        if not text:
            raise ValueError("empty message")
        lock = self._locks.setdefault(conversation, asyncio.Lock())
        async with lock:
            return await self._send(text, conversation, emit, via)

    async def _reply(
        self, conversation: str, text: str, emit: Emit, *, tainted: bool = False
    ) -> dict[str, Any]:
        await emit({"type": "text", "text": text})
        self._save(conversation, "assistant", text, tainted)
        await emit({"type": "done", "text": text})
        return {"text": text, "tainted": tainted}

    async def _send(self, text: str, conversation: str, emit: Emit, via: str) -> dict[str, Any]:
        svc = self.svc
        # Emergency stop via voice/chat is detected locally, never through the model
        if is_stop_command(text):
            await asyncio.to_thread(svc.engine.halt, actor="user", via=via)
            self._save(conversation, "user", text)
            return await self._reply(
                conversation,
                "Emergency stop active. All actions and agents are blocked until you release it.",
                emit,
            )

        brain_answer = await svc.brain.handle_command(text, via=via)
        if brain_answer is not None:
            self._save(conversation, "user", text)
            return await self._reply(conversation, brain_answer, emit)

        command = quick.match(text)
        if command is not None:
            self._save(conversation, "user", text)
            outcome = await asyncio.to_thread(
                svc.engine.request,
                command.action,
                command.params,
                source=Source.USER,
                actor="user",
                reason=f"Quick command ({via})",
            )
            return await self._reply(conversation, quick.reply(command, outcome), emit)

        history = self.history(conversation)
        message_id = self._save(conversation, "user", text)
        await self._remember(text, message_id, emit)
        svc.brain.enqueue_chat(text, message_id)

        ok, reason = svc.costs.check()
        if not ok:
            svc.notifier.notify("Jarvis paused", reason)
            return await self._reply(
                conversation, f"I'm pausing: {reason}. Local features keep running.", emit
            )

        context_tainted = any(row["tainted"] for row in history)
        messages: list[dict[str, Any]] = [
            {"role": row["role"], "content": row["content"]} for row in history
        ]
        messages.append({"role": "user", "content": text})
        memories = [m["text"] for m in svc.memory.search(text, limit=6)]
        # Thoughts from the brain light up live; knowledge from files only as untrusted data
        thoughts, foreign = svc.brain.relevant(text)
        memories += [f"[Style] {t}" for t in svc.brain.style()]
        memories += [f"[Brain] {t}" for t in thoughts]
        context_tainted = context_tainted or bool(foreign)
        system = [
            jarvis_persona(svc.config.user_name),
            jarvis_context(
                autonomy=int(svc.engine.autonomy),
                halted=svc.engine.halted,
                memories=memories,
                tainted=context_tainted,
            ),
        ]
        if foreign:
            system.append(UNTRUSTED_RULES + "\n" + wrap_untrusted("brain_files", foreign))
        tools = self.tools()

        async def on_text(delta: str) -> None:
            await emit({"type": "text", "text": delta})

        turn_tainted = False
        answer_parts: list[str] = []
        for _ in range(MAX_ITERATIONS):
            try:
                result = await svc.llm.turn(
                    system=system,
                    messages=messages,
                    tools=tools,
                    allow_tools=not turn_tainted,
                    on_text=on_text,
                )
            except LLMError as exc:
                await emit({"type": "error", "message": str(exc)})
                return await self._reply(conversation, str(exc), emit, tainted=turn_tainted)
            svc.costs.record(result.model, "chat", result.usage)
            messages.append({"role": "assistant", "content": result.content})
            if result.text:
                answer_parts.append(result.text)
            if result.stop_reason == "refusal":
                note = "Sorry, I can't help with that."
                await emit({"type": "text", "text": note})
                answer_parts.append(note)
                break
            tool_uses = result.tool_uses
            if not tool_uses or result.stop_reason == "max_tokens":
                break

            tool_results = []
            for block in tool_uses:
                content, untrusted = await self._run_tool(
                    block, turn_tainted or context_tainted, turn_tainted, emit
                )
                turn_tainted = turn_tainted or untrusted
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block["id"], "content": content}
                )
            messages.append({"role": "user", "content": tool_results})
            await emit({"type": "text", "text": "\n"})

        answer = "\n".join(p.strip() for p in answer_parts if p.strip()) or "(no answer)"
        self._save(conversation, "assistant", answer, turn_tainted)
        await emit({"type": "done", "text": answer, "tainted": turn_tainted})
        return {"text": answer, "tainted": turn_tainted}

    async def _run_tool(
        self, block: dict[str, Any], tainted: bool, tools_locked: bool, emit: Emit
    ) -> tuple[str, bool]:
        name = str(block.get("name", ""))
        raw = block.get("input")
        if tools_locked:
            # Defense in depth: no more tools after untrusted data
            await emit(
                {
                    "type": "tool",
                    "name": name,
                    "status": "denied",
                    "message": "Tools locked (untrusted data in this run)",
                }
            )
            return json.dumps({"status": "denied", "message": "tools locked"}), False
        if not isinstance(raw, dict):
            return json.dumps({"status": "denied", "message": "INVALID_JSON"}), False
        outcome = await asyncio.to_thread(
            self.svc.engine.request,
            name,
            raw,
            source=Source.ASSISTANT,
            actor="jarvis",
            tainted=tainted,
        )
        await emit(
            {
                "type": "tool",
                "name": name,
                "status": outcome.status,
                "message": outcome.message[:300],
                "approval_id": outcome.approval_id,
                "undo_id": outcome.undo_id,
            }
        )
        if outcome.untrusted and outcome.status == "executed":
            payload = {"status": outcome.status, "message": outcome.message}
            return (
                json.dumps(payload, ensure_ascii=False) + "\n" + wrap_untrusted(name, outcome.data)
            ), True
        return json.dumps(outcome.to_dict(), ensure_ascii=False, default=str)[:20000], False

    async def _remember(self, text: str, message_id: int, emit: Emit) -> None:
        for candidate in extract_candidates(text):
            try:
                memory = self.svc.memory.add(
                    candidate.text, category=candidate.category, source=f"chat:{message_id}"
                )
            except MemoryRejected:
                continue
            await emit({"type": "memory", "id": memory.get("id"), "text": memory.get("text")})
