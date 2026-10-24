"""OpenRouter provider: translation, streaming with tools, errors – against a fake server."""

from __future__ import annotations

import json

import httpx
import pytest

from sentinel.config import Settings
from sentinel.llm import factory
from sentinel.llm.base import LLMError
from sentinel.llm.openrouter_provider import OpenRouterProvider, to_messages, to_tools

KEY = "sk-or-v1-test-not-real-000000000000"


def sse(*chunks: dict) -> str:
    return (
        "".join(f"data: {json.dumps(c)}\n\n" for c in chunks)
        + ": OPENROUTER PROCESSING\n\ndata: [DONE]\n\n"
    )


def provider_with(handler, **kw) -> tuple[OpenRouterProvider, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(wrapped))
    provider = OpenRouterProvider(
        api_key=KEY, model="anthropic/claude-opus-5.5", client=client, **kw
    )
    return provider, seen


def test_message_translation() -> None:
    messages = [
        {"role": "user", "content": "Open Gmail"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "On it.", "reasoning_details": [{"type": "x"}]},
                {"type": "tool_use", "id": "c1", "name": "open_site", "input": {"name": "gmail"}},
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "ok"}],
        },
    ]
    out = to_messages("sys", messages)
    assert [m["role"] for m in out] == ["system", "user", "assistant", "tool"]
    assistant = out[2]
    assert assistant["tool_calls"][0]["function"] == {
        "name": "open_site",
        "arguments": '{"name": "gmail"}',
    }
    assert assistant["reasoning_details"] == [{"type": "x"}]
    assert out[3] == {"role": "tool", "tool_call_id": "c1", "content": "ok"}


def test_tool_schema() -> None:
    tools = to_tools([{"name": "x", "description": "d", "input_schema": {"type": "object"}}])
    assert tools[0]["type"] == "function" and tools[0]["function"]["parameters"]["type"] == "object"


async def test_streaming_text_usage_and_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["stream"] is True and body["reasoning"] == {"effort": "low"}
        return httpx.Response(
            200,
            text=sse(
                {"choices": [{"delta": {"content": "Hal"}}]},
                {"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]},
                {"choices": [], "usage": {"prompt_tokens": 30, "completion_tokens": 4}},
            ),
        )

    provider, seen = provider_with(handler)
    streamed: list[str] = []

    async def on_text(text: str) -> None:
        streamed.append(text)

    result = await provider.turn(
        system=["s"],
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        allow_tools=True,
        on_text=on_text,
    )
    assert result.text == "Hallo" and streamed == ["Hal", "lo"] and result.stop_reason == "end_turn"
    assert (result.usage.input_tokens, result.usage.output_tokens) == (30, 4)
    assert seen[0].headers["authorization"] == f"Bearer {KEY}"
    assert KEY not in str(seen[0].url)


async def test_streamed_tool_call_is_assembled() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["tool_choice"] == "none"
        return httpx.Response(
            200,
            text=sse(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "function": {"name": "open_", "arguments": '{"na'},
                                    }
                                ]
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {"name": "site", "arguments": 'me": "gmail"}'},
                                    }
                                ]
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {"reasoning_details": [{"type": "r"}]},
                            "finish_reason": "tool_calls",
                        }
                    ]
                },
            ),
        )

    provider, _ = provider_with(handler)
    tools = [{"name": "open_site", "description": "", "input_schema": {"type": "object"}}]
    result = await provider.turn(
        system=["s"], messages=[{"role": "user", "content": "x"}], tools=tools, allow_tools=False
    )
    use = result.tool_uses[0]
    assert result.stop_reason == "tool_use"
    assert (use["id"], use["name"], use["input"]) == ("call_1", "open_site", {"name": "gmail"})


async def test_broken_tool_arguments_become_none() -> None:
    provider, _ = provider_with(
        lambda r: httpx.Response(
            200,
            text=sse(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "c",
                                        "function": {"name": "x", "arguments": "{kaputt"},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ),
        )
    )
    result = await provider.turn(
        system=["s"], messages=[{"role": "user", "content": "x"}], tools=[], allow_tools=True
    )
    assert result.tool_uses[0]["input"] is None  # the chat rejects this cleanly


async def test_reasoning_can_be_enabled() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["reasoning"] == {"enabled": True}
        return httpx.Response(200, text=sse({"choices": [{"delta": {"content": "ok"}}]}))

    provider, _ = provider_with(handler, reasoning=True)
    await provider.complete(system="s", prompt="p")


@pytest.mark.parametrize(
    ("status", "message"),
    [(401, "key"), (402, "credits"), (404, "model"), (429, "Too many"), (418, "418")],
)
async def test_errors(status: int, message: str) -> None:
    provider, _ = provider_with(lambda r: httpx.Response(status, json={"error": {"message": "x"}}))
    with pytest.raises(LLMError, match=message) as info:
        await provider.complete(system="s", prompt="p")
    assert KEY not in str(info.value)


async def test_overload_is_retried(monkeypatch) -> None:
    from sentinel.llm import openrouter_provider

    monkeypatch.setattr(openrouter_provider, "RETRY_DELAYS_S", (0, 0))
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, text=sse({"choices": [{"delta": {"content": "OK"}}]}))

    provider, _ = provider_with(handler)
    assert (await provider.complete(system="s", prompt="p")).text == "OK"


async def test_stream_error_chunk() -> None:
    provider, _ = provider_with(
        lambda r: httpx.Response(200, text=sse({"error": {"message": "Provider returned error"}}))
    )
    with pytest.raises(LLMError, match="Provider returned error"):
        await provider.complete(system="s", prompt="p")


def test_factory_prefers_openrouter_over_gemini(tmp_path) -> None:
    both = Settings(  # type: ignore[call-arg]
        _env_file=None, data_dir=tmp_path / "d", GEMINI_API_KEY="AQ.x", OPENROUTER_API_KEY=KEY
    )
    assert factory.create_provider(both).name == "openrouter"
    factory._keyring().set_password(*factory.OPENROUTER_KEYRING, KEY)
    from tests.conftest import make_settings

    assert factory.create_provider(make_settings(tmp_path)).name == "openrouter"


def test_model_choice_file_overrides_env(tmp_path) -> None:
    from tests.conftest import make_settings

    config = make_settings(tmp_path)
    assert factory.chosen_model(config) == config.openrouter_model
    factory.save_model(config, "minimax/minimax-m3.1-flash:free")
    assert factory.chosen_model(config) == "minimax/minimax-m3.1-flash:free"


def test_free_models_cost_nothing() -> None:
    from sentinel.llm.base import Usage
    from sentinel.llm.costs import estimate_eur

    assert estimate_eur("minimax/minimax-m3.1-flash:free", Usage(10_000, 2_000), 0.92) == 0.0
    assert estimate_eur("anthropic/claude-opus-5.5", Usage(1_000_000, 0), 1.0) == 4.0
