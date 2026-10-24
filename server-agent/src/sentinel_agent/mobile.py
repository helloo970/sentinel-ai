"""Phone view via the server: read status and trigger the emergency stop.

The PC opens no port. It reports its status to the agent regularly
(POST /v1/relay, token + IP allowlist) and picks up waiting commands in the same call.
The phone only talks to a dedicated HTTPS port of the agent:

- only devices from the approved home subnets (RFC 1918)
- login by PIN (scrypt hash in a file, 5 failed attempts -> 5 minute lockout)
- session cookie (HttpOnly, Secure, SameSite=Strict), POST only with a custom header
- without pairing: read-only and trigger the emergency stop
- with a paired phone: signed commands (approvals, modes, release the emergency stop ...).
  The agent only checks the shape and forwards; signature and PIN are verified by the PC
  alone (ADR 0009)
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import re
import secrets
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

log = logging.getLogger("sentinel_agent.mobile")

PIN_MIN, PIN_MAX = 6, 64
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
SESSION_TTL_S = 12 * 3600
MAX_SESSIONS = 20
IP_FAIL_LIMIT, IP_LOCK_S = 5, 300
GLOBAL_FAIL_LIMIT, GLOBAL_WINDOW_S = 20, 3600
MAX_BODY = 4096
MAX_RELAY_BODY = 65536
SEVERITIES = {"info", "warn", "critical"}
PC_STALE_S = 120
MAX_PENDING = 20
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX32 = re.compile(r"^[0-9a-f]{32}$")
ALLOWED_NETWORKS = (
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
    ipaddress.IPv4Network("127.0.0.0/8"),  # for local tests only, the phone is never loopback
)


# ---------------------------------------------------------------------- PIN


def hash_pin(pin: str) -> str:
    if not PIN_MIN <= len(pin) <= PIN_MAX:
        raise ValueError(f"PIN must have {PIN_MIN} to {PIN_MAX} characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(pin.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_pin(pin: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, digest = stored.strip().split("$")
        if kind != "scrypt":
            return False
        candidate = hashlib.scrypt(
            pin.encode()[:256], salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate.hex(), digest)


# ---------------------------------------------------------------------- Relay


def _text(value: Any, limit: int) -> str:
    return str(value or "")[:limit]


def sanitize_status(raw: Any) -> dict[str, Any]:
    """Accept only known fields with fixed types and lengths."""
    if not isinstance(raw, dict):
        raise ValueError("status must be an object")
    alerts = raw.get("alerts") if isinstance(raw.get("alerts"), dict) else {}
    recent = []
    for item in (raw.get("recent") or [])[:10]:
        if not isinstance(item, dict):
            continue
        severity = item.get("severity") if item.get("severity") in SEVERITIES else "info"
        recent.append(
            {
                "title": _text(item.get("title"), 160),
                "severity": severity,
                "ts": float(item.get("ts") or 0),
            }
        )
    autonomy = raw.get("autonomy")
    for item, source in zip(recent, (raw.get("recent") or [])[:10], strict=False):
        if isinstance(source, dict) and isinstance(source.get("id"), int):
            item["id"] = source["id"]
    return {
        "halted": bool(raw.get("halted")),
        "autonomy": autonomy if isinstance(autonomy, int) and 0 <= autonomy <= 3 else None,
        "alerts": {k: max(0, int(alerts.get(k) or 0)) for k in ("critical", "warn", "info")},
        "recent": recent,
        "mode": _text(raw.get("mode"), 60) or None,
        "quiet": bool(raw.get("quiet")),
        "paired": bool(raw.get("paired")),
        "approvals": [
            {
                "id": _text(a.get("id"), 32),
                "title": _text(a.get("title"), 200),
                "risk": _text(a.get("risk"), 20),
                "requires_pin": bool(a.get("requires_pin")),
                "expires": _number(a.get("expires")),
            }
            for a in _dicts(raw.get("approvals"), 10)
        ],
        "undo": [
            {
                "id": _text(u.get("id"), 32),
                "title": _text(u.get("title"), 160),
                "expires": _number(u.get("expires")),
            }
            for u in _dicts(raw.get("undo"), 10)
        ],
        "modes": [
            {
                "name": _text(m.get("name"), 32),
                "title": _text(m.get("title"), 60),
                "active": bool(m.get("active")),
            }
            for m in _dicts(raw.get("modes"), 10)
        ],
        "routines": [
            {"name": _text(r.get("name"), 32), "title": _text(r.get("title"), 60)}
            for r in _dicts(raw.get("routines"), 10)
        ],
        "programs": [
            {"name": _text(r.get("name"), 32), "title": _text(r.get("title"), 60)}
            for r in _dicts(raw.get("programs"), 20)
        ],
        "tests": [
            {
                "rule": _text(t.get("rule"), 40),
                "group": _text(t.get("group"), 20),
                "title": _text(t.get("title"), 120),
            }
            for t in _dicts(raw.get("tests"), 40)
        ],
        "results": [
            {
                "id": _text(r.get("id"), 32),
                "status": _text(r.get("status"), 20),
                "message": _text(r.get("message"), 500),
                "ts": _number(r.get("ts")),
            }
            for r in _dicts(raw.get("results"), 20)
        ],
    }


def _dicts(value: Any, limit: int) -> list[dict[str, Any]]:
    return [v for v in value[:limit] if isinstance(v, dict)] if isinstance(value, list) else []


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def sanitize_envelope(raw: Any) -> dict[str, Any]:
    """Check the shape only - the PC verifies the signature, the agent does not know the key."""
    if not isinstance(raw, dict):
        raise ValueError("envelope must be an object")
    payload = raw.get("payload")
    if not isinstance(payload, str) or not 0 < len(payload) <= 2000:
        raise ValueError("invalid payload")
    if not HEX32.match(str(raw.get("nonce", ""))) or not HEX64.match(str(raw.get("sig", ""))):
        raise ValueError("invalid signature")
    envelope = {
        "payload": payload,
        "nonce": raw["nonce"],
        "ts": int(_number(raw.get("ts"))),
        "sig": raw["sig"],
    }
    if raw.get("pin_sig"):
        if not HEX64.match(str(raw["pin_sig"])):
            raise ValueError("invalid pin signature")
        envelope["pin_sig"] = raw["pin_sig"]
    return envelope


class Relay:
    """Latest PC status and waiting commands (in memory only)."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self._lock = threading.Lock()
        self.status: dict[str, Any] | None = None
        self.received = 0.0
        self.pending: list[dict[str, Any]] = []
        self.halt_sent = 0.0

    def report(self, raw: Any) -> list[dict[str, Any]]:
        status = sanitize_status(raw)
        with self._lock:
            self.status = status
            self.received = self.clock()
            if status["halted"]:
                self.pending = [c for c in self.pending if c["action"] != "halt"]
            commands, self.pending = self.pending, []
            if any(c["action"] == "halt" for c in commands):
                self.halt_sent = self.received
        return commands

    def request_halt(self, client: str) -> dict[str, Any]:
        with self._lock:
            existing = next((c for c in self.pending if c["action"] == "halt"), None)
            if existing:
                return existing
            command = {
                "id": secrets.token_hex(8),
                "action": "halt",
                "ts": self.clock(),
                "client": client,
            }
            self.pending.append(command)
        log.warning("emergency stop requested from phone %s", client)
        return command

    def request_phone(self, client: str, raw: Any) -> dict[str, Any]:
        envelope = sanitize_envelope(raw)
        with self._lock:
            if len(self.pending) >= MAX_PENDING:
                raise ValueError("too many pending commands")
            command = {
                "id": secrets.token_hex(8),
                "action": "phone",
                "ts": self.clock(),
                "client": client,
                "envelope": envelope,
            }
            self.pending.append(command)
        return command

    def view(self) -> dict[str, Any]:
        with self._lock:
            age = self.clock() - self.received if self.received else None
            return {
                "pc": self.status,
                "pc_age_s": round(age, 1) if age is not None else None,
                "pc_online": age is not None and age < PC_STALE_S,
                "halt_pending": any(c["action"] == "halt" for c in self.pending)
                or (
                    bool(self.halt_sent)
                    and not (self.status or {}).get("halted")
                    and self.clock() - self.halt_sent < PC_STALE_S
                ),
            }


