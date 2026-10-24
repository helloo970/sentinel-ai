"""Client for the server agent on the Ubuntu server (read-only, token + TLS)."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx

from sentinel.config import Settings
from sentinel.monitor import rules
from sentinel.permissions.netguard import is_rfc1918

if TYPE_CHECKING:
    from sentinel.services import Services

log = logging.getLogger(__name__)

ENDPOINTS = ("status", "services", "auth-failures", "updates")
# Newer agent versions; older ones answer with 404 - that is not an error
OPTIONAL_ENDPOINTS = ("docker", "pihole", "backups")
POLL_INTERVAL_S = 300


class ServerAgentError(RuntimeError):
    pass


class ServerForbidden(ServerAgentError):  # noqa: N818 - domain name
    """The agent rejects this PC (wrong IP or wrong token)."""


def http_error(endpoint: str, status: int) -> ServerAgentError:
    if status == 403:
        return ServerForbidden(
            "Server rejects this PC (403): the PC's IP address has probably "
            "changed. Assign a fixed IP in the router or reinstall the agent with the new IP."
        )
    if status in (401, 429):
        return ServerForbidden(
            f"Server rejects the token ({status}) - check the token in the .env."
        )
    return ServerAgentError(f"{endpoint}: HTTP {status}")


class ServerAgentClient:
    def __init__(self, config: Settings) -> None:
        self.config = config
        self.last: dict[str, Any] | None = None
        self.last_poll = 0.0
        self.error: str | None = None
        self.container_states: dict[str, str] = {}

    @property
    def configured(self) -> bool:
        return bool(self.config.server_agent_url and self.config.server_agent_token)

    def _validate(self) -> str:
        url = self.config.server_agent_url or ""
        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise ServerAgentError("Server agent must be reachable via HTTPS")
        if not parsed.hostname or not is_rfc1918(parsed.hostname):
            raise ServerAgentError("Server agent address must be a private IP (RFC 1918)")
        if self.config.server_agent_ca is None or not self.config.server_agent_ca.is_file():
            raise ServerAgentError("Server certificate is missing (SENTINEL_SERVER_AGENT_CA)")
        return url.rstrip("/")

    async def fetch(self) -> dict[str, Any]:
        if not self.configured:
            raise ServerAgentError("Server agent is not configured")
        base = self._validate()
        token = self.config.server_agent_token.get_secret_value()  # type: ignore[union-attr]
        result: dict[str, Any] = {}
        async with httpx.AsyncClient(
            verify=str(self.config.server_agent_ca),
            timeout=10,
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=False,
        ) as client:
            for endpoint in ENDPOINTS:
                response = await client.get(f"{base}/v1/{endpoint}")
                if response.status_code != 200:
                    raise http_error(endpoint, response.status_code)
                result[endpoint.replace("-", "_")] = response.json()
            for endpoint in OPTIONAL_ENDPOINTS:
                response = await client.get(f"{base}/v1/{endpoint}")
                if response.status_code == 200:
                    result[endpoint] = response.json()
        self.last = result
        self.last_poll = time.time()
        self.error = None
        return result

    async def post_relay(self, payload: dict[str, Any]) -> list[dict[str, Any]] | None:
        """Report status for the phone view. None if the agent does not support it."""
        base = self._validate()
        token = self.config.server_agent_token.get_secret_value()  # type: ignore[union-attr]
        async with httpx.AsyncClient(
            verify=str(self.config.server_agent_ca),
            timeout=5,
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=False,
        ) as client:
            response = await client.post(f"{base}/v1/relay", json=payload)
        if response.status_code in (404, 405):
            return None
        if response.status_code != 200:
            raise http_error("relay", response.status_code)
        commands = response.json().get("commands", [])
        return [c for c in commands if isinstance(c, dict)] if isinstance(commands, list) else []

    def _client(self, timeout: float) -> httpx.AsyncClient:
        token = self.config.server_agent_token.get_secret_value()  # type: ignore[union-attr]
        return httpx.AsyncClient(
            verify=str(self.config.server_agent_ca),
            timeout=timeout,
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=False,
        )

    async def put_vault(self, name: str, data_b64: str) -> None:
        """Store an encrypted backup (the server only sees bytes)."""
        base = self._validate()
        async with self._client(30) as client:
            response = await client.post(f"{base}/v1/vault/{name}", json={"data": data_b64})
        if response.status_code in (404, 405):
            raise ServerAgentError("Server agent is too old - please update it")
        if response.status_code != 200:
            raise http_error("vault", response.status_code)

    async def get_vault(self, name: str) -> str | None:
        base = self._validate()
        async with self._client(30) as client:
            response = await client.get(f"{base}/v1/vault/{name}")
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise http_error("vault", response.status_code)
        data = response.json().get("data")
        return data if isinstance(data, str) else None

    def summary_for_rules(self, data: dict[str, Any]) -> dict[str, Any]:
        status = data.get("status", {})
        auth = data.get("auth_failures", {})
        services = data.get("services", {})
        return {
            "disk_percent": status.get("disk_percent", 0),
            "auth_failures_1h": auth.get("failures_1h", 0),
            "auth_top_sources": auth.get("top_sources", []),
            "failed_services": [
                s["name"]
                for s in services.get("services", [])
                if s.get("state") not in ("active", "inactive-ok")
            ],
            "hour_bucket": int(time.time() // 3600),
        }

    async def poll_and_check(self, svc: Services) -> None:
        if not self.configured or time.time() - self.last_poll < POLL_INTERVAL_S:
            return
        self.last_poll = time.time()
        try:
            data = await self.fetch()
        except (ServerAgentError, httpx.HTTPError, ValueError) as exc:
            self.error = str(exc) or type(exc).__name__
            return
        for finding in rules.check_server(self.summary_for_rules(data)):
            svc.alerts.handle(finding)
        extras = {
            **data,
            "hour_bucket": int(time.time() // 3600),
            "day_bucket": int(time.time() // 86400),
            "backup_max_age_h": svc.config.backup_max_age_h,
            "now": time.time(),
        }
        for finding in rules.check_server_extras(extras, self.container_states):
            svc.alerts.handle(finding)
        self.container_states = {
            c.get("name", "?"): c.get("state", "")
            for c in (data.get("docker") or {}).get("containers", [])
        }
        self.record_metrics(svc, data)

    @staticmethod
    def record_metrics(svc: Services, data: dict[str, Any]) -> None:
        status = data.get("status") or {}
        load = status.get("load") or [0.0]
        svc.db.execute(
            "INSERT INTO server_metrics(ts, load1, cpu_count, memory_percent, disk_percent, "
            "auth_failures) VALUES(?,?,?,?,?,?)",
            (
                time.time(),
                float(load[0] or 0),
                int(status.get("cpu_count") or 1),
                float(status.get("memory_percent") or 0),
                float(status.get("disk_percent") or 0),
                int((data.get("auth_failures") or {}).get("failures_1h") or 0),
            ),
        )
        svc.db.execute("DELETE FROM server_metrics WHERE ts < ?", (time.time() - 30 * 86400,))

    @staticmethod
    def history(svc: Services, days: int = 7) -> list[dict[str, Any]]:
        since = time.time() - max(1, min(days, 30)) * 86400
        return svc.db.query(
            "SELECT ts, load1, cpu_count, memory_percent, disk_percent, auth_failures "
            "FROM server_metrics WHERE ts >= ? ORDER BY ts",
            (since,),
        )

    def attackers(self, geoip: Any) -> list[dict[str, Any]]:
        sources = ((self.last or {}).get("auth_failures") or {}).get("top_sources") or []
        return [{**s, "country": geoip.country(str(s.get("ip", "")))} for s in sources]

    def status(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "last_poll": self.last_poll or None,
            "error": self.error,
            "data": self.last,
        }
