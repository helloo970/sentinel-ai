"""Phone view, PC relay and backup watcher."""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from sentinel_agent import backups, mobile
from sentinel_agent.server import AgentConfig, build_mobile_server, build_server

TOKEN = "a" * 40
PIN = "482915"


# ---------------------------------------------------------------------- PIN & Relay


def test_pin_hash_roundtrip_and_limits() -> None:
    stored = mobile.hash_pin(PIN)
    assert stored.startswith("scrypt$") and PIN not in stored
    assert mobile.verify_pin(PIN, stored)
    assert not mobile.verify_pin("000000", stored)
    assert not mobile.verify_pin(PIN, "kaputt")
    with pytest.raises(ValueError):
        mobile.hash_pin("123")


def test_relay_sanitizes_status() -> None:
    status = mobile.sanitize_status(
        {
            "halted": 1,
            "autonomy": 7,
            "alerts": {"critical": "2", "warn": -4},
            "recent": [{"title": "x" * 500, "severity": "evil"}, "kaputt"],
            "secret": "token",
        }
    )
    assert status["halted"] is True and status["autonomy"] is None
    assert status["alerts"] == {"critical": 2, "warn": 0, "info": 0}
    assert len(status["recent"]) == 1 and len(status["recent"][0]["title"]) == 160
    assert status["recent"][0]["severity"] == "info"
    assert "secret" not in status
    with pytest.raises(ValueError):
        mobile.sanitize_status(["not", "an object"])


def test_relay_delivers_halt_once() -> None:
    now = [1000.0]
    relay = mobile.Relay(clock=lambda: now[0])
    first = relay.request_halt("192.0.2.50")
    assert relay.request_halt("192.0.2.50")["id"] == first["id"]  # no duplicate command
    assert relay.view()["halt_pending"] is True
    commands = relay.report({"halted": False})
    assert [c["action"] for c in commands] == ["halt"]
    assert relay.report({"halted": False}) == []
    assert relay.view()["halt_pending"] is True  # delivered, PC not locked yet
    relay.report({"halted": True})
    view = relay.view()
    assert view["halt_pending"] is False and view["pc"]["halted"] is True
    now[0] += 500
    assert relay.view()["pc_online"] is False


def test_auth_locks_after_failures(tmp_path: Path) -> None:
    pin_file = tmp_path / "pin"
    pin_file.write_text(mobile.hash_pin(PIN))
    now = [0.0]
    auth = mobile.MobileAuth(str(pin_file), clock=lambda: now[0])
    for _ in range(5):
        assert auth.login("192.0.2.9", "999999") is None
    assert auth.locked("192.0.2.9")
    assert auth.login("192.0.2.9", PIN) is None  # locked, even with the right PIN
    token = auth.login("192.0.2.10", PIN)
    assert token and auth.valid(token)
    now[0] += mobile.IP_LOCK_S + 1
    assert auth.login("192.0.2.9", PIN)
    now[0] += mobile.SESSION_TTL_S + 1
    assert not auth.valid(token)


def test_no_pin_file_means_no_login(tmp_path: Path) -> None:
    auth = mobile.MobileAuth(str(tmp_path / "fehlt"))
    assert auth.login("192.0.2.9", PIN) is None


@pytest.mark.parametrize("network", ["0.0.0.0/0", "203.0.113.0/24", "10.0.0.0/8"])
def test_mobile_networks_must_be_small_and_private(network: str) -> None:
    with pytest.raises(ValueError):
        mobile.parse_networks([network])


def test_mobile_config_validation() -> None:
    base = dict(bind="10.0.0.1", port=8844, cert="c", key="k", token=TOKEN)
    AgentConfig(
        **base, allowed_clients=["10.0.0.2"], mobile_port=8845, mobile_clients=["10.0.0.0/24"]
    ).validate()
    with pytest.raises(ValueError):
        AgentConfig(**base, allowed_clients=["10.0.0.2"], mobile_port=8845).validate()
    with pytest.raises(ValueError):
        AgentConfig(
            **base, allowed_clients=["10.0.0.2"], mobile_port=8844, mobile_clients=["10.0.0.0/24"]
        ).validate()
    with pytest.raises(ValueError):
        AgentConfig(**base, allowed_clients=["10.0.0.2"], backup_paths=["relativ"]).validate()


# ---------------------------------------------------------------------- HTTP


