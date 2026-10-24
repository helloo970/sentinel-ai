"""Local detection of the emergency-stop command ("SENTINEL, stop")."""

from __future__ import annotations

import re
import unicodedata

_STOP = re.compile(
    r"\b(sentinel|sentinell|sentinal|jarvis)\b[\s,.!:;-]*"
    r"(stopp|stop|halt|emergency stop|not ?aus|notaus)\b"
)
_ALONE = re.compile(r"^(not ?aus|notaus|emergency stop)$")


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = text.replace("-", " ")
    return re.sub(r"\s+", " ", text).strip(" .!?,")


def is_stop_command(text: str) -> bool:
    norm = _normalize(text)
    return bool(_STOP.search(norm) or _ALONE.match(norm))
