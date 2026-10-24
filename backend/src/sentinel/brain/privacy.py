"""What never goes into the brain: secrets, grades/report cards, addresses, phone numbers.

English patterns plus the German ones, so German files or notes are filtered too.
"""

from __future__ import annotations

import re

from sentinel.secrets_filter import contains_secret, mentions_secret_topic

_SUBJECTS = (
    r"math|maths|english|german|french|spanish|biology|bio|physics|chemistry|history|"
    r"geography|computer\s+science|art|music|religion|ethics|politics|"
    r"mathe|deutsch|englisch|französisch|physik|chemie|geschichte|informatik|sport|kunst|"
    r"musik|ethik|erdkunde|politik"
)
_GRADES = re.compile(
    r"\b(?:grades?|gpa|report\s+cards?|transcript\s+of\s+records|transcripts?|"
    r"zeugnis\w*|noten(?:schnitt|spiegel|durchschnitt)?|schulnote\w*|klausurnote\w*|"
    r"halbjahresinfo\w*)\b"
    r"|\b(?:got|get|received|scored)\s+(?:an?\s+)?[A-F][+-]?\s+(?:in|on)\b"
    r"|\bnote\s*[:=]?\s*[1-6](?:[,.]\d)?\b"
    r"|\b(?:eine|ne|die|'?ne)\s+[1-6][+-]?\s+(?:in|im|auf|bekommen|geschrieben)\b"
    rf"|\b[1-6](?:[,.]\d)?\s+(?:in|im)\s+(?:{_SUBJECTS})\b",
    re.IGNORECASE,
)
# "12 Main Street", "221b Baker St." (case-sensitive, so "Python 3 is the way" stays allowed)
_STREET = re.compile(
    r"\b\d{1,5}[a-z]?\s+(?:[A-Z][\w\-]*\s+){1,3}"
    r"(?:Street|St\.|Avenue|Ave\.|Road|Rd\.|Lane|Drive|Boulevard|Blvd\.|Way|Court|Place|Square)"
    r"(?=\W|$)"
)
_ADDRESS = re.compile(
    # German: "Musterstraße 12", "12345 Musterstadt"
    r"\b[\wäöüß\-]+(?:straße|strasse|str\.|weg|allee|platz|gasse|ring|damm|ufer|chaussee)"
    r"\s*\d{1,4}\s?[a-z]?\b"
    r"|\b\d{5}\s+[A-ZÄÖÜ][a-zäöüß]+"
    r"|\b(?:lives?\s+at|(?<!ip\s)(?<!mac\s)(?<!e-mail\s)(?<!email\s)address|wohnt|wohne|"
    r"adresse|anschrift)\b.{0,40}\d",
    re.IGNORECASE,
)
_PHONE = re.compile(
    r"(?:\+|\b00)\d{1,3}[\s/\-]?\d[\d\s/\-]{6,}\d|\b0\d{2,5}[\s/\-]?\d[\d\s/\-]{4,}\d"
    r"|\(\d{3}\)\s?\d{3}[\s\-]\d{4}\b"
)
_EMAIL = re.compile(r"\b[\w.+\-]+@[\w\-]+\.[\w.\-]+\b")


def blocked_reason(text: str) -> str | None:
    """Reason why a text is not stored – or None."""
    if contains_secret(text) or mentions_secret_topic(text):
        return "passwords, keys and tokens never go into the brain"
    if _GRADES.search(text):
        return "grades and report cards don't go into the brain"
    if any(p.search(text) for p in (_ADDRESS, _STREET, _PHONE, _EMAIL)):
        return "addresses, phone numbers and e-mail addresses don't go into the brain"
    return None


def scrub(text: str) -> str:
    """Removes phone numbers, e-mail and street addresses from raw text."""
    text = _PHONE.sub("[number]", text)
    text = _EMAIL.sub("[e-mail]", text)
    text = _STREET.sub("[address]", text)
    return _ADDRESS.sub("[address]", text)
