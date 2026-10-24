"""Server & home network: Docker, Pi-hole, updates, history, attacker countries, certificates, devices."""

from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding

from sentinel.monitor import certs, rules
from sentinel.network.lookup import GeoIP, OuiVendors
from sentinel.permissions import Severity, Source
from sentinel.services import Services


def test_server_extras_rules() -> None:
    data = {
        "day_bucket": 1,
        "hour_bucket": 1,
        "updates": {"pending": 12, "security": 3, "reboot_required": True},
        "docker": {
            "containers": [
                {"name": "pihole", "state": "running", "status": "Up (healthy)", "healthy": True},
                {"name": "web", "state": "restarting", "status": "Restarting (1)"},
                {"name": "db", "state": "running", "status": "Up (unhealthy)", "healthy": False},
                {"name": "job", "state": "exited", "status": "Exited (0)"},
                {"name": "app", "state": "exited", "status": "Exited (137)"},
            ]
        },
        "pihole": {
            "queries_total": 10000,
            "top_clients": [
                {"ip": "10.0.0.7", "name": "tv", "count": 6000},
                {"ip": "10.0.0.8", "name": "pc", "count": 1000},
            ],
        },
    }
    found = rules.check_server_extras(data, {"app": "running", "job": "exited"})
    titles = [f.title for f in found]
    assert any("3 security updates" in t for t in titles)
    assert any("restart" in t for t in titles)
    assert any("web" in t and "restarting" in t for t in titles)
    assert any("db" in t and "unhealthy" in t for t in titles)
    assert any("app" in t for t in titles)
    assert not any("job" in t for t in titles)  # had already finished
    spikes = [f for f in found if f.rule == "pihole_client_spike"]
    assert len(spikes) == 1 and "tv" in spikes[0].title


def test_no_spike_for_small_networks() -> None:
    data = {"pihole": {"queries_total": 100, "top_clients": [{"ip": "x", "count": 90}]}}
    assert rules.check_server_extras(data, {}) == []


async def test_poll_records_metrics_and_container_states(svc: Services, monkeypatch) -> None:
    from pydantic import SecretStr

    svc.config.server_agent_url = "https://10.0.0.5:8844"
    svc.config.server_agent_token = SecretStr("t" * 40)

    async def fake_fetch():
        data = {
            "status": {
                "load": [0.5, 0.4, 0.3],
                "cpu_count": 4,
                "memory_percent": 40,
                "disk_percent": 55,
            },
            "services": {"services": []},
            "auth_failures": {"failures_1h": 3, "top_sources": [{"ip": "203.0.113.9", "count": 3}]},
            "updates": {"pending": 0, "security": 0},
            "docker": {"containers": [{"name": "pihole", "state": "running"}]},
        }
        svc.server.last = data
        return data

    monkeypatch.setattr(svc.server, "fetch", fake_fetch)
    await svc.server.poll_and_check(svc)
    history = svc.server.history(svc, 7)
    assert len(history) == 1 and history[0]["disk_percent"] == 55
    assert svc.server.container_states == {"pihole": "running"}
    attackers = svc.server.attackers(svc.geoip)
    assert attackers[0]["ip"] == "203.0.113.9"


def test_geoip_lookup(tmp_path: Path) -> None:
    db = tmp_path / "geo.csv"
    db.write_text(
        "198.51.100.0,198.51.100.255,DE\n203.0.113.0,203.0.113.255,US\n2001:db8::,2001:db8::ff,FR\n",
        encoding="utf-8",
    )
    geo = GeoIP(db)
    assert geo.country("198.51.100.20") == "DE"
    assert geo.country("203.0.113.5") == "US"
    assert geo.country("192.0.2.1") is None
    assert geo.country("10.0.0.5") == "LAN"
    assert geo.country("2001:db8::1") is None
    assert geo.country("not-an-ip") is None
    assert GeoIP(tmp_path / "missing.csv").country("198.51.100.20") is None


def test_oui_vendor(tmp_path: Path) -> None:
    oui = tmp_path / "oui.csv"
    oui.write_text(
        "Registry,Assignment,Organization Name,Organization Address\n"
        'MA-L,AABBCC,"Beispiel GmbH",Irgendwo\n',
        encoding="utf-8",
    )
    vendors = OuiVendors(oui)
    assert vendors.vendor("a8:bb:cc:00:00:01") is None
    assert vendors.vendor("AA-BB-CC-00-00-01") == "Private address (random)"  # bit set
    oui.write_text(
        "Registry,Assignment,Organization Name\nMA-L,A8BBCC,Beispiel GmbH\n", encoding="utf-8"
    )
    assert OuiVendors(oui).vendor("a8:bb:cc:00:00:01") == "Beispiel GmbH"
    assert vendors.vendor("kaputt") is None


def make_cert(days: int) -> bytes:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "test")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now - dt.timedelta(days=400))
        .not_valid_after(now + dt.timedelta(days=days))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(Encoding.DER)


@pytest.mark.parametrize(
    "days,severity", [(10, Severity.WARN), (-2, Severity.CRITICAL), (200, None)]
)
def test_cert_expiry_findings(days: int, severity) -> None:
    status = certs.describe("10.0.0.5:8844", make_cert(days))
    assert status.subject == "test"
    finding = certs.finding_for(status)
    assert (finding.severity if finding else None) == severity


@pytest.mark.parametrize(
    "target", ["203.0.113.5:443", "10.0.0.5", "10.0.0.5:99999", "pi.hole:443", ""]
)
def test_cert_targets_must_be_private(target: str) -> None:
    assert certs.parse_target(target) is None


def test_cert_watch_raises_alerts(svc: Services, monkeypatch) -> None:
    svc.config.cert_watch = ["10.0.0.5:8443", "203.0.113.5:443"]
    monkeypatch.setattr(certs, "fetch_certificate", lambda host, port: make_cert(5))
    results = svc.certs.check()
    assert [r.error for r in results] == [None, "only private addresses host:port allowed"]
    assert svc.alerts.list()[0]["rule"] == "cert_expiring"


def test_device_profile_with_vendor_and_ports(svc: Services, monkeypatch) -> None:
    svc.config.home_subnet = "10.0.0.0/24"
    now = time.time()
    svc.db.execute(
        "INSERT INTO devices(mac, ip, first_seen, last_seen) VALUES(?,?,?,?)",
        ("aa:bb:cc:00:00:01", "10.0.0.1", now, now),
    )
    monkeypatch.setattr(
        svc.network, "port_check", lambda ip: [{"port": 22, "service": "SSH", "risky": False}]
    )
    outcome = svc.engine.request("port_check", {"ip": "10.0.0.1"}, source=Source.USER, actor="user")
    assert outcome.status == "executed"
    device = svc.network.devices()[0]
    assert device["ports"] == [{"port": 22, "service": "SSH", "risky": False}]
    assert device["ports_checked"] is not None
    assert device["vendor"] == "Private address (random)"
    json.dumps(device)  # must be serializable for the API


def test_wireshark_manuf_fallback(tmp_path: Path) -> None:
    (tmp_path / "manuf.txt").write_text(
        "# Kommentar\nA8:BB:CC\tBeispiel\tBeispiel GmbH\n00:1B:C5:00:00:00/36\tKlein\n"
        "00:50:56\tVMware\n",
        encoding="utf-8",
    )
    vendors = OuiVendors(tmp_path / "oui.csv")
    assert vendors.available
    assert vendors.vendor("a8:bb:cc:12:34:56") == "Beispiel GmbH"
    assert vendors.vendor("00:50:56:00:00:01") == "VMware"
