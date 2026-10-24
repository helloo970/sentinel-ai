"""Extra collectors: Docker containers (from a root snapshot) and Pi-hole statistics (local API).

The agent deliberately has no access to the Docker socket (that would be practically root).
A root timer writes `docker ps` as JSON lines to a file every minute, which the agent
only reads. Pi-hole is only queried via 127.0.0.1 with an app password.
"""

from __future__ import annotations

import contextlib
import json
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DOCKER_SNAPSHOT = "/run/sentinel-agent/docker.json"
MAX_SNAPSHOT_AGE_S = 300
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def docker_containers(path: str = DOCKER_SNAPSHOT, now: float | None = None) -> dict[str, Any]:
    snapshot = Path(path)
    if not snapshot.is_file():
        return {"available": False, "containers": [], "reason": "no Docker snapshot"}
    age = (now or time.time()) - snapshot.stat().st_mtime
    containers = []
    for line in snapshot.read_text(encoding="utf-8", errors="replace").splitlines()[:300]:
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        status = str(raw.get("Status", ""))[:120]
        containers.append(
            {
                "name": str(raw.get("Names", ""))[:80],
                "image": str(raw.get("Image", ""))[:120],
                "state": str(raw.get("State", "")).lower()[:20],
                "status": status,
                "healthy": None
                if "health" not in status.lower()
                else "(healthy)" in status.lower(),
            }
        )
    return {
        "available": True,
        "stale": age > MAX_SNAPSHOT_AGE_S,
        "age_s": int(age),
        "containers": containers,
    }


class PiholeClient:
    """Reads Pi-hole v6 statistics. Local addresses only, app password from a file."""

    def __init__(self, url: str, password_file: str, timeout: float = 5.0) -> None:
        self.url = url.rstrip("/")
        self.password_file = password_file
        self.timeout = timeout

    def configured(self) -> bool:
        host = urlparse(self.url).hostname or ""
        return host in LOCAL_HOSTS and Path(self.password_file).is_file()

    def _context(self) -> ssl.SSLContext | None:
        if not self.url.startswith("https://"):
            return None
        # 127.0.0.1 only: Pi-hole uses a self-signed certificate; local, so no man in the middle
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    def _request(
        self, method: str, path: str, *, body: dict | None = None, sid: str | None = None
    ) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(  # noqa: S310 - fixed local URL
            f"{self.url}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json", **({"X-FTL-SID": sid} if sid else {})},
        )
        with urllib.request.urlopen(  # noqa: S310 - fixed local URL
            request,
            timeout=self.timeout,
            context=self._context(),
        ) as response:
            raw = response.read()
        return json.loads(raw) if raw else {}

    def stats(self) -> dict[str, Any]:
        if not self.configured():
            return {"configured": False}
        password = Path(self.password_file).read_text(encoding="utf-8").strip()
        try:
            auth = self._request("POST", "/api/auth", body={"password": password})
            sid = (auth.get("session") or {}).get("sid")
            if not sid:
                return {"configured": True, "error": "Pi-hole login failed"}
            try:
                summary = self._request("GET", "/api/stats/summary", sid=sid)
                blocked = self._request(
                    "GET", "/api/stats/top_domains?blocked=true&count=10", sid=sid
                )
                clients = self._request("GET", "/api/stats/top_clients?count=10", sid=sid)
            finally:
                with contextlib.suppress(urllib.error.URLError, OSError, ValueError):
                    self._request("DELETE", "/api/auth", sid=sid)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return {"configured": True, "error": f"Pi-hole not reachable ({type(exc).__name__})"}
        return parse_pihole(summary, blocked, clients)


def parse_pihole(summary: dict, blocked: dict, clients: dict) -> dict[str, Any]:
    queries = summary.get("queries") or {}
    return {
        "configured": True,
        "queries_total": int(queries.get("total") or 0),
        "queries_blocked": int(queries.get("blocked") or 0),
        "percent_blocked": round(float(queries.get("percent_blocked") or 0.0), 1),
        "clients_active": int((summary.get("clients") or {}).get("active") or 0),
        "domains_blocklist": int((summary.get("gravity") or {}).get("domains_being_blocked") or 0),
        "top_blocked": [
            {"domain": str(d.get("domain", ""))[:200], "count": int(d.get("count") or 0)}
            for d in (blocked.get("domains") or [])[:10]
        ],
        "top_clients": [
            {
                "ip": str(c.get("ip", ""))[:45],
                "name": str(c.get("name") or "")[:80],
                "count": int(c.get("count") or 0),
            }
            for c in (clients.get("clients") or [])[:10]
        ],
    }
