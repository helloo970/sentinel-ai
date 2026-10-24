"""Parcel tracking from Gmail: find shipping mails, detect tracking number and status.

Security:
- Gmail is only read read-only (like mail.service).
- Links from mails are never opened or displayed. The tracking link is built from the
  detected number and a fixed template of the carrier.
- Subject and sender are treated as untrusted and only displayed.

Shipping mails from German carriers are German, so German patterns are kept next to the
English ones.
"""

from __future__ import annotations

import email
import email.policy
import email.utils
import html
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from sentinel.mail.service import decode

SEARCH = (
    '"newer_than:21d (Sendungsnummer OR Sendungsverfolgung OR Trackingnummer OR '
    "tracking OR shipped OR shipment OR delivery OR parcel OR package OR "
    'versandt OR verschickt OR Zustellung OR Paket OR Lieferung)"'
)
MAX_MAILS = 25
MAX_BODY = 200_000


@dataclass(frozen=True)
class Carrier:
    key: str
    name: str
    hints: tuple[str, ...]
    number: re.Pattern[str]
    url: str


CARRIERS = (
    Carrier(
        "ups",
        "UPS",
        ("ups",),
        re.compile(r"\b(1Z[0-9A-Z]{16})\b"),
        "https://www.ups.com/track?loc=de_DE&tracknum={n}",
    ),
    Carrier(
        "amazon",
        "Amazon Logistics",
        ("amazon",),
        re.compile(r"\b(TBA\d{12})\b"),
        "https://track.amazon.de/tracking/{n}",
    ),
    Carrier(
        "dhl",
        "DHL",
        ("dhl", "deutsche post"),
        re.compile(r"\b(JJD\d{18,20}|\d{20}|\d{12})\b"),
        "https://www.dhl.de/de/privatkunden/pakete-empfangen/verfolgen.html?piececode={n}",
    ),
    Carrier(
        "hermes",
        "Hermes",
        ("hermes",),
        re.compile(r"\b(H\d{19}|\d{14}|\d{16})\b"),
        "https://www.myhermes.de/empfangen/sendungsverfolgung/sendungsinformation#{n}",
    ),
    Carrier(
        "dpd",
        "DPD",
        ("dpd",),
        re.compile(r"\b(0\d{13})\b"),
        "https://tracking.dpd.de/status/de_DE/parcel/{n}",
    ),
    Carrier(
        "gls",
        "GLS",
        ("gls",),
        re.compile(r"\b([0-9A-Z]{11,12})\b"),
        "https://gls-group.com/DE/de/paketverfolgung?match={n}",
    ),
)
_BY_KEY = {c.key: c for c in CARRIERS}
# Known tracking links: take the number straight from the URL (the link itself is discarded)
_URL_NUMBERS = (
    ("dhl", re.compile(r"dhl\.de/\S*?(?:piececode|idc)=([0-9A-Z]{10,22})", re.I)),
    ("hermes", re.compile(r"hermes\S*?sendungsinformation#?([0-9A-Z]{14,20})", re.I)),
    ("dpd", re.compile(r"dpd\.\w+/\S*?parcel/(\d{14})", re.I)),
    ("gls", re.compile(r"gls\S*?match=([0-9A-Z]{11,12})", re.I)),
    ("ups", re.compile(r"ups\.com/\S*?tracknum=(1Z[0-9A-Z]{16})", re.I)),
)
STATUS_RULES = (
    ("delivered", re.compile(r"zugestellt|geliefert|delivered|abgegeben|has been delivered", re.I)),
    (
        "pickup",
        re.compile(
            r"abholbereit|packstation|filiale|paketshop|abholung|"
            r"ready for pick-?up|pickup point|parcel locker",
            re.I,
        ),
    ),
    (
        "today",
        re.compile(r"heute|in zustellung|out for delivery|arriving today|unterwegs zu dir", re.I),
    ),
    (
        "shipped",
        re.compile(
            r"versandt|verschickt|unterwegs|shipped|on its way|on the way|auf dem weg", re.I
        ),
    ),
)
_TAGS = re.compile(r"<[^>]+>")
_HREF = re.compile(r"href\s*=\s*[\"']([^\"']{1,500})[\"']", re.I)
_STYLE = re.compile(r"<(style|script)[^>]*>.*?</\1>", re.I | re.S)


def tracking_url(carrier: str, number: str) -> str:
    return _BY_KEY[carrier].url.format(n=quote(number, safe=""))


def message_text(raw: bytes) -> str:
    """Text from a (possibly truncated) mail, HTML without tags."""
    try:
        message = email.message_from_bytes(raw[:MAX_BODY], policy=email.policy.default)
    except Exception:
        return ""
    parts: list[str] = []
    for part in message.walk():
        kind = part.get_content_type()
        if kind not in ("text/plain", "text/html"):
            continue
        try:
            content = part.get_content()
        except Exception:  # noqa: S112 - skip broken mail parts
            continue
        if not isinstance(content, str):
            continue
        if kind == "text/html":
            # Link targets only to detect tracking numbers, never to open them
            links = " ".join(html.unescape(h) for h in _HREF.findall(content)[:200])
            content = html.unescape(_TAGS.sub(" ", _STYLE.sub(" ", content))) + " " + links
        parts.append(content)
    return " ".join(parts)[:MAX_BODY]


def detect(sender: str, subject: str, body: str) -> list[tuple[str, str]]:
    """Found (carrier, number) pairs, most reliable matches first."""
    found: list[tuple[str, str]] = []
    for key, pattern in _URL_NUMBERS:
        for number in pattern.findall(body):
            found.append((key, number.upper()))
    context = f"{sender} {subject} {body[:3000]}".lower()
    for carrier in CARRIERS:
        if not any(hint in context for hint in carrier.hints):
            continue
        for number in carrier.number.findall(f"{subject} {body}"):
            if carrier.key == "gls" and not re.search(r"\d", number):
                continue
            found.append((carrier.key, number.upper()))
    unique = list(dict.fromkeys(found))
    return unique[:3]


def status_of(subject: str) -> str:
    for status, pattern in STATUS_RULES:
        if pattern.search(subject):
            return status
    return "unknown"


def parse_parcel_mail(raw: bytes) -> list[dict[str, Any]]:
    try:
        message = email.message_from_bytes(raw[:MAX_BODY])
    except Exception:
        return []
    subject = decode(message.get("Subject"))[:200]
    name, address = email.utils.parseaddr(decode(message.get("From")))
    try:
        date = email.utils.parsedate_to_datetime(message.get("Date", "")).timestamp()
    except (TypeError, ValueError):
        date = None
    body = message_text(raw)
    return [
        {
            "carrier": carrier,
            "carrier_name": _BY_KEY[carrier].name,
            "number": number,
            "status": status_of(subject),
            "subject": subject,
            "sender": (name or address)[:80],
            "date": date,
            "tracking_url": tracking_url(carrier, number),
        }
        for carrier, number in detect(f"{name} {address}", subject, body)
    ]


def merge(parcels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Only the newest message per tracking number."""
    latest: dict[str, dict[str, Any]] = {}
    for parcel in parcels:
        key = f"{parcel['carrier']}|{parcel['number']}"
        if key not in latest or (parcel["date"] or 0) > (latest[key]["date"] or 0):
            latest[key] = parcel
    order = {"today": 0, "pickup": 1, "shipped": 2, "unknown": 3, "delivered": 4}
    return sorted(latest.values(), key=lambda p: (order[p["status"]], -(p["date"] or 0)))
