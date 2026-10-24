"""HTTPS server of the agent: token + TLS + IP allowlist.

Read-only via GET. The only write is POST /v1/relay: the PC reports its status for the
phone view and picks up waiting emergency-stop commands in the same call.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import ssl
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from sentinel_agent import backups, collectors, extras, mobile, vault

log = logging.getLogger("sentinel_agent")

RFC1918 = (
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
)
MAX_AUTH_FAILURES = 10


@dataclass
class AgentConfig:
    bind: str
    port: int
    cert: str
    key: str
    token: str
    allowed_clients: list[str]
    services: list[str] = field(default_factory=lambda: ["ssh"])
    auth_log: str = "/var/log/auth.log"
    docker_snapshot: str = extras.DOCKER_SNAPSHOT
    pihole_url: str = "https://127.0.0.1:8443"
    pihole_password_file: str = "/etc/sentinel-agent/pihole-password"  # noqa: S105 - path
    backup_paths: list[str] = field(default_factory=list)
    backup_snapshot: str = backups.BACKUP_SNAPSHOT
    # Phone view: own port (0 = off), allowed home subnets, PIN hash file
    mobile_port: int = 0
    mobile_clients: list[str] = field(default_factory=list)
    mobile_pin_file: str = "/etc/sentinel-agent/mobile-pin"
    # Encrypted backups from the PC (brain)
    vault_dir: str = vault.VAULT_DIR

    @classmethod
    def load(cls, path: str | Path) -> AgentConfig:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        token_file = Path(data["token_file"])
        token = token_file.read_text(encoding="utf-8").strip()
        config = cls(
            bind=data["bind"],
            port=int(data.get("port", 8844)),
            cert=data["cert"],
            key=data["key"],
            token=token,
            allowed_clients=list(data["allowed_clients"]),
            services=list(data.get("services", ["ssh"])),
            auth_log=data.get("auth_log", "/var/log/auth.log"),
            docker_snapshot=data.get("docker_snapshot", extras.DOCKER_SNAPSHOT),
            pihole_url=data.get("pihole_url", "https://127.0.0.1:8443"),
            pihole_password_file=data.get(
                "pihole_password_file", "/etc/sentinel-agent/pihole-password"
            ),
            backup_paths=[str(p) for p in data.get("backup_paths") or []],
            backup_snapshot=data.get("backup_snapshot", backups.BACKUP_SNAPSHOT),
            mobile_port=int(data.get("mobile_port") or 0),
            mobile_clients=[str(c) for c in data.get("mobile_clients") or []],
            mobile_pin_file=data.get("mobile_pin_file", "/etc/sentinel-agent/mobile-pin"),
            vault_dir=data.get("vault_dir", vault.VAULT_DIR),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if len(self.token) < 32:
            raise ValueError("token must have at least 32 characters")
        if not is_private(self.bind):
            raise ValueError("bind address must be a private (RFC 1918) address")
        if not self.allowed_clients or not all(is_private(c) for c in self.allowed_clients):
            raise ValueError("allowed_clients must be private (RFC 1918) addresses")
        if not self.vault_dir.startswith("/"):
            raise ValueError("vault_dir must be an absolute path")
        if not all(p.startswith("/") for p in self.backup_paths):
            raise ValueError("backup_paths must be absolute paths")
        if self.mobile_port:
            if self.mobile_port == self.port or not 1024 <= self.mobile_port <= 65535:
                raise ValueError("mobile_port must be a free port >= 1024")
            if not self.mobile_clients:
                raise ValueError("mobile_clients is required when mobile_port is set")
            mobile.parse_networks(self.mobile_clients)


def is_private(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return isinstance(ip, ipaddress.IPv4Address) and any(ip in n for n in RFC1918)


class AuthLimiter:
    """Blocks an IP for one minute after too many failed attempts."""

    def __init__(self) -> None:
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def blocked(self, ip: str) -> bool:
        with self._lock:
            recent = [t for t in self._fails.get(ip, []) if t > time.time() - 60]
            self._fails[ip] = recent
            return len(recent) >= MAX_AUTH_FAILURES

    def fail(self, ip: str) -> None:
        with self._lock:
            self._fails.setdefault(ip, []).append(time.time())


def make_handler(
    config: AgentConfig, limiter: AuthLimiter, relay: mobile.Relay | None = None
) -> type[BaseHTTPRequestHandler]:
    relay = relay or mobile.Relay()
    routes: dict[str, Any] = {
        "/v1/status": collectors.status,
        "/v1/services": lambda: collectors.service_states(config.services),
        "/v1/auth-failures": lambda: collectors.auth_failures(
            collectors.read_auth_log(config.auth_log)
        ),
        "/v1/updates": collectors.updates,
        "/v1/docker": lambda: extras.docker_containers(config.docker_snapshot),
        "/v1/pihole": lambda: extras.PiholeClient(
            config.pihole_url, config.pihole_password_file
        ).stats(),
        "/v1/backups": lambda: backups.backup_status(config.backup_snapshot),
    }

    class Handler(BaseHTTPRequestHandler):
        server_version = "sentinel-agent"
        sys_version = ""

        def log_message(self, fmt: str, *args: Any) -> None:  # no token in the log
            log.info("%s %s", self.client_address[0], fmt % args)

        def _send(self, status: int, body: dict[str, Any]) -> None:
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def _reject_other(self) -> None:
            self._send(405, {"error": "method not allowed"})

        do_PUT = do_DELETE = do_PATCH = _reject_other  # noqa: N815

        def _authorized(self) -> bool:
            client = self.client_address[0]
            if client not in config.allowed_clients:
                self._send(403, {"error": "forbidden"})
                return False
            if limiter.blocked(client):
                self._send(429, {"error": "too many failed attempts"})
                return False
            scheme, _, value = self.headers.get("Authorization", "").partition(" ")
            if scheme != "Bearer" or not hmac.compare_digest(value.encode(), config.token.encode()):
                limiter.fail(client)
                self._send(401, {"error": "unauthorized"})
                return False
            return True

        def do_POST(self) -> None:
            if not self._authorized():
                return
            if self.path.startswith("/v1/vault/"):
                self._vault_post(self.path.removeprefix("/v1/vault/"))
                return
            if self.path != "/v1/relay":
                self._send(404, {"error": "not found"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > mobile.MAX_RELAY_BODY:
                self._send(413, {"error": "too large"})
                return
            try:
                commands = relay.report(json.loads(self.rfile.read(length) or b"{}"))
            except ValueError:
                self._send(400, {"error": "invalid status"})
                return
            self._send(200, {"commands": commands})

        def _vault_post(self, name: str) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            if length > vault.MAX_BODY:
                self._send(413, {"error": "too large"})
                return
            try:
                result = vault.store(config.vault_dir, name, json.loads(self.rfile.read(length)))
            except (ValueError, OSError) as exc:
                log.warning("vault upload rejected: %s", type(exc).__name__)
                self._send(400, {"error": "invalid backup"})
                return
            self._send(200, result)

        def do_GET(self) -> None:
            if self.client_address[0] not in config.allowed_clients:
                self._send(403, {"error": "forbidden"})
                return
            if self.path == "/health":
                self._send(200, {"status": "ok"})
                return
            if not self._authorized():
                return
            if self.path.startswith("/v1/vault/"):
                try:
                    found = vault.latest(config.vault_dir, self.path.removeprefix("/v1/vault/"))
                except (ValueError, OSError):
                    found = None
                if found is None:
                    self._send(404, {"error": "not found"})
                else:
                    self._send(200, found)
                return
            route = routes.get(self.path)
            if route is None:
                self._send(404, {"error": "not found"})
                return
            try:
                self._send(200, route())
            except Exception:
                log.exception("collector failed")
                self._send(500, {"error": "collector failed"})

    return Handler


def _wrap_tls(server: ThreadingHTTPServer, config: AgentConfig) -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(config.cert, config.key)
    server.socket = context.wrap_socket(server.socket, server_side=True)


def build_server(
    config: AgentConfig, *, tls: bool = True, relay: mobile.Relay | None = None
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(
        (config.bind, config.port), make_handler(config, AuthLimiter(), relay)
    )
    if tls:
        _wrap_tls(server, config)
    return server


def mobile_view(config: AgentConfig) -> dict[str, Any]:
    """Server data for the phone (without logs, without service names)."""
    try:
        status = collectors.status()
    except OSError:
        status = {}
    keys = ("load", "memory_percent", "disk_percent", "uptime_h", "reboot_required")
    return {
        "server": {k: status.get(k) for k in keys},
        "backups": backups.backup_status(config.backup_snapshot),
    }


def build_mobile_server(
    config: AgentConfig, relay: mobile.Relay, *, tls: bool = True
) -> ThreadingHTTPServer:
    handler = mobile.make_mobile_handler(
        mobile.parse_networks(config.mobile_clients),
        relay,
        mobile.MobileAuth(config.mobile_pin_file),
        extra_view=lambda: mobile_view(config),
    )
    server = ThreadingHTTPServer((config.bind, config.mobile_port), handler)
    if tls:
        _wrap_tls(server, config)
    return server
