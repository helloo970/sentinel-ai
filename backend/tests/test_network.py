"""Network: RFC 1918 only, subnet limits, ARP parser, port check only for own devices."""

from __future__ import annotations

import ipaddress
import time

import pytest

from sentinel.network.inventory import parse_arp
from sentinel.permissions import Source
from sentinel.permissions.netguard import (
    ip_in_subnet,
    is_rfc1918,
    url_is_private,
    validate_home_subnet,
)
from sentinel.services import Services

ARP_OUTPUT = """
Schnittstelle: 10.0.0.20 --- 0x7
  Internetadresse       Physische Adresse     Typ
  10.0.0.1              aa-bb-cc-00-00-01     dynamisch
  10.0.0.33             aa-bb-cc-00-00-21     dynamisch
  10.0.0.255            ff-ff-ff-ff-ff-ff     statisch
  224.0.0.22            01-00-5e-00-00-16     statisch
  203.0.113.9           aa-bb-cc-00-00-99     dynamisch
"""


@pytest.mark.parametrize("ip", ["10.1.2.3", "172.16.0.1", "172.31.255.254", "192.168.0.10"])
def test_private_addresses(ip: str) -> None:
    assert is_rfc1918(ip)


@pytest.mark.parametrize(
    "ip",
    [
        "203.0.113.5",
        "198.51.100.1",
        "192.0.2.1",
        "172.32.0.1",
        "127.0.0.1",
        "169.254.1.1",
        "100.64.0.1",
        "::1",
        "fe80::1",
        "not-an-ip",
        "",
    ],
)
def test_non_private_addresses(ip: str) -> None:
    assert not is_rfc1918(ip)


def test_subnet_validation() -> None:
    assert str(validate_home_subnet("10.0.0.7/24")) == "10.0.0.0/24"
    for bad in ("203.0.113.0/24", "10.0.0.0/8", "10.0.0.0/21", "fd00::/64"):
        with pytest.raises(ValueError):
            validate_home_subnet(bad)


def test_ip_in_subnet() -> None:
    net = ipaddress.IPv4Network("10.0.0.0/24")
    assert ip_in_subnet("10.0.0.5", net)
    assert not ip_in_subnet("10.0.1.5", net)
    assert not ip_in_subnet("203.0.113.5", None)


def test_private_url_check() -> None:
    assert url_is_private("http://10.0.0.5:8080")
    assert not url_is_private("http://203.0.113.5")
    assert not url_is_private("ftp://10.0.0.5")
    assert url_is_private("https://10.0.0.5", require_https=True)
    assert not url_is_private("http://10.0.0.5", require_https=True)


def test_parse_arp_filters_broadcast_multicast_and_foreign() -> None:
    devices = parse_arp(ARP_OUTPUT, ipaddress.IPv4Network("10.0.0.0/24"))
    assert devices == [
        {"ip": "10.0.0.1", "mac": "aa:bb:cc:00:00:01"},
        {"ip": "10.0.0.33", "mac": "aa:bb:cc:00:00:21"},
    ]


@pytest.fixture
def lan(svc: Services) -> Services:
    svc.config.home_subnet = "10.0.0.0/24"
    now = time.time()
    svc.db.execute(
        "INSERT INTO devices(mac, ip, first_seen, last_seen) VALUES(?,?,?,?)",
        ("aa:bb:cc:00:00:01", "10.0.0.1", now, now),
    )
    return svc


@pytest.mark.parametrize("ip", ["203.0.113.5", "10.0.1.1", "10.0.0.77", "127.0.0.1"])
def test_port_check_denied_for_foreign_or_unknown(lan: Services, ip: str) -> None:
    outcome = lan.engine.request("port_check", {"ip": ip}, source=Source.USER, actor="user")
    assert outcome.status == "denied"


def test_port_check_known_device(lan: Services, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        lan.network, "port_check", lambda ip: [{"port": 445, "service": "SMB", "risky": True}]
    )
    outcome = lan.engine.request(
        "port_check", {"ip": "10.0.0.1"}, source=Source.ASSISTANT, actor="jarvis"
    )
    assert outcome.status == "executed"
    assert "SMB" in outcome.message


def test_mark_device_with_undo(lan: Services) -> None:
    outcome = lan.engine.request(
        "mark_device",
        {"mac": "AA-BB-CC-00-00-01", "label": "Router"},
        source=Source.ASSISTANT,
        actor="jarvis",
    )
    assert outcome.status == "executed"
    assert lan.network.get_device("aa:bb:cc:00:00:01")["label"] == "Router"
    lan.engine.undo(outcome.undo_id)
    assert lan.network.get_device("aa:bb:cc:00:00:01")["label"] is None


def test_new_device_alert_after_baseline(lan: Services, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        lan.network,
        "read_arp",
        lambda: [
            {"ip": "10.0.0.1", "mac": "aa:bb:cc:00:00:01"},
            {"ip": "10.0.0.50", "mac": "aa:bb:cc:00:00:50"},
        ],
    )
    result = lan.network.refresh(resolve_names=False)
    assert result["new"] == 1
    alerts = lan.alerts.list()
    assert alerts and alerts[0]["rule"] == "new_device"
