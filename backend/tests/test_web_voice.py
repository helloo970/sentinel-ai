"""Opening sites, searching, app views, voice switch and online voice."""

from __future__ import annotations

import pytest

from sentinel.actions import web as web_actions
from sentinel.chat import quick
from sentinel.permissions import Source
from sentinel.services import Services


@pytest.fixture
def opened(monkeypatch) -> list[str]:
    urls: list[str] = []
    monkeypatch.setattr(web_actions.webbrowser, "open", lambda url, new=0: urls.append(url) or True)
    return urls


def run(svc: Services, action: str, params: dict, source: Source = Source.ASSISTANT):
    return svc.engine.request(action, params, source=source, actor="jarvis")


def test_open_known_site(svc: Services, opened: list[str]) -> None:
    assert run(svc, "open_site", {"name": "Google Mail"}).status == "executed"
    assert opened == ["https://mail.google.com/"]


@pytest.mark.parametrize("name", ["evil.example", "https://evil.example", "phishing"])
def test_unknown_site_denied(svc: Services, opened: list[str], name: str) -> None:
    assert run(svc, "open_site", {"name": name}).status == "denied"
    assert opened == []


def test_search_builds_url_itself(svc: Services, opened: list[str]) -> None:
    run(svc, "web_search", {"query": "Iron Man & Jarvis", "engine": "youtube"})
    assert opened == ["https://www.youtube.com/results?search_query=Iron+Man+%26+Jarvis"]
    assert run(svc, "web_search", {"query": "x", "engine": "evil"}).status == "denied"


def test_search_blocks_secrets(svc: Services, opened: list[str]) -> None:
    outcome = run(svc, "web_search", {"query": "sk-ant-api03-" + "x" * 40})
    assert outcome.status == "denied" and opened == []


def test_untrusted_source_cannot_open(svc: Services, opened: list[str]) -> None:
    outcome = run(svc, "open_site", {"name": "gmail"}, source=Source.UNTRUSTED)
    assert outcome.status == "denied" and opened == []


def test_open_view_publishes_navigation(svc: Services, monkeypatch) -> None:
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(svc.bus, "publish", lambda kind, data: events.append((kind, data)))
    assert run(svc, "open_view", {"view": "security", "tab": "tests"}).status == "executed"
    assert ("navigate", {"view": "security", "tab": "tests", "title": "Security Center"}) in events
    assert run(svc, "open_view", {"view": "hacker"}).status == "denied"


@pytest.mark.parametrize(
    ("text", "action", "params"),
    [
        ("Jarvis, open Gmail", "open_site", {"name": "gmail"}),
        ("open YouTube", "open_site", {"name": "youtube"}),
        ("show me the Security Center", "open_view", {"view": "security"}),
        ("open the alert test", "open_view", {"view": "security", "tab": "tests"}),
        ("go to settings", "open_view", {"view": "settings"}),
        ("start Steam", "launch_program", {"name": "steam"}),
        (
            "search for weather Berlin",
            "web_search",
            {"query": "weather Berlin", "engine": "google"},
        ),
        ("search YouTube for Lo-Fi", "web_search", {"query": "Lo-Fi", "engine": "youtube"}),
        (
            "play Imagine Dragons on YouTube",
            "web_search",
            {"query": "Imagine Dragons", "engine": "youtube"},
        ),
        ("start focus mode", "start_mode", {"name": "fokus"}),
    ],
)
def test_voice_quick_commands(text: str, action: str, params: dict) -> None:
    command = quick.match(text)
    assert command is not None and (command.action, command.params) == (action, params)


@pytest.mark.parametrize("text", ["please open the door to success for me", "What time is it?"])
def test_unknown_requests_go_to_jarvis(text: str) -> None:
    assert quick.match(text) is None


def test_voice_toggle_persists(svc: Services, monkeypatch) -> None:
    # Never open the real microphone or load models
    monkeypatch.setattr(svc.voice, "_open_stream", lambda: None)
    monkeypatch.setattr(svc.voice, "_preload_stt", lambda: None)
    assert svc.voice.enabled is False
    status = svc.voice.set_enabled(True)
    assert status["enabled"] is True and svc.voice.enabled is True
    svc.voice.set_enabled(False)
    assert svc.voice.enabled is False
    actions = [r["action"] for r in svc.db.query("SELECT action FROM audit")]
    assert actions.count("voice_toggle") == 2


async def test_edge_voice_redacts_and_caches(monkeypatch) -> None:
    import sys
    import types

    from sentinel.voice.edge_tts_engine import EdgeVoice

    spoken: list[str] = []

    class FakeCommunicate:
        def __init__(self, text: str, voice: str, rate: str, pitch: str) -> None:
            spoken.append(text)

        async def stream(self):
            yield {"type": "audio", "data": b"ID3"}
            yield {"type": "WordBoundary"}

    monkeypatch.setitem(sys.modules, "edge_tts", types.SimpleNamespace(Communicate=FakeCommunicate))
    voice = EdgeVoice("en-AU-WilliamMultilingualNeural")
    secret = "My key is sk-ant-api03-" + "y" * 40
    assert await voice.synthesize(secret) == b"ID3"
    assert await voice.synthesize(secret) == b"ID3"
    assert len(spoken) == 1 and "sk-ant" not in spoken[0]


def test_app_speaking_blocks_listening(svc: Services) -> None:
    svc.voice.set_speaking(True)
    assert svc.voice.state == "speaking"
    svc.voice.set_speaking(False)
    assert svc.voice.state == "idle"


def test_listen_once_needs_active_voice(svc: Services) -> None:
    assert svc.voice.listen_once() is False  # voice off
    svc.voice._stream = object()
    svc.voice.voice_active = True
    assert svc.voice.listen_once() is True and svc.voice._listen.is_set()


def test_two_claps_by_default(svc: Services) -> None:
    assert svc.voice.clap.claps == 2
