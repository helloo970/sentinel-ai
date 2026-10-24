"""ElevenLabs voice: request, secrets removed, cache, errors, fallback to Microsoft."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from sentinel.services import Services
from sentinel.voice import elevenlabs as el

KEY = "sk_test_not_real_0000000000000000"


def voice_with(tmp_path: Path, handler) -> tuple[el.ElevenLabsVoice, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    el._keyring().set_password(*el.KEYRING, KEY)
    client = httpx.AsyncClient(transport=httpx.MockTransport(wrapped))
    return el.ElevenLabsVoice(tmp_path, client=client), seen


async def test_synthesize_request_and_cache(tmp_path: Path) -> None:
    voice, seen = voice_with(tmp_path, lambda r: httpx.Response(200, content=b"ID3mp3"))
    secret = "Here is my key sk-ant-api03-" + "z" * 40
    assert await voice.synthesize(secret) == b"ID3mp3"
    assert await voice.synthesize(secret) == b"ID3mp3"
    assert len(seen) == 1  # second time from the cache
    request = seen[0]
    assert request.url.path == f"/v1/text-to-speech/{el.DEFAULT_VOICE}"
    assert request.headers["xi-api-key"] == KEY and KEY not in str(request.url)
    body = json.loads(request.content)
    assert body["model_id"] == el.DEFAULT_MODEL and "sk-ant" not in body["text"]


async def test_voice_choice_is_used(tmp_path: Path) -> None:
    el.save_choice(tmp_path, voice_id="abc123")
    voice, seen = voice_with(tmp_path, lambda r: httpx.Response(200, content=b"x"))
    await voice.synthesize("Hello")
    assert seen[0].url.path == "/v1/text-to-speech/abc123"


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (401, {"detail": {"status": "quota_exceeded"}}, "quota"),
        (401, {"detail": {"status": "invalid_api_key"}}, "rejected"),
        (404, {}, "does not exist"),
        (429, {}, "Too many"),
    ],
)
async def test_errors(tmp_path: Path, status: int, body: dict, message: str) -> None:
    voice, _ = voice_with(tmp_path, lambda r: httpx.Response(status, json=body))
    with pytest.raises(el.ElevenLabsError, match=message):
        await voice.synthesize("Hello")
    assert message in (voice.last_error or "")


async def test_voices_list(tmp_path: Path) -> None:
    data = {
        "voices": [
            {"voice_id": "v1", "name": "Daniel", "labels": {"gender": "male", "accent": "british"}}
        ]
    }
    voice, _ = voice_with(tmp_path, lambda r: httpx.Response(200, json=data))
    assert await voice.voices() == [{"id": "v1", "name": "Daniel", "info": "male, british"}]


def test_tts_route_falls_back_to_microsoft(svc: Services, tmp_path: Path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from sentinel.api.app import create_app

    el._keyring().set_password(*el.KEYRING, KEY)

    async def broken(text: str) -> bytes:
        raise el.ElevenLabsError("quota used up")

    async def edge(text: str) -> bytes:
        return b"EDGE"

    monkeypatch.setattr(svc.eleven, "synthesize", broken)
    monkeypatch.setattr(svc.edge_voice, "synthesize", edge)
    monkeypatch.setattr(svc.edge_voice, "available", lambda: True)
    client = TestClient(
        create_app(svc, token="t-1", start_background=False), base_url="http://127.0.0.1:8765"
    )
    resp = client.post(
        "/voice/tts", json={"text": "Hello"}, headers={"Authorization": "Bearer t-1"}
    )
    assert resp.status_code == 200 and resp.content == b"EDGE"


def test_status_reports_provider(svc: Services) -> None:
    assert svc.voice.status()["tts_provider"] == "edge"
    el._keyring().set_password(*el.KEYRING, KEY)
    assert svc.voice.status()["tts_provider"] == "elevenlabs"


def stt_with(tmp_path: Path, handler) -> tuple[el.ElevenLabsSTT, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    el._keyring().set_password(*el.KEYRING, KEY)
    client = httpx.Client(transport=httpx.MockTransport(wrapped))
    return el.ElevenLabsSTT(tmp_path, client=client), seen


def test_stt_request(tmp_path: Path) -> None:
    import numpy as np

    stt, seen = stt_with(tmp_path, lambda r: httpx.Response(200, json={"text": " Hello Jarvis "}))
    wav = el.wav_bytes(np.zeros(1600, dtype=np.float32))
    assert wav[:4] == b"RIFF"
    assert (
        stt.transcribe(wav, keyterms=["Luna", "Luna", "x", "too long " * 10, "a[b]"])
        == "Hello Jarvis"
    )
    body = seen[0].content
    assert seen[0].url.path == "/v1/speech-to-text"
    assert seen[0].headers["xi-api-key"] == KEY
    assert b"scribe_v2" in body and b"Luna" in body and b"ab" in body
    assert body.count(b"Luna") == 1


def test_stt_permission_error_falls_back_for_a_while(tmp_path: Path) -> None:
    body = {"detail": {"status": "missing_permissions"}}
    stt, _ = stt_with(tmp_path, lambda r: httpx.Response(401, json=body))
    with pytest.raises(el.ElevenLabsError, match="Speech to Text"):
        stt.transcribe(b"RIFF")
    assert not stt.available


def test_voice_uses_cloud_then_local(svc: Services, monkeypatch) -> None:
    import numpy as np

    el._keyring().set_password(*el.KEYRING, KEY)
    heard: list[str] = []
    monkeypatch.setattr(svc.voice, "handle_transcript", heard.append)
    monkeypatch.setattr(
        svc.voice.cloud_stt, "transcribe", lambda wav, keyterms=None: "from the cloud"
    )
    svc.voice._handle_audio(np.full(16000, 0.1, dtype=np.float32))
    assert heard == ["from the cloud"] and svc.voice.stt_provider == "elevenlabs"

    def broken(wav, keyterms=None):
        raise el.ElevenLabsError("gone")

    class Segment:
        text = "local"

    class Model:
        def transcribe(self, audio, **kwargs):
            assert "Jarvis" in kwargs["initial_prompt"]
            return [Segment()], None

    monkeypatch.setattr(svc.voice.cloud_stt, "transcribe", broken)
    monkeypatch.setattr(svc.voice, "_load_stt", lambda: Model())
    svc.voice._handle_audio(np.full(16000, 0.1, dtype=np.float32))
    assert heard[-1] == "local" and svc.voice.stt_provider == "local"