# ---------------------------------------------------------------------- Login


class MobileAuth:
    def __init__(self, pin_file: str, clock: Callable[[], float] = time.time) -> None:
        self.pin_file = Path(pin_file)
        self.clock = clock
        self._lock = threading.Lock()
        self._sessions: dict[str, float] = {}
        self._ip_fails: dict[str, list[float]] = {}
        self._global_fails: list[float] = []

    @property
    def configured(self) -> bool:
        return self.pin_file.is_file()

    def locked(self, client: str) -> bool:
        now = self.clock()
        with self._lock:
            self._global_fails = [t for t in self._global_fails if t > now - GLOBAL_WINDOW_S]
            fails = [t for t in self._ip_fails.get(client, []) if t > now - IP_LOCK_S]
            self._ip_fails[client] = fails
            return len(fails) >= IP_FAIL_LIMIT or len(self._global_fails) >= GLOBAL_FAIL_LIMIT

    def login(self, client: str, pin: str) -> str | None:
        if self.locked(client) or not self.configured:
            return None
        try:
            stored = self.pin_file.read_text(encoding="utf-8")
        except OSError:
            return None
        if not verify_pin(pin, stored):
            with self._lock:
                now = self.clock()
                self._ip_fails.setdefault(client, []).append(now)
                self._global_fails.append(now)
            log.warning("mobile login failed from %s", client)
            return None
        token = secrets.token_urlsafe(32)
        with self._lock:
            now = self.clock()
            self._sessions = {t: e for t, e in self._sessions.items() if e > now}
            if len(self._sessions) >= MAX_SESSIONS:
                oldest = min(self._sessions, key=self._sessions.__getitem__)
                del self._sessions[oldest]
            self._sessions[token] = now + SESSION_TTL_S
            self._ip_fails.pop(client, None)
        log.info("mobile login from %s", client)
        return token

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            expires = self._sessions.get(token)
            return expires is not None and expires > self.clock()

    def logout(self, token: str | None) -> None:
        with self._lock:
            self._sessions.pop(token or "", None)


