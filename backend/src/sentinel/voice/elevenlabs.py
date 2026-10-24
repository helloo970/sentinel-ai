"""ElevenLabs voice (default: "Daniel", British, with the multilingual Flash model).

The key lives in the Windows credential vault ("sentinel elevenlabs login") and is sent
to ElevenLabs only in the xi-api-key header. Spoken texts have secrets removed first.
If the quota is used up, the Microsoft voice takes over (see api/routes.py).
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import wave
from collections import OrderedDict
from pathlib import Path
from typing import Any

import httpx

from sentinel.secrets_filter import redact

log = logging.getLogger(__name__)

API = "https://api.elevenlabs.io/v1"
KEYRING = ("SentinelAI-ElevenLabs", "api_key")
DEFAULT_VOICE = "onwK4e9ZLuTAKqWW03F9"  # "Daniel" – deep, British
DEFAULT_MODEL = "eleven_flash_v2_5"  # fast, multilingual, half the credits
STT_MODEL = "scribe_v2"
MAX_KEYTERMS = 50
MAX_CHARS = 1500
CACHE_SIZE = 40


class ElevenLabsError(RuntimeError):
    pass


def _keyring() -> Any:
    import keyring

    return keyring


def stored_key() -> str | None:
    try:
        return _keyring().get_password(*KEYRING)
    except Exception:
        return None


def load_choice(config_dir: Path) -> dict[str, str]:
    try:
        data = json.loads((config_dir / "voice.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return (
        {k: str(v) for k, v in data.items() if isinstance(v, str)} if isinstance(data, dict) else {}
    )


def save_choice(config_dir: Path, **values: str) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    data = load_choice(config_dir)
    data.update(values)
    (config_dir / "voice.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


class ElevenLabsVoice:
    def __init__(self, config_dir: Path, client: httpx.AsyncClient | None = None) -> None:
        self.config_dir = config_dir
        self._client = client
        self._cache: OrderedDict[str, bytes] = OrderedDict()
        self.last_error: str | None = None

    @property
    def key(self) -> str | None:
        return stored_key()

    @property
    def configured(self) -> bool:
        return bool(self.key)

    @property
    def voice_id(self) -> str:
        return load_choice(self.config_dir).get("voice_id") or DEFAULT_VOICE

    @property
    def model(self) -> str:
        return load_choice(self.config_dir).get("model") or DEFAULT_MODEL

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0))
        return self._client

    async def synthesize(self, text: str) -> bytes:
        key = self.key
        if not key:
            raise ElevenLabsError("No ElevenLabs key")
        clean = redact(text).strip()[:MAX_CHARS]
        if not clean:
            return b""
        cache_key = hashlib.sha256(f"{self.voice_id}|{self.model}|{clean}".encode()).hexdigest()
        if cache_key in self._cache:
            self._cache.move_to_end(cache_key)
            return self._cache[cache_key]
        try:
            resp = await self._http().post(
                f"{API}/text-to-speech/{self.voice_id}",
                params={"output_format": "mp3_44100_128"},
                headers={"xi-api-key": key, "Content-Type": "application/json"},
                json={
                    "text": clean,
                    "model_id": self.model,
                    "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
                },
            )
        except httpx.HTTPError as exc:
            self.last_error = "ElevenLabs unreachable"
            raise ElevenLabsError(self.last_error) from exc
        if resp.status_code != 200:
            self.last_error = _status_message(resp.status_code, resp.text)
            log.warning("elevenlabs error %s", resp.status_code)
            raise ElevenLabsError(self.last_error)
        self.last_error = None
        data = resp.content
        self._cache[cache_key] = data
        while len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)
        return data

    async def voices(self) -> list[dict[str, str]]:
        key = self.key
        if not key:
            raise ElevenLabsError("No ElevenLabs key")
        try:
            resp = await self._http().get(f"{API}/voices", headers={"xi-api-key": key})
        except httpx.HTTPError as exc:
            raise ElevenLabsError("ElevenLabs unreachable") from exc
        if resp.status_code != 200:
            raise ElevenLabsError(_status_message(resp.status_code, resp.text))
        result = []
        for voice in resp.json().get("voices", []):
            labels = voice.get("labels") or {}
            result.append(
                {
                    "id": str(voice.get("voice_id", "")),
                    "name": str(voice.get("name", ""))[:60],
                    "info": ", ".join(
                        str(labels[k]) for k in ("gender", "accent", "age") if labels.get(k)
                    )[:80],
                }
            )
        return result


def wav_bytes(samples: Any, rate: int = 16000) -> bytes:
    """float32 samples (-1..1) as 16-bit WAV."""
    import numpy as np

    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm)
    return buffer.getvalue()


_KEYTERM_BAD = re.compile(r"[<>{}\[\]\\]")


def clean_keyterms(terms: list[str]) -> list[str]:
    """Terms for recognition: short, without forbidden characters, without duplicates."""
    seen: dict[str, str] = {}
    for term in terms:
        term = _KEYTERM_BAD.sub("", term).strip()
        if 2 <= len(term) < 50 and len(term.split()) <= 5:
            seen.setdefault(term.lower(), term)
    return list(seen.values())[:MAX_KEYTERMS]


class ElevenLabsSTT:
    """Speech recognition with ElevenLabs Scribe (runs in the voice thread, hence synchronous)."""

    def __init__(self, config_dir: Path, client: httpx.Client | None = None) -> None:
        self.config_dir = config_dir
        self._client = client
        self.last_error: str | None = None
        self.disabled_until = 0.0

    @property
    def model(self) -> str:
        return load_choice(self.config_dir).get("stt_model") or STT_MODEL

    @property
    def available(self) -> bool:
        import time

        return bool(stored_key()) and time.time() >= self.disabled_until

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0))
        return self._client

    def transcribe(self, wav: bytes, *, keyterms: list[str] | None = None) -> str:
        import time

        key = stored_key()
        if not key:
            raise ElevenLabsError("No ElevenLabs key")
        data: dict[str, Any] = {"model_id": self.model, "language_code": "en"}
        terms = clean_keyterms(keyterms or [])
        if terms:
            data["keyterms"] = terms
        try:
            resp = self._http().post(
                f"{API}/speech-to-text",
                headers={"xi-api-key": key},
                data=data,
                files={"file": ("speech.wav", wav, "audio/wav")},
            )
        except httpx.HTTPError as exc:
            self.last_error = "ElevenLabs unreachable"
            self.disabled_until = time.time() + 60
            raise ElevenLabsError(self.last_error) from exc
        if resp.status_code != 200:
            self.last_error = _status_message(resp.status_code, resp.text, stt=True)
            # No permission or no credit: recognise locally only for a while
            self.disabled_until = time.time() + (600 if resp.status_code in (401, 402) else 60)
            log.warning("elevenlabs stt error %s", resp.status_code)
            raise ElevenLabsError(self.last_error)
        self.last_error = None
        text = resp.json().get("text", "")
        return str(text).strip()


def _status_message(status: int, text: str, *, stt: bool = False) -> str:
    try:
        detail = json.loads(text).get("detail") or {}
        code = (
            str(detail.get("status") or detail.get("code") or "")
            if isinstance(detail, dict)
            else ""
        )
    except (ValueError, AttributeError):
        code = ""
    if "quota" in code or status == 402:
        return "ElevenLabs quota for this month used up - the Microsoft voice takes over."
    if "permission" in code:
        right = "Speech to Text" if stt else "Text to Speech"
        return (
            f'The ElevenLabs key lacks the "{right}" permission - '
            "enable it in the API key settings."
        )
    if status == 401:
        return f"ElevenLabs key was rejected ({code or 'invalid'})."
    if status == 404:
        return "That ElevenLabs voice does not exist (any more)."
    if status == 429:
        return "Too many requests to ElevenLabs."
    return f"ElevenLabs reported error {status}."
