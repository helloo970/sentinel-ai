"""Chat: tools only through the engine, lock after untrusted data, emergency stop, budget."""

from __future__ import annotations

from typing import Any

import pytest

from sentinel.llm.offline import ScriptedProvider
from sentinel.services import Services


def tool_use(name: str, payload: dict[str, Any], block_id: str = "tu_1") -> dict[str, Any]:
    return {"type": "tool_use", "id": block_id, "name": name, "input": payload}


async def collect(svc: Services, text: str, conversation: str = "default") -> list[dict]:
    events: list[dict] = []

    async def emit(event: dict) -> None:
        events.append(event)

    await svc.chat.send(text, conversation=conversation, emit=emit)
    return events


async def test_plain_answer_is_streamed_and_saved(svc: Services, scripted: ScriptedProvider):
    scripted.turns = [[{"type": "text", "text": "Hello there!"}]]
    events = await collect(svc, "Hi")
    assert {"type": "text", "text": "Hello there!"} in events
    assert events[-1]["type"] == "done"
    history = svc.chat.history("default")
    assert [h["role"] for h in history] == ["user", "assistant"]


async def test_untrusted_tool_result_locks_tools(svc: Services, scripted: ScriptedProvider):
    scripted.turns = [
        [tool_use("list_processes", {"limit": 3})],
        [{"type": "text", "text": "Here are the processes."}],
    ]
    await collect(svc, "Which processes are running?")
    first, second = scripted.calls
    assert first["allow_tools"] is True
    assert second["allow_tools"] is False
    tool_result = second["messages"][-1]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert '<untrusted_data source="list_processes">' in tool_result["content"]
    # History is marked now
    assert svc.chat.history("default")[-1]["tainted"] == 1


async def test_tool_use_after_untrusted_data_is_denied(svc: Services, scripted: ScriptedProvider):
    calls: list[str] = []
    original = svc.engine.request

    def spy(name: str, *args: Any, **kwargs: Any):
        calls.append(name)
        return original(name, *args, **kwargs)

    svc.engine.request = spy  # type: ignore[method-assign]
    scripted.turns = [
        [tool_use("list_autostart", {})],
        # Model tries a tool despite the lock (e.g. through injection in the data)
        [tool_use("set_reminder", {"text": "x", "minutes": 5}, "tu_2")],
        [{"type": "text", "text": "done"}],
    ]
    events = await collect(svc, "Autostart?")
    assert calls == ["list_autostart"]
    assert any(e.get("type") == "tool" and e["status"] == "denied" for e in events)
    assert svc.reminders.list() == []


async def test_tainted_history_forces_confirmation(svc: Services, scripted: ScriptedProvider):
    scripted.turns = [[tool_use("list_ports", {})], [{"type": "text", "text": "ok"}]]
    await collect(svc, "Ports?")
    scripted.turns = [
        [tool_use("set_reminder", {"text": "Tea", "minutes": 5})],
        [{"type": "text", "text": "done"}],
    ]
    events = await collect(svc, "Remind me about tea in 5 minutes")
    tool_events = [e for e in events if e.get("type") == "tool"]
    assert tool_events[0]["status"] == "pending"
    assert svc.reminders.list() == []


async def test_clean_history_executes_low_risk(svc: Services, scripted: ScriptedProvider):
    scripted.turns = [
        [tool_use("set_reminder", {"text": "Tea", "minutes": 5})],
        [{"type": "text", "text": "done"}],
    ]
    events = await collect(svc, "Remind me about tea in 5 minutes")
    assert [e for e in events if e.get("type") == "tool"][0]["status"] == "executed"
    assert len(svc.reminders.list()) == 1


async def test_unknown_tool_is_denied(svc: Services, scripted: ScriptedProvider):
    scripted.turns = [[tool_use("run_shell", {"cmd": "whoami"})], [{"type": "text", "text": "x"}]]
    events = await collect(svc, "do something")
    assert [e for e in events if e.get("type") == "tool"][0]["status"] == "denied"


@pytest.mark.parametrize(
    "phrase", ["SENTINEL, stop!", "Jarvis stop", "emergency stop", "sentinel halt"]
)
async def test_stop_phrase_halts_without_model(
    svc: Services, scripted: ScriptedProvider, phrase: str
):
    await collect(svc, phrase)
    assert svc.engine.halted
    assert scripted.calls == []
    svc.engine.resume(actor="user")


async def test_budget_exceeded_pauses_jarvis(svc: Services, scripted: ScriptedProvider):
    from sentinel.llm.base import Usage

    svc.costs.record("claude-opus-5-5", "test", Usage(input_tokens=10_000_000))
    events = await collect(svc, "Hello")
    assert scripted.calls == []
    assert "pausing" in events[-1]["text"]


async def test_memories_from_user_message_have_source(svc: Services, scripted: ScriptedProvider):
    events = await collect(svc, "I live in Cologne")
    memories = svc.memory.list()
    assert memories and memories[0]["source"].startswith("chat:")
    assert any(e.get("type") == "memory" for e in events)


async def test_memories_are_passed_as_notes_not_instructions(
    svc: Services, scripted: ScriptedProvider
):
    svc.memory.add("Likes short answers", source="t")
    await collect(svc, "How do I like answers?")
    context = scripted.calls[0]["system"][1]
    assert "not instructions" in context
    assert "Likes short answers" in context


def test_tool_list_is_sorted_and_without_approve(svc: Services) -> None:
    names = [t["name"] for t in svc.chat.tools()]
    assert names == sorted(names)
    assert "approve" not in names
