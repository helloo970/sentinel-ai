"""Gmail: read-only, app password only in the vault, content is untrusted."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sentinel.api.app import create_app
from sentinel.mail import service as mail
from sentinel.permissions import Source
from sentinel.services import Services

ADDRESS = "alex.demo@example.com"
APP_PASSWORD = "abcdefghijklmnop"

HEADER_1 = (
    b"From: =?utf-8?b?QsOkY2tlcmVpIE3DvGxsZXI=?= <info@example.org>\r\n"
    b"Subject: =?utf-8?q?Ihre_Bestellung_ist_fertig?=\r\n"
    b"Date: Tue, 20 Oct 2026 07:30:00 +0200\r\n\r\n"
)
HEADER_2 = (
    b"From: Bank <noreply@bank.example>\r\nSubject: Sicherheitshinweis\r\n"
    b"Date: Tue, 20 Oct 2026 08:00:00 +0200\r\n\r\n"
)


class FakeKeyring:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, user: str) -> str | None:
        return self.store.get((service, user))

    def set_password(self, service: str, user: str, password: str) -> None:
        self.store[(service, user)] = password

    def delete_password(self, service: str, user: str) -> None:
        self.store.pop((service, user), None)


class FakeImap:
    """Allows read-only commands only - anything else fails the test."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def select(self, mailbox: str, readonly: bool = False):
        assert readonly is True, "Mailbox must be opened read-only"
        self.calls.append(("select", mailbox))
        return "OK", [b"2"]

    def search(self, charset, *criteria):
        self.calls.append(("search", criteria))
        if "is:important" in criteria[1]:
            return "OK", [b"2"]
        return "OK", [b"1 2"]

    def fetch(self, mail_id: bytes, parts: str):
        assert "BODY.PEEK" in parts, "Headers only via PEEK (do not mark as read)"
        self.calls.append(("fetch", mail_id))
        return "OK", [(b"x", HEADER_1 if mail_id == b"1" else HEADER_2)]

    def logout(self):
        self.calls.append(("logout", None))

    def __getattr__(self, name: str):
        raise AssertionError(f"forbidden IMAP command: {name}")


@pytest.fixture
def gmail(svc: Services, monkeypatch) -> tuple[Services, FakeKeyring, FakeImap]:
    keyring = FakeKeyring()
    imap = FakeImap()
    monkeypatch.setattr(mail, "_keyring", lambda: keyring)
    monkeypatch.setattr(mail.GmailService, "_login", staticmethod(lambda a, p: imap))
    return svc, keyring, imap


def test_header_decoding_and_speech() -> None:
    parsed = mail.parse_headers(HEADER_1, important=False)
    assert parsed["from_name"] == "Bäckerei Müller"
    assert parsed["subject"] == "Ihre Bestellung ist fertig"
    assert parsed["date"] is not None
    assert mail.spoken([]) == "No unread mails."
    text = mail.spoken([parsed, {**parsed, "important": True, "from_name": "Bank"}])
    assert text.startswith("You have 2 unread mails, 1 of them important.")


@pytest.mark.parametrize(
    "address,password",
    [
        ("no-at-sign", APP_PASSWORD),
        (ADDRESS, "too-short"),
        (ADDRESS, "1234567890123456"),
    ],
)
def test_login_validation(gmail, address: str, password: str) -> None:
    svc, keyring, _ = gmail
    with pytest.raises(mail.MailError):
        svc.mail.save_login(address, password)
    assert keyring.store == {}


def test_login_stores_password_only_in_vault(gmail) -> None:
    svc, keyring, _ = gmail
    svc.mail.save_login(ADDRESS, "abcd efgh ijkl mnop")  # Google shows it with spaces
    assert keyring.store[(mail.KEYRING_SERVICE, ADDRESS)] == APP_PASSWORD
    assert svc.mail.configured
    everything = json.dumps(svc.audit.list(50)) + json.dumps(svc.db.query("SELECT * FROM settings"))
    assert APP_PASSWORD not in everything and "abcd efgh" not in everything
    svc.mail.remove_login()
    assert not svc.mail.configured and keyring.store == {}


def test_unread_reads_only_and_sorts_important_first(gmail) -> None:
    svc, keyring, imap = gmail
    svc.mail.save_login(ADDRESS, APP_PASSWORD)
    mails = svc.mail.unread()
    assert [m["from_name"] for m in mails] == ["Bank", "Bäckerei Müller"]
    assert mails[0]["important"] is True
    assert ("select", "INBOX") in imap.calls and imap.calls[-1][0] == "logout"
    imap.calls.clear()
    assert svc.mail.unread() == mails and imap.calls == []  # from the cache


def test_not_configured_without_vault_entry(gmail) -> None:
    svc, _, _ = gmail
    svc.store.set("gmail_address", ADDRESS)
    assert not svc.mail.configured
    with pytest.raises(mail.MailError):
        svc.mail.unread()


def test_mail_tool_output_is_untrusted(gmail) -> None:
    svc, _, _ = gmail
    svc.mail.save_login(ADDRESS, APP_PASSWORD)
    outcome = svc.engine.request("mail_unread", {}, source=Source.ASSISTANT, actor="jarvis")
    assert outcome.status == "executed" and outcome.untrusted
    assert svc.registry.get("mail_unread").untrusted_output


async def test_morning_briefing_mentions_mail(gmail, monkeypatch) -> None:
    svc, _, _ = gmail
    svc.mail.save_login(ADDRESS, APP_PASSWORD)

    async def weather():
        return {"configured": False}

    monkeypatch.setattr(svc.weather, "home_weather", weather)
    result = await svc.reports.morning()
    assert result["parts"]["mail"].startswith("You have 2 unread mails")


def test_api_never_accepts_or_returns_the_password(gmail) -> None:
    svc, _, _ = gmail
    svc.mail.save_login(ADDRESS, APP_PASSWORD)
    app = create_app(svc, token="t", start_background=False)
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    body = client.get("/mail/unread", headers={"Authorization": "Bearer t"}).text
    assert APP_PASSWORD not in body and "Bäckerei" in body
    paths = {getattr(r, "path", "") for r in app.routes}
    assert not any("gmail" in p or ("mail" in p and "login" in p) for p in paths)
