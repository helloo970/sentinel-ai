from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime

import pytest

from sentinel_agent import collectors
from sentinel_agent.server import AgentConfig, build_server, is_private

TOKEN = "a" * 40

AUTH_LOG = """2026-10-20T06:10:00.000000+02:00 srv sshd[1]: Failed password for root from 203.0.113.7 port 1 ssh2
2026-10-20T07:50:00.000000+02:00 srv sshd[1]: Failed password for invalid user admin from 203.0.113.7 port 2 ssh2
2026-10-20T07:55:00.000000+02:00 srv sshd[1]: Invalid user test from 198.51.100.4 port 3
2026-10-20T07:56:00.000000+02:00 srv sshd[1]: Accepted publickey for demo from 10.0.0.20 port 4
Oct 20 07:58:00 srv sshd[1]: Failed password for demo from 198.51.100.4 port 5 ssh2
"""


def test_auth_failures_counts_last_hour() -> None:
    result = collectors.auth_failures(AUTH_LOG, now=datetime(2026, 10, 20, 8, 0))
    assert result["failures_1h"] == 3
    assert result["top_sources"][0]["count"] == 2


def test_meminfo_and_os_name() -> None:
    info = collectors.meminfo("MemTotal: 8388608 kB\nMemAvailable: 2097152 kB\n")
    assert info == {"memory_total_gb": 8.0, "memory_percent": 75.0}
    assert collectors.os_name('PRETTY_NAME="Ubuntu 24.04 LTS"\n') == "Ubuntu 24.04 LTS"


def test_updates_parser() -> None:
    text = (
        "12 updates can be applied immediately.\n5 of these updates are standard security updates."
    )
    assert collectors.updates(text)["pending"] == 12
    assert collectors.updates(text)["security"] == 5


def test_invalid_service_names_are_not_executed() -> None:
    result = collectors.service_states(["ssh; rm -rf /", "$(id)"])
    assert all(s["state"] == "invalid-name" for s in result["services"])


@pytest.mark.parametrize(
    "bind,clients", [("203.0.113.1", ["10.0.0.2"]), ("10.0.0.1", ["203.0.113.2"]), ("10.0.0.1", [])]
)
def test_config_requires_private_addresses(bind: str, clients: list[str]) -> None:
    config = AgentConfig(bind, 8443, "c", "k", TOKEN, clients)
    with pytest.raises(ValueError):
        config.validate()


def test_short_token_rejected() -> None:
    with pytest.raises(ValueError):
        AgentConfig("10.0.0.1", 8443, "c", "k", "short", ["10.0.0.2"]).validate()


def test_is_private() -> None:
    assert is_private("192.168.1.1") and not is_private("203.0.113.1")


@pytest.fixture
def server():
    config = AgentConfig("127.0.0.1", 0, "", "", TOKEN, ["127.0.0.1"], services=[])
    srv = build_server(config, tls=False)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def get(url: str, token: str | None = None, method: str = "GET") -> tuple[int, dict]:
    request = urllib.request.Request(url, method=method)  # noqa: S310
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_requires_token(server: str) -> None:
    assert get(f"{server}/v1/updates")[0] == 401
    assert get(f"{server}/v1/updates", "wrong")[0] == 401
    status, body = get(f"{server}/v1/updates", TOKEN)
    assert status == 200 and "pending" in body


def test_only_get_and_known_paths(server: str) -> None:
    assert get(f"{server}/v1/status", TOKEN, method="POST")[0] == 404  # only /v1/relay
    assert get(f"{server}/v1/status", TOKEN, method="DELETE")[0] == 405
    assert get(f"{server}/v1/../etc/passwd", TOKEN)[0] == 404


def test_rate_limit_after_failures(server: str) -> None:
    for _ in range(10):
        get(f"{server}/v1/updates", "wrong")
    assert get(f"{server}/v1/updates", TOKEN)[0] == 429


def test_docker_snapshot_parsing(tmp_path) -> None:
    from sentinel_agent import extras

    snap = tmp_path / "docker.json"
    snap.write_text(
        '{"Names":"pihole","Image":"pihole/pihole","State":"running","Status":"Up 2 days (healthy)"}\n'
        '{"Names":"web","Image":"nginx","State":"restarting","Status":"Restarting (1) 5s ago"}\n'
        "kaputt\n",
        encoding="utf-8",
    )
    result = extras.docker_containers(str(snap))
    assert result["available"] and [c["name"] for c in result["containers"]] == ["pihole", "web"]
    assert result["containers"][0]["healthy"] is True
    assert result["containers"][1]["state"] == "restarting"
    assert extras.docker_containers(str(tmp_path / "fehlt.json"))["available"] is False


def test_pihole_only_local_and_parsing(tmp_path) -> None:
    from sentinel_agent import extras

    pw = tmp_path / "pw"
    pw.write_text("app-passwort")
    assert not extras.PiholeClient("https://192.168.1.5:8443", str(pw)).configured()
    assert not extras.PiholeClient("https://127.0.0.1:8443", str(tmp_path / "x")).configured()
    assert extras.PiholeClient("https://127.0.0.1:8443", str(pw)).configured()
    parsed = extras.parse_pihole(
        {
            "queries": {"total": 1000, "blocked": 120, "percent_blocked": 12.04},
            "clients": {"active": 7},
            "gravity": {"domains_being_blocked": 90000},
        },
        {"domains": [{"domain": "ads.example", "count": 50}]},
        {"clients": [{"ip": "10.0.0.20", "name": "pc", "count": 600}]},
    )
    assert parsed["percent_blocked"] == 12.0 and parsed["top_clients"][0]["count"] == 600
    assert extras.parse_pihole({}, {}, {})["queries_total"] == 0


def test_new_endpoints_require_token(server: str) -> None:
    assert get(f"{server}/v1/docker")[0] == 401
    status, body = get(f"{server}/v1/docker", TOKEN)
    assert status == 200 and "available" in body
    status, body = get(f"{server}/v1/pihole", TOKEN)
    assert status == 200 and body["configured"] is False