def request(url: str, *, body: dict | None = None, headers: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET")  # noqa: S310
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:  # noqa: S310
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read()


@pytest.fixture
def servers(tmp_path: Path):
    pin_file = tmp_path / "pin"
    pin_file.write_text(mobile.hash_pin(PIN))
    config = AgentConfig(
        "127.0.0.1",
        0,
        "",
        "",
        TOKEN,
        ["127.0.0.1"],
        services=[],
        backup_snapshot=str(tmp_path / "backups.json"),
        mobile_port=1,
        mobile_clients=["127.0.0.0/24"],
        mobile_pin_file=str(pin_file),
    )
    relay = mobile.Relay()
    agent = build_server(config, tls=False, relay=relay)
    config.mobile_port = 0  # free port for testing
    phone = build_mobile_server(config, relay, tls=False)
    for srv in (agent, phone):
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield (
        f"http://127.0.0.1:{agent.server_address[1]}",
        f"http://127.0.0.1:{phone.server_address[1]}",
    )
    agent.shutdown()
    phone.shutdown()


def login(phone: str) -> str:
    status, headers, _ = request(
        f"{phone}/m/api/login", body={"pin": PIN}, headers={"X-Sentinel": "1"}
    )
    assert status == 200
    cookie = headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Secure" in cookie
    return cookie.split(";")[0]


def test_phone_page_has_strict_headers(servers) -> None:
    _, phone = servers
    status, headers, body = request(f"{phone}/m")
    assert status == 200 and b"SENTINEL" in body
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    assert headers["X-Frame-Options"] == "DENY"
    assert request(f"{phone}/m/../server.py")[0] == 404


def test_phone_requires_login_and_header(servers) -> None:
    _, phone = servers
    assert request(f"{phone}/m/api/status")[0] == 401
    assert request(f"{phone}/m/api/login", body={"pin": PIN})[0] == 403  # without header
    assert (
        request(f"{phone}/m/api/login", body={"pin": "000000"}, headers={"X-Sentinel": "1"})[0]
        == 401
    )
    cookie = login(phone)
    status, _, body = request(f"{phone}/m/api/status", headers={"Cookie": cookie})
    data = json.loads(body)
    assert status == 200 and data["pc"] is None and "backups" in data and "server" in data


def test_phone_stop_reaches_pc_via_relay(servers) -> None:
    agent, phone = servers
    cookie = login(phone)
    headers = {"Cookie": cookie, "X-Sentinel": "1"}
    assert request(f"{phone}/m/api/stop", body={}, headers=headers)[0] == 400  # without confirm
    assert request(f"{phone}/m/api/stop", body={"confirm": True}, headers=headers)[0] == 200

    # PC reports without a token -> rejected, with a token -> gets the emergency stop
    assert request(f"{agent}/v1/relay", body={"halted": False})[0] == 401
    status, _, body = request(
        f"{agent}/v1/relay",
        body={"halted": False, "autonomy": 2},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert status == 200 and json.loads(body)["commands"][0]["action"] == "halt"

    request(
        f"{agent}/v1/relay", body={"halted": True}, headers={"Authorization": f"Bearer {TOKEN}"}
    )
    _, _, body = request(f"{phone}/m/api/status", headers={"Cookie": cookie})
    assert json.loads(body)["pc"]["halted"] is True


def test_phone_logout(servers) -> None:
    _, phone = servers
    cookie = login(phone)
    request(f"{phone}/m/api/logout", body={}, headers={"Cookie": cookie, "X-Sentinel": "1"})
    assert request(f"{phone}/m/api/status", headers={"Cookie": cookie})[0] == 401


def test_relay_rejects_large_body(servers) -> None:
    agent, _ = servers
    big = {"recent": [{"title": "x" * 1000}] * 100}
    try:
        status, _, _ = request(
            f"{agent}/v1/relay", body=big, headers={"Authorization": f"Bearer {TOKEN}"}
        )
    except (ConnectionError, urllib.error.URLError):
        status = 413  # server rejected and closed the connection
    assert status == 413


# ---------------------------------------------------------------------- Backups


def test_backup_scan_and_status(tmp_path: Path) -> None:
    root = tmp_path / "backup"
    (root / "daily").mkdir(parents=True)
    old, new = root / "a.tar", root / "daily" / "b.tar"
    old.write_bytes(b"1" * 10)
    new.write_bytes(b"2" * 30)
    os.utime(old, (1000, 1000))
    os.utime(new, (5000, 5000))
    result = backups.scan([str(root), str(tmp_path / "fehlt")])
    first, missing = result["paths"]
    assert first["files"] == 2 and first["bytes"] == 40 and first["newest"] == 5000
    assert missing == {"path": str(tmp_path / "fehlt"), "exists": False}

    snap = tmp_path / "backups.json"
    snap.write_text(json.dumps(result))
    status = backups.backup_status(str(snap), now=result["ts"] + 10)
    assert status["available"] and not status["stale"]
    assert backups.backup_status(str(snap), now=result["ts"] + 4 * 3600)["stale"]
    assert backups.backup_status(str(tmp_path / "x.json"))["available"] is False


def test_backup_scan_entry_limit(tmp_path: Path) -> None:
    for i in range(20):
        (tmp_path / f"{i}.bin").write_bytes(b"x")
    assert backups.scan_path(str(tmp_path), max_entries=5)["truncated"] is True


def test_backups_endpoint(servers, tmp_path: Path) -> None:
    agent, _ = servers
    (tmp_path / "backups.json").write_text(json.dumps({"ts": time.time(), "paths": []}))
    status, _, body = request(f"{agent}/v1/backups", headers={"Authorization": f"Bearer {TOKEN}"})
    assert status == 200 and json.loads(body)["available"] is True


# ---------------------------------------------------------------------- Paired phone

ENVELOPE = {"payload": '{"cmd":"ack_all"}', "nonce": "a" * 32, "ts": 1767225600, "sig": "b" * 64}


def test_envelope_shape_only() -> None:
    assert mobile.sanitize_envelope({**ENVELOPE, "extra": "x"}) == ENVELOPE
    with_pin = mobile.sanitize_envelope({**ENVELOPE, "pin_sig": "c" * 64})
    assert with_pin["pin_sig"] == "c" * 64
    for bad in (
        {**ENVELOPE, "sig": "zz"},
        {**ENVELOPE, "nonce": "1"},
        {**ENVELOPE, "payload": "x" * 3000},
        {**ENVELOPE, "pin_sig": "nope"},
        "kaputt",
    ):
        with pytest.raises(ValueError):
            mobile.sanitize_envelope(bad)


def test_status_control_fields_sanitized() -> None:
    status = mobile.sanitize_status(
        {
            "paired": True,
            "approvals": [{"id": "x" * 99, "title": "t" * 999, "requires_pin": 1}, "kaputt"],
            "modes": [{"name": "gaming", "title": "Gaming", "active": True}],
            "results": [{"id": "r1", "status": "executed", "message": "ok", "ts": "nan?"}],
            "recent": [{"id": 7, "title": "a", "severity": "warn"}, {"id": "x", "title": "b"}],
        }
    )
    assert status["approvals"][0]["id"] == "x" * 32 and len(status["approvals"]) == 1
    assert len(status["approvals"][0]["title"]) == 200
    assert status["modes"][0]["active"] is True and status["results"][0]["ts"] == 0.0
    assert status["recent"][0]["id"] == 7 and "id" not in status["recent"][1]


def test_phone_command_is_forwarded_untouched(servers) -> None:
    agent, phone = servers
    cookie = login(phone)
    headers = {"Cookie": cookie, "X-Sentinel": "1"}
    assert (
        request(f"{phone}/m/api/command", body={"envelope": {"sig": "x"}}, headers=headers)[0]
        == 400
    )
    status, _, body = request(
        f"{phone}/m/api/command", body={"envelope": ENVELOPE}, headers=headers
    )
    assert status == 200
    _, _, body = request(
        f"{agent}/v1/relay", body={"halted": False}, headers={"Authorization": f"Bearer {TOKEN}"}
    )
    command = json.loads(body)["commands"][0]
    assert command["action"] == "phone" and command["envelope"] == ENVELOPE


def test_phone_command_requires_login(servers) -> None:
    _, phone = servers
    status, _, _ = request(
        f"{phone}/m/api/command", body={"envelope": ENVELOPE}, headers={"X-Sentinel": "1"}
    )
    assert status == 401


def test_pending_limit() -> None:
    relay = mobile.Relay()
    for _ in range(mobile.MAX_PENDING):
        relay.request_phone("192.0.2.9", ENVELOPE)
    with pytest.raises(ValueError):
        relay.request_phone("192.0.2.9", ENVELOPE)


def test_static_app_files(servers) -> None:
    _, phone = servers
    for path, kind in (
        ("/m/sign.js", "javascript"),
        ("/m/manifest.webmanifest", "manifest"),
        ("/m/icon.png", "image/png"),
    ):
        status, headers, _ = request(f"{phone}{path}")
        assert status == 200 and kind in headers["Content-Type"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_signature_matches_python(tmp_path: Path) -> None:
    import base64
    import hashlib
    import hmac
    import subprocess

    sign_js = Path(mobile.__file__).with_name("mobile_static") / "sign.js"
    code = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
    payload = '{"cmd":"resume","text":"Café ✓"}'
    script = (
        f"require({json.dumps(str(sign_js))}); const S = globalThis.SentinelSign;"
        f"const s = S.base32({json.dumps(code)});"
        f"console.log(S.signEnvelope(S.derive(s, 'pin:135790'), {json.dumps('a' * 32)}, 1767225600,"
        f" {json.dumps(payload)}));"
    )
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)  # noqa: S603, S607
    secret = base64.b32decode(code)
    key = hmac.new(secret, b"pin:135790", hashlib.sha256).digest()
    message = f"{'a' * 32}.1767225600.{payload}".encode()
    assert out.stdout.strip() == hmac.new(key, message, hashlib.sha256).hexdigest()


def test_status_keeps_alarm_tests() -> None:
    status = mobile.sanitize_status(
        {"tests": [{"rule": "malware_file", "group": "PC", "title": "x" * 300}] * 50}
    )
    assert len(status["tests"]) == 40 and len(status["tests"][0]["title"]) == 120
