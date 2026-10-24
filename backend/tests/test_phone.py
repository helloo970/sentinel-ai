"""Paired phone: pairing, signed commands, PIN requirement, relay."""

from __future__ import annotations

import base64
import json
import secrets

import pytest

from sentinel.permissions import Autonomy, Source
from sentinel.phone import link
from sentinel.phone.link import PhoneError, derive, sign
from sentinel.services import Services

OWNER_PIN = "246810"


class FakeKeyring:
    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, name: str) -> str | None:
        return self.data.get((service, name))

    def set_password(self, service: str, name: str, value: str) -> None:
        self.data[(service, name)] = value

    def delete_password(self, service: str, name: str) -> None:
        self.data.pop((service, name), None)


@pytest.fixture
def keyring(monkeypatch) -> FakeKeyring:
    fake = FakeKeyring()
    monkeypatch.setattr(link, "_keyring", lambda: fake)
    return fake


@pytest.fixture
def phone(svc: Services, keyring: FakeKeyring) -> bytes:
    """Pair a phone and return the secret (as the phone stores it)."""
    svc.pin.set_pin(OWNER_PIN)
    code = svc.phone.pair(OWNER_PIN)["code"]
    return base64.b32decode(code + "=" * (-len(code) % 8))


def envelope(
    secret: bytes,
    command: dict,
    *,
    pin: str | None = None,
    ts: int | None = None,
    nonce: str | None = None,
) -> dict:
    payload = json.dumps(command)
    nonce = nonce or secrets.token_hex(16)
    ts = ts if ts is not None else int(link.time.time())
    env = {
        "payload": payload,
        "nonce": nonce,
        "ts": ts,
        "sig": sign(derive(secret, "pair"), nonce, ts, payload),
    }
    if pin is not None:
        env["pin_sig"] = sign(derive(secret, "pin:" + pin), nonce, ts, payload)
    return env


# ---------------------------------------------------------------------- Pairing


def test_pairing_needs_owner_pin(svc: Services, keyring: FakeKeyring) -> None:
    with pytest.raises(PhoneError, match="owner PIN"):
        svc.phone.pair("123456")
    svc.pin.set_pin(OWNER_PIN)
    with pytest.raises(PhoneError, match="wrong"):
        svc.phone.pair("000000")
    pairing = svc.phone.pair(OWNER_PIN)
    assert len(pairing["code"]) == 32 and svc.phone.paired
    # Only derived keys are in the vault, never the secret or PIN
    stored = " ".join(keyring.data.values())
    assert pairing["code"] not in stored and OWNER_PIN not in stored
    svc.phone.unpair()
    assert not svc.phone.paired


def test_pair_route(svc: Services, keyring: FakeKeyring) -> None:
    from fastapi.testclient import TestClient

    from sentinel.api.app import create_app

    svc.pin.set_pin(OWNER_PIN)
    svc.config.server_agent_url = "https://10.0.0.5:8844"
    client = TestClient(
        create_app(svc, token="t-123", start_background=False), base_url="http://127.0.0.1:8765"
    )
    headers = {"Authorization": "Bearer t-123"}
    assert client.post("/phone/pair", json={"pin": "999999"}, headers=headers).status_code == 400
    body = client.post("/phone/pair", json={"pin": OWNER_PIN}, headers=headers).json()
    assert body["link"] == f"https://10.0.0.5:8845/m#pair={body['code']}"
    assert client.get("/phone/status", headers=headers).json()["paired"] is True


# ---------------------------------------------------------------------- Verify


async def test_signed_command_runs(svc: Services, phone: bytes) -> None:
    result = await svc.phone.handle(envelope(phone, {"cmd": "ack_all"}))
    assert result["status"] == "executed"


@pytest.mark.parametrize(
    "tamper",
    [
        lambda e: {**e, "payload": json.dumps({"cmd": "resume"})},
        lambda e: {**e, "sig": "0" * 64},
        lambda e: {**e, "sig": "broken"},
        lambda e: {**e, "nonce": "x"},
        lambda e: {**e, "ts": e["ts"] + 1},
    ],
)
async def test_tampered_commands_are_denied(svc: Services, phone: bytes, tamper) -> None:
    svc.engine.halt(actor="user")
    result = await svc.phone.handle(tamper(envelope(phone, {"cmd": "halt"})))
    assert result["status"] == "denied"
    assert svc.engine.halted


async def test_wrong_secret_is_denied(svc: Services, phone: bytes) -> None:
    other = secrets.token_bytes(20)
    result = await svc.phone.handle(envelope(other, {"cmd": "ack_all"}))
    assert result["status"] == "denied" and "Signature" in result["message"]


async def test_replay_and_old_commands_denied(svc: Services, phone: bytes) -> None:
    env = envelope(phone, {"cmd": "ack_all"})
    assert (await svc.phone.handle(env))["status"] == "executed"
    replay = await svc.phone.handle(env)
    assert replay["status"] == "denied" and "already" in replay["message"]
    old = envelope(phone, {"cmd": "ack_all"}, ts=int(link.time.time()) - 3600)
    assert (await svc.phone.handle(old))["status"] == "denied"


async def test_unknown_command_denied(svc: Services, phone: bytes) -> None:
    result = await svc.phone.handle(envelope(phone, {"cmd": "shell", "line": "whoami"}))
    assert result["status"] == "denied"


async def test_not_paired_denied(svc: Services, keyring: FakeKeyring) -> None:
    result = await svc.phone.handle(envelope(secrets.token_bytes(20), {"cmd": "halt"}))
    assert result["status"] == "denied" and "paired" in result["message"]


# ---------------------------------------------------------------------- PIN


