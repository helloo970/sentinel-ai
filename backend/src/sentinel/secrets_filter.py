"""Detects and redacts secrets (passwords, keys, tokens)."""

from __future__ import annotations

import math
import re
from collections import Counter

REDACTED = "[REDACTED]"

_TOKEN_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(-----END [A-Z ]*PRIVATE KEY-----|$)"),
    re.compile(r"\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=\-]{12,}", re.IGNORECASE),
]

# English and German keywords (German stays so German text is still caught)
_KEYWORDS = (
    r"passw(?:or)?t|password|passwd|pwd|passcode|passphrase|kennwort|pin|puk|token|secret|"
    r"geheimnis|api[ _\-]?key|api[ _\-]?schlüssel|schlüssel|private[ _\-]?key|"
    r"access[ _\-]?key|zugangsdaten|login[ _\-]?(?:details|data)|credentials?|tan|iban|"
    r"kreditkarte|credit[ _\-]?card|card[ _\-]?number|cvv|cvc|security[ _\-]?code"
)

# "password: abc", "token=xyz", "my password is abc", "mein Passwort ist abc"
_ASSIGNMENT = re.compile(
    rf"\b({_KEYWORDS})\b\s*(?:[:=]|is|are|ist|lautet|heißt)\s*[\"']?([^\s\"',;]{{3,}})",
    re.IGNORECASE,
)
_KEYWORD_ONLY = re.compile(rf"\b({_KEYWORDS})\b", re.IGNORECASE)
_LONG_TOKEN = re.compile(r"[A-Za-z0-9_\-+/=]{24,}")


def _entropy(value: str) -> float:
    counts = Counter(value)
    total = len(value)
    return -sum(c / total * math.log2(c / total) for c in counts.values())


def _looks_random(value: str) -> bool:
    has_digit = any(c.isdigit() for c in value)
    has_alpha = any(c.isalpha() for c in value)
    return has_digit and has_alpha and _entropy(value) >= 3.8


def contains_secret(text: str) -> bool:
    """True if the text probably contains a secret."""
    if any(p.search(text) for p in _TOKEN_PATTERNS):
        return True
    if _ASSIGNMENT.search(text):
        return True
    return any(_looks_random(m.group(0)) for m in _LONG_TOKEN.finditer(text))


def mentions_secret_topic(text: str) -> bool:
    """True if the text talks about secrets (too risky for memory)."""
    return bool(_KEYWORD_ONLY.search(text)) or contains_secret(text)


def redact(text: str) -> str:
    """Replaces detected secrets with [REDACTED]."""
    for pattern in _TOKEN_PATTERNS:
        text = pattern.sub(REDACTED, text)
    text = _ASSIGNMENT.sub(lambda m: f"{m.group(1)}: {REDACTED}", text)
    return _LONG_TOKEN.sub(lambda m: REDACTED if _looks_random(m.group(0)) else m.group(0), text)
