"""Gemini provider: translation, streaming, tools, errors – against a fake server."""

from __future__ import annotations

import json

import httpx
import pytest

from sentinel.config import Settings
from sentinel.llm import factory
from sentinel.llm.base import LLMError
from sentinel.llm.gemini_provider import GeminiProvider, to_contents, to_declarations
from sentinel.llm.offline import OfflineProvider

KEY = "AQ.test-key-not-real-0000000000"


def sse(*chunks: dict) -> str:
    return "".join(f"data: {json.dumps(c)}\r\n\r\n" for c in chunks)


def provider_with(handler) -> tuple[GeminiProvider, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(wrapped))
    return GeminiProvider(api_key=KEY, model="gemini-flash-latest", client=client), seen


def test_contents_translation_keeps_tool_names_and_signatures() -> None:
    messages = [
        {"role": "user", "content": "Start Steam"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "On it."},
                {
                    "type": "tool_use",
                    "id": "c1",
                    "name": "launch_program",
                    "input": {"name": "steam"},
                    "thought_signature": "sig-1",
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "c1", "content": '{"status": "executed"}'}
            ],
        },
    ]
    contents = to_contents(messages)
    assert [c["role"] for c in contents] == ["user", "model", "user"]
    call = contents[1]["parts"][1]
    assert call["functionCall"] == {"name": "launch_program", "args": {"name": "steam"}}
    assert call["thoughtSignature"] == "sig-1"
    assert contents[2]["parts"][0]["functionResponse"]["name"] == "launch_program"


def test_declarations_use_full_json_schema() -> None:
    schema = {"type": "object", "additionalProperties": False}
    tools = [{"name": "x", "description": "d", "input_schema": schema}]
    assert to_declarations(tools)[0]["parametersJsonSchema"] == schema


async def test_streaming_text_and_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=sse(
                {"candidates": [{"content": {"parts": [{"text": "Hal"}]}}]},
                {"candidates": [{"content": {"parts": [{"thought": True, "text": "intern"}]}}]},
                {
                    "candidates": [
                        {"content": {"parts": [{"text": "lo!"}]}, "finishReason": "STOP"}
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 12,
                        "candidatesTokenCount": 3,
                        "thoughtsTokenCount": 5,
                    },
                },
            ),
        )

    provider, seen = provider_with(handler)
    streamed: list[str] = []

    async def on_text(text: str) -> None:
        streamed.append(text)

    result = await provider.turn(
        system=["sys"],
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        allow_tools=True,
        on_text=on_text,
    )
    assert result.text == "Hallo!" and streamed == ["Hal", "lo!"]
    assert result.stop_reason == "end_turn"
    assert (result.usage.input_tokens, result.usage.output_tokens) == (12, 8)
    assert KEY not in str(seen[0].url)  # key only in the header
    assert seen[0].headers["x-goog-api-key"] == KEY


async def test_function_call_and_tool_lock() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["toolConfig"]["functionCallingConfig"]["mode"] == "NONE"
        part = {
            "functionCall": {"name": "launch_program", "args": {"name": "steam"}},
            "thoughtSignature": "abc",
        }
        return httpx.Response(
            200, text=sse({"candidates": [{"content": {"parts": [part]}, "finishReason": "STOP"}]})
        )

    provider, _ = provider_with(handler)
    tools = [{"name": "launch_program", "description": "", "input_schema": {"type": "object"}}]
    result = await provider.turn(
        system=["s"], messages=[{"role": "user", "content": "x"}], tools=tools, allow_tools=False
    )
    assert result.stop_reason == "tool_use"
    use = result.tool_uses[0]
    assert use["name"] == "launch_program" and use["input"] == {"name": "steam"}
    assert use["id"].startswith("call_") and use["thought_signature"] == "abc"


async def test_safety_block_is_refusal() -> None:
    provider, _ = provider_with(
        lambda r: httpx.Response(200, text=sse({"promptFeedback": {"blockReason": "SAFETY"}}))
    )
    assert (await provider.complete(system="s", prompt="p")).stop_reason == "refusal"


async def test_json_mode() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        config = json.loads(request.content)["generationConfig"]
        assert config["responseMimeType"] == "application/json"
        assert config["responseJsonSchema"] == {"type": "object"}
        return httpx.Response(
            200, text=sse({"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})
        )

    provider, _ = provider_with(handler)
    await provider.complete(system="s", prompt="p", json_schema={"type": "object"})


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (401, {"error": {"status": "UNAUTHENTICATED"}}, "key"),
        (403, {"error": {"status": "PERMISSION_DENIED"}}, "key"),
        (404, {}, "model"),
        (429, {}, "quota"),
        (502, {}, "502"),
    ],
)
async def test_errors_are_translated(status: int, body: dict, message: str) -> None:
    provider, _ = provider_with(lambda r: httpx.Response(status, json=body))
    with pytest.raises(LLMError, match=message) as info:
        await provider.complete(system="s", prompt="p")
    assert KEY not in str(info.value)


async def test_research_uses_google_search_only() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["tools"] == [{"google_search": {}}]
        assert "otto.de" in body["systemInstruction"]["parts"][0]["text"]
        return httpx.Response(
            200, text=sse({"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})
        )

    provider, _ = provider_with(handler)
    await provider.research(system="s", prompt="p", allowed_domains=["otto.de"])


def test_factory_order(tmp_path) -> None:
    from tests.conftest import make_settings

    assert isinstance(factory.create_provider(make_settings(tmp_path)), OfflineProvider)
    gem = Settings(_env_file=None, data_dir=tmp_path / "d", GEMINI_API_KEY=KEY)  # type: ignore[call-arg]
    assert factory.create_provider(gem).name == "gemini"
    both = Settings(  # type: ignore[call-arg]
        _env_file=None, data_dir=tmp_path / "d", GEMINI_API_KEY=KEY, ANTHROPIC_API_KEY="sk-test"
    )
    assert factory.create_provider(both).name == "anthropic"


def test_factory_reads_key_from_keyring(tmp_path) -> None:
    from tests.conftest import make_settings

    factory._keyring().set_password(*factory.GEMINI_KEYRING, KEY)
    assert factory.create_provider(make_settings(tmp_path)).name == "gemini"


@pytest.fixture
def no_wait(monkeypatch) -> None:
    from sentinel.llm import gemini_provider

    monkeypatch.setattr(gemini_provider, "RETRY_DELAYS_S", (0, 0))


async def test_overload_is_retried(no_wait) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}})
        return httpx.Response(
            200, text=sse({"candidates": [{"content": {"parts": [{"text": "OK"}]}}]})
        )

    provider, _ = provider_with(handler)
    result = await provider.complete(system="s", prompt="p")
    assert result.text == "OK" and calls["n"] == 3


async def test_overload_gives_clear_message(no_wait) -> None:
    provider, seen = provider_with(lambda r: httpx.Response(503, json={}))
    with pytest.raises(LLMError, match="overloaded"):
        await provider.complete(system="s", prompt="p")
    assert len(seen) == 3


async def test_timeout_gives_quota_hint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    provider, _ = provider_with(handler)
    with pytest.raises(LLMError, match="free quota"):
        await provider.complete(system="s", prompt="p")
