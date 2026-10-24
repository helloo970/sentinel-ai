"""Neural Microsoft voice via edge-tts (default: an English-language voice).

The text to be spoken is sent to Microsoft for this (as with Read Aloud in Edge). Secrets
are removed first. Without internet SENTINEL falls back to Piper or the system voice.
"""

from __future__ import annotations

import hashlib
import logging
from collections import OrderedDict

from sentinel.secrets_filter import redact

log = logging.getLogger(__name__)

MAX_CHARS = 1500
CACHE_SIZE = 40


class EdgeVoice:
    def __init__(self, voice: str, rate: str = "+0%", pitch: str = "-6Hz") -> None:
        self.voice = voice
        self.rate = rate
        self.pitch = pitch
        self._cache: OrderedDict[str, bytes] = OrderedDict()

    @staticmethod
    def available() -> bool:
        import importlib.util

        return importlib.util.find_spec("edge_tts") is not None

    async def synthesize(self, text: str) -> bytes:
        """Text -> MP3. Raises RuntimeError if the service is unreachable."""
        clean = redact(text).strip()[:MAX_CHARS]
        if not clean:
            return b""
        key = hashlib.sha256(f"{self.voice}|{self.rate}|{self.pitch}|{clean}".encode()).hexdigest()
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        import edge_tts

        audio = bytearray()
        try:
            communicate = edge_tts.Communicate(clean, self.voice, rate=self.rate, pitch=self.pitch)
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio":
                    audio.extend(chunk["data"])
        except Exception as exc:
            log.warning("edge-tts failed: %s", type(exc).__name__)
            raise RuntimeError("Online voice unreachable") from exc
        data = bytes(audio)
        self._cache[key] = data
        while len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)
        return data