# ---------------------------------------------------------------------- HTTP


SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self'; manifest-src 'self'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
    "Strict-Transport-Security": "max-age=31536000",
}
STATIC_DIR = Path(__file__).with_name("mobile_static")
STATIC = {
    "/m": ("index.html", "text/html; charset=utf-8"),
    "/m/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/m/icon.png": ("icon.png", "image/png"),
    "/m/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/m/sign.js": ("sign.js", "text/javascript; charset=utf-8"),
    "/m/app.css": ("app.css", "text/css; charset=utf-8"),
}


def parse_networks(values: list[str]) -> list[ipaddress.IPv4Network]:
    networks = []
    for value in values:
        network = ipaddress.ip_network(value, strict=False)
        if not isinstance(network, ipaddress.IPv4Network) or not any(
            network.subnet_of(allowed) for allowed in ALLOWED_NETWORKS
        ):
            raise ValueError(f"mobile client network must be private: {value}")
        if network.prefixlen < 16:
            raise ValueError(f"mobile client network too large: {value}")
        networks.append(network)
    return networks


def make_mobile_handler(
    networks: list[ipaddress.IPv4Network],
    relay: Relay,
    auth: MobileAuth,
    extra_view: Callable[[], dict[str, Any]] = dict,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "sentinel-agent"
        sys_version = ""

        def log_message(self, fmt: str, *args: Any) -> None:
            log.info("mobile %s %s", self.client_address[0], fmt % args)

        def _allowed(self) -> bool:
            try:
                ip = ipaddress.ip_address(self.client_address[0])
            except ValueError:
                return False
            return any(ip in n for n in networks)

        def _send(
            self,
            status: int,
            body: bytes,
            content_type: str = "application/json",
            headers: dict[str, str] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for key, value in {**SECURITY_HEADERS, **(headers or {})}.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, data: dict[str, Any], **kw: Any) -> None:
            self._send(status, json.dumps(data).encode(), **kw)

        def _session(self) -> str | None:
            for part in self.headers.get("Cookie", "").split(";"):
                name, _, value = part.strip().partition("=")
                if name == "sid":
                    return value
            return None

        def do_GET(self) -> None:
            if not self._allowed():
                self._json(403, {"error": "forbidden"})
                return
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send(302, b"", headers={"Location": "/m"})
                return
            if path in STATIC:
                name, content_type = STATIC[path]
                self._send(200, (STATIC_DIR / name).read_bytes(), content_type)
                return
            if path == "/m/api/status":
                if not auth.valid(self._session()):
                    self._json(401, {"error": "login required"})
                    return
                self._json(200, {**relay.view(), **extra_view()})
                return
            self._json(404, {"error": "not found"})

        def do_POST(self) -> None:
            if not self._allowed():
                self._json(403, {"error": "forbidden"})
                return
            if self.headers.get("X-Sentinel") != "1":
                self._json(403, {"error": "missing header"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self._json(413, {"error": "too large"})
                return
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self._json(400, {"error": "invalid json"})
                return
            client = self.client_address[0]
            path = self.path.split("?", 1)[0]
            if path == "/m/api/login":
                if auth.locked(client):
                    self._json(429, {"error": "locked"})
                    return
                token = auth.login(client, str(body.get("pin", "")))
                if token is None:
                    self._json(401, {"error": "wrong pin"})
                    return
                cookie = (
                    f"sid={token}; HttpOnly; Secure; SameSite=Strict; Path=/m; "
                    f"Max-Age={SESSION_TTL_S}"
                )
                self._json(200, {"ok": True}, headers={"Set-Cookie": cookie})
                return
            if not auth.valid(self._session()):
                self._json(401, {"error": "login required"})
                return
            if path == "/m/api/stop":
                if body.get("confirm") is not True:
                    self._json(400, {"error": "confirm required"})
                    return
                command = relay.request_halt(client)
                self._json(200, {"ok": True, "id": command["id"]})
                return
            if path == "/m/api/command":
                try:
                    command = relay.request_phone(client, body.get("envelope"))
                except ValueError as exc:
                    self._json(400, {"error": str(exc)})
                    return
                self._json(200, {"ok": True, "id": command["id"]})
                return
            if path == "/m/api/logout":
                auth.logout(self._session())
                cookie = "sid=; HttpOnly; Secure; SameSite=Strict; Path=/m; Max-Age=0"
                self._json(200, {"ok": True}, headers={"Set-Cookie": cookie})
                return
            self._json(404, {"error": "not found"})

        def _reject(self) -> None:
            self._json(405, {"error": "method not allowed"})

        do_PUT = do_DELETE = do_PATCH = _reject  # noqa: N815

    return Handler
