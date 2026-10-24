"""Read Gmail: unread and important mails (sender and subject only).

Security:
- Sign-in with a Google app password, stored in the Windows Credential Manager
  (keyring) - never in .env, frontend, log or memory.
- Mailbox opened read-only (EXAMINE), headers via BODY.PEEK: nothing is marked as read,
  nothing is sent, moved or deleted.
- Sender and subject come from strangers and are untrusted for the AI.
"""

from __future__ import annotations

import contextlib
import email
import email.utils
import imaplib
import re
import ssl
import time
from email.header import decode_header, make_header
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sentinel.services import Services

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
KEYRING_SERVICE = "SentinelAI-Gmail"
CACHE_S = 300
MAX_MAILS = 15
ADDRESS_RE = re.compile(
    r"^[^@\s]{1,64}@(gmail\.com|googlemail\.com|[^@\s]{1,190}\.[a-z]{2,})$", re.IGNORECASE
)


class MailError(RuntimeError):
    pass


def _keyring() -> Any:
    import keyring

    return keyring


def decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        text = str(make_header(decode_header(value)))
    except (ValueError, LookupError):
        text = value
    return re.sub(r"\s+", " ", text).strip()


def parse_headers(raw: bytes, important: bool) -> dict[str, Any]:
    message = email.message_from_bytes(raw)
    name, address = email.utils.parseaddr(decode(message.get("From")))
    try:
        sent = email.utils.parsedate_to_datetime(message.get("Date", "")).timestamp()
    except (TypeError, ValueError, IndexError):
        sent = None
    return {
        "from_name": (name or address)[:80],
        "from_address": address[:120],
        "subject": decode(message.get("Subject"))[:200] or "(no subject)",
        "date": sent,
        "important": important,
    }


def spoken(mails: list[dict[str, Any]]) -> str:
    if not mails:
        return "No unread mails."
    important = [m for m in mails if m["important"]]
    text = f"You have {len(mails)} unread mails"
    text += f", {len(important)} of them important." if important else "."
    top = (important or mails)[:3]
    names = ", ".join(f"{m['from_name']}: {m['subject']}" for m in top)
    return f"{text} For example {names}."


class GmailService:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self._cache: tuple[float, list[dict[str, Any]]] | None = None
        self._parcel_cache: tuple[float, list[dict[str, Any]]] | None = None
        self.error: str | None = None

    # ------------------------------------------------------------------ Credentials

    @property
    def address(self) -> str | None:
        return self.svc.store.get("gmail_address")

    @property
    def configured(self) -> bool:
        if not self.address:
            return False
        try:
            return _keyring().get_password(KEYRING_SERVICE, self.address) is not None
        except Exception:
            return False

    def save_login(self, address: str, app_password: str) -> None:
        """Only via the command line (sentinel gmail login) - never via the API."""
        address = address.strip()
        if not ADDRESS_RE.match(address):
            raise MailError("Invalid email address")
        password = app_password.replace(" ", "").strip()
        if not re.fullmatch(r"[A-Za-z]{16}", password):
            raise MailError("A Google app password has 16 letters")
        self._login(address, password).logout()
        _keyring().set_password(KEYRING_SERVICE, address, password)
        self.svc.store.set("gmail_address", address)
        self.svc.audit.append(
            actor="user",
            source="user",
            action="gmail_login",
            params={"address": address},
            decision="execute",
        )

    def remove_login(self) -> None:
        address = self.address
        if address:
            with contextlib.suppress(Exception):
                _keyring().delete_password(KEYRING_SERVICE, address)
        self.svc.store.set("gmail_address", "")
        self._cache = None
        self.svc.audit.append(
            actor="user", source="user", action="gmail_logout", decision="execute"
        )

    @staticmethod
    def _login(address: str, password: str) -> imaplib.IMAP4_SSL:
        context = ssl.create_default_context()
        try:
            client = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=context, timeout=15)
            client.login(address, password)
        except imaplib.IMAP4.error as exc:
            raise MailError("Gmail sign-in rejected (check the app password)") from exc
        except OSError as exc:
            raise MailError("Gmail not reachable") from exc
        return client

    # ------------------------------------------------------------------ Reading

    def _fetch(self) -> list[dict[str, Any]]:
        address = self.address
        if not address:
            raise MailError("Gmail is not connected (sentinel gmail login)")
        password = _keyring().get_password(KEYRING_SERVICE, address)
        if not password:
            raise MailError("Gmail is not connected (sentinel gmail login)")
        client = self._login(address, password)
        try:
            status, _ = client.select("INBOX", readonly=True)
            if status != "OK":
                raise MailError("Inbox not readable")
            important = self._search(client, '"is:unread is:important newer_than:3d"')
            unread = self._search(client, '"is:unread newer_than:3d"')
            ids = list(dict.fromkeys(important + unread))[:MAX_MAILS]
            mails = []
            for mail_id in ids:
                status, data = client.fetch(
                    mail_id, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])"
                )
                if status == "OK" and data and isinstance(data[0], tuple):
                    mails.append(parse_headers(data[0][1], mail_id in important))
        finally:
            with contextlib.suppress(Exception):
                client.logout()
        mails.sort(key=lambda m: (not m["important"], -(m["date"] or 0)))
        return mails

    @staticmethod
    def _search(client: imaplib.IMAP4_SSL, query: str) -> list[bytes]:
        status, data = client.search(None, "X-GM-RAW", query)
        if status != "OK" or not data or not data[0]:
            return []
        return list(reversed(data[0].split()))  # newest first

    def unread(self, *, force: bool = False) -> list[dict[str, Any]]:
        if not force and self._cache and time.time() - self._cache[0] < CACHE_S:
            return self._cache[1]
        try:
            mails = self._fetch()
        except MailError as exc:
            self.error = str(exc)
            raise
        self.error = None
        self._cache = (time.time(), mails)
        return mails

    # ------------------------------------------------------------------ Parcels

    def _fetch_parcels(self) -> list[dict[str, Any]]:
        from sentinel.mail import parcels

        address = self.address
        password = _keyring().get_password(KEYRING_SERVICE, address) if address else None
        if not address or not password:
            raise MailError("Gmail is not connected (sentinel gmail login)")
        client = self._login(address, password)
        found: list[dict[str, Any]] = []
        try:
            status, _ = client.select("INBOX", readonly=True)
            if status != "OK":
                raise MailError("Inbox not readable")
            for mail_id in self._search(client, parcels.SEARCH)[: parcels.MAX_MAILS]:
                status, data = client.fetch(mail_id, f"(BODY.PEEK[]<0.{parcels.MAX_BODY}>)")
                if status == "OK" and data and isinstance(data[0], tuple):
                    found.extend(parcels.parse_parcel_mail(data[0][1]))
        finally:
            with contextlib.suppress(Exception):
                client.logout()
        return parcels.merge(found)

    def parcels(self, *, force: bool = False) -> list[dict[str, Any]]:
        """Shipments of the last 3 weeks (cached for 15 minutes)."""
        if not force and self._parcel_cache and time.time() - self._parcel_cache[0] < 900:
            return self._parcel_cache[1]
        result = self._fetch_parcels()
        self._parcel_cache = (time.time(), result)
        return result
