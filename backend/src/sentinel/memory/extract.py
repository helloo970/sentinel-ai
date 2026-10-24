"""Automatic remembering from the user's own messages (rule-based, local)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sentinel.secrets_filter import mentions_secret_topic


@dataclass(frozen=True)
class Candidate:
    text: str
    category: str


_NAME = r"([A-Z][\w\-']+(?:\s[A-Z][\w\-']+)?)"

_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    # "Remember: ...", "Jarvis, note that ...", "please keep in mind that ..."
    # (not "do you remember that ...")
    (
        re.compile(
            r"(?:^\W*(?:(?:hey\s+)?jarvis\W+)?(?:please\s+)?(?:remember|note|keep in mind)"
            r"(?:\s+that)?|(?<!you\s)\b(?:remember|note|keep in mind)\s+that)[,:]?\s+(.{3,300})",
            re.I,
        ),
        "note",
        "{0}",
    ),
    # Names must be capitalized, only the phrase itself ignores case
    (re.compile(rf"\b(?i:my name is|call me)\s+{_NAME}"), "person", "Name is {0}"),
    (re.compile(r"\bi live in\s+([\w\- ]{2,60})", re.I), "place", "Lives in {0}"),
    (
        re.compile(r"\bi work (as|at|for|on)\s+([\w\-. ]{2,80})", re.I),
        "work",
        "Works {0} {1}",
    ),
    (
        re.compile(
            r"\bi (?:really\s+|also\s+)?(like|love|hate|prefer|enjoy|dislike|don't like|"
            r"do not like)\s+([\w\-., ]{2,100})",
            re.I,
        ),
        "preference",
        "{0} {1}",
    ),
    (
        re.compile(r"\bmy\s+(favou?rite\s+[\w\-]+)\s+(?:is|are)\s+([\w\-., ]{2,80})", re.I),
        "preference",
        "{0}: {1}",
    ),
]

_VERB_MAP = {
    "like": "Likes",
    "love": "Loves",
    "hate": "Doesn't like",
    "dislike": "Doesn't like",
    "don't like": "Doesn't like",
    "do not like": "Doesn't like",
    "prefer": "Prefers",
    "enjoy": "Enjoys",
}

# Cut a value at the start of the next clause ("I live in Cologne and I work ...")
_CLAUSE = re.compile(r"[.!?\n]|\s+(?:but|because|since)\s+|\s+and\s+i\b", re.I)


def _clean(value: str, *, whole: bool = False) -> str:
    value = re.split(r"[.!?\n]", value)[0] if whole else _CLAUSE.split(value)[0]
    return value.strip(" ,;:")


def extract_candidates(message: str) -> list[Candidate]:
    """Finds statements worth remembering. Anything related to secrets is discarded."""
    if mentions_secret_topic(message):
        return []
    found: list[Candidate] = []
    for pattern, category, template in _PATTERNS:
        for match in pattern.finditer(message):
            groups = [_clean(g, whole=category == "note") for g in match.groups()]
            if category == "preference" and groups and groups[0].lower() in _VERB_MAP:
                groups[0] = _VERB_MAP[groups[0].lower()]
            text = template.format(*groups).strip()
            if len(text) >= 4 and not mentions_secret_topic(text):
                found.append(Candidate(text[0].upper() + text[1:], category))
    return found