async def test_resume_needs_pin(svc: Services, phone: bytes) -> None:
    svc.engine.halt(actor="user")
    no_pin = await svc.phone.handle(envelope(phone, {"cmd": "resume"}))
    assert no_pin["status"] == "denied" and svc.engine.halted
    wrong = await svc.phone.handle(envelope(phone, {"cmd": "resume"}, pin="111111"))
    assert wrong["status"] == "denied" and svc.engine.halted
    ok = await svc.phone.handle(envelope(phone, {"cmd": "resume"}, pin=OWNER_PIN))
    assert ok["status"] == "executed" and not svc.engine.halted
    audit = svc.db.query("SELECT actor FROM audit WHERE action = 'emergency_release'")
    assert not audit or audit[-1]["actor"] == "phone"


async def test_pin_lock_after_five_failures(svc: Services, phone: bytes) -> None:
    for _ in range(5):
        await svc.phone.handle(envelope(phone, {"cmd": "autonomy", "level": 0}, pin="000000"))
    locked = await svc.phone.handle(envelope(phone, {"cmd": "autonomy", "level": 0}, pin=OWNER_PIN))
    assert locked["status"] == "denied" and "locked" in locked["message"]
    assert svc.engine.autonomy == Autonomy.AUTOPILOT


async def test_autonomy_with_pin(svc: Services, phone: bytes) -> None:
    result = await svc.phone.handle(envelope(phone, {"cmd": "autonomy", "level": 1}, pin=OWNER_PIN))
    assert result["status"] == "executed" and svc.engine.autonomy == Autonomy.ASSISTANT
    bad = await svc.phone.handle(envelope(phone, {"cmd": "autonomy", "level": 9}, pin=OWNER_PIN))
    assert bad["status"] == "denied"


# ---------------------------------------------------------------------- Actions


async def test_action_runs_through_engine(svc: Services, phone: bytes) -> None:
    result = await svc.phone.handle(
        envelope(phone, {"cmd": "action", "name": "add_shopping_item", "params": {"text": "Milk"}})
    )
    assert result["status"] == "executed" and svc.shopping.items()[0]["text"] == "Milk"
    unknown = await svc.phone.handle(
        envelope(phone, {"cmd": "action", "name": "format_disk", "params": {}})
    )
    assert unknown["status"] == "denied"
    svc.engine.halt(actor="user")
    halted = await svc.phone.handle(
        envelope(phone, {"cmd": "action", "name": "add_shopping_item", "params": {"text": "Bread"}})
    )
    assert halted["status"] == "denied"


async def test_pin_approval_from_phone(svc: Services, phone: bytes, monkeypatch) -> None:
    order_id = svc.db.execute(
        "INSERT INTO orders(ts, query, data) VALUES(0, 'x', ?)",
        (
            json.dumps(
                {
                    "title": "Cable",
                    "price": 9.99,
                    "currency": "EUR",
                    "shop": "otto.de",
                    "url": "https://www.otto.de/p/kabel",
                    "summary": "",
                    "specs": [],
                    "availability": "",
                }
            ),
        ),
    )
    pending = svc.engine.request(
        "open_order", {"order_id": order_id}, source=Source.USER, actor="user"
    )
    assert pending.status == "pending"
    opened: list[str] = []
    import sentinel.actions.shop as shop_actions

    monkeypatch.setattr(
        shop_actions.webbrowser, "open", lambda url, new=0: opened.append(url) or True
    )
    without = await svc.phone.handle(envelope(phone, {"cmd": "approve", "id": pending.approval_id}))
    assert without["status"] == "denied" and opened == []
    ok = await svc.phone.handle(
        envelope(phone, {"cmd": "approve", "id": pending.approval_id}, pin=OWNER_PIN)
    )
    assert ok["status"] == "executed" and opened == ["https://www.otto.de/p/kabel"]


async def test_chat_from_phone_uses_quick_commands(svc: Services, phone: bytes, scripted) -> None:
    result = await svc.phone.handle(
        envelope(phone, {"cmd": "chat", "text": "Put eggs on the list"})
    )
    assert result["status"] == "executed" and "eggs" in result["message"].lower()
    assert scripted.calls == []


# ---------------------------------------------------------------------- Relay


async def test_relay_passes_phone_commands_and_reports_results(svc: Services, phone: bytes) -> None:
    command = {
        "id": "c1",
        "action": "phone",
        "envelope": envelope(
            phone, {"cmd": "action", "name": "add_shopping_item", "params": {"text": "Cheese"}}
        ),
    }
    assert await svc.relay.handle_all([command]) == 1
    assert await svc.relay.handle_all([command]) == 0  # same ID only once
    snap = svc.relay.snapshot()
    assert snap["results"][-1]["id"] == "c1" and snap["results"][-1]["status"] == "executed"
    assert snap["paired"] is True
    assert {"approvals", "undo", "modes", "routines", "programs"} <= set(snap)


def test_snapshot_without_pairing_has_no_control_data(svc: Services, keyring: FakeKeyring) -> None:
    snap = svc.relay.snapshot()
    assert snap["paired"] is False and "approvals" not in snap and "programs" not in snap


async def test_alert_test_from_phone(svc: Services, phone: bytes) -> None:
    result = await svc.phone.handle(envelope(phone, {"cmd": "test_alert", "rule": "server_backup"}))
    assert result["status"] == "executed" and result["message"].startswith("TEST: ")
    bad = await svc.phone.handle(envelope(phone, {"cmd": "test_alert", "rule": "x"}))
    assert bad["status"] == "denied"
    assert len(svc.relay.snapshot()["tests"]) > 10
