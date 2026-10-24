"""Device inventory of your own subnet (RFC 1918 only).

Read the ARP table, optional ping sweep, port check of individual own devices.
All external programs are started with an absolute path and a fixed argument list.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psutil

from sentinel.monitor.rules import Finding, _fp
from sentinel.permissions.models import ActionDenied, Severity
from sentinel.permissions.netguard import MAX_PREFIX, RFC1918, ip_in_subnet, is_rfc1918
from sentinel.winproc import NO_WINDOW

if TYPE_CHECKING:
    from sentinel.services import Services

log = logging.getLogger(__name__)

ARP_LINE = re.compile(
    r"^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+([0-9a-fA-F]{2}(?:[-:][0-9a-fA-F]{2}){5})\b"
)
COMMON_PORTS = (21, 22, 23, 53, 80, 139, 443, 445, 554, 1883, 3389, 5900, 8080, 8443, 9100)
PORT_NAMES = {
    21: "FTP",
    22: "SSH",
    23: "Telnet",
    53: "DNS",
    80: "HTTP",
    139: "NetBIOS",
    443: "HTTPS",
    445: "SMB",
    554: "RTSP",
    1883: "MQTT",
    3389: "RDP",
    5900: "VNC",
    8080: "HTTP-Alt",
    8443: "HTTPS-Alt",
    9100: "Printer",
}
RISKY_PORTS = {21, 23, 445, 3389, 5900}


def _system32(exe: str) -> Path:
    return Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / exe


def normalize_mac(mac: str) -> str:
    return mac.lower().replace("-", ":")


def is_unicast_mac(mac: str) -> bool:
    first = int(mac.split(":")[0], 16)
    return mac != "ff:ff:ff:ff:ff:ff" and not first & 1


def parse_arp(output: str, subnet: ipaddress.IPv4Network | None) -> list[dict[str, str]]:
    devices = []
    for line in output.splitlines():
        match = ARP_LINE.match(line)
        if not match:
            continue
        ip, mac = match.group(1), normalize_mac(match.group(2))
        if not ip_in_subnet(ip, subnet) or not is_unicast_mac(mac):
            continue
        if subnet is not None and ip == str(subnet.broadcast_address):
            continue
        devices.append({"ip": ip, "mac": mac})
    return devices


def _default_route_ip() -> str | None:
    """Local address of the default route (UDP connect sends no packets)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            return str(probe.getsockname()[0])
    except OSError:
        return None


VIRTUAL_MARKERS = (
    "vethernet",
    "virtualbox",
    "vmware",
    "hyper-v",
    "wsl",
    "loopback",
    "docker",
    "vpn",
    "tap",
    "tun",
    "zerotier",
    "tailscale",
    "bluetooth",
)


def detect_subnet() -> ipaddress.IPv4Network | None:
    """Determines the private subnet of the active, physical network adapter.

    Virtual adapters (WSL, Hyper-V, VPN ...) are skipped; large networks are
    limited to /24 around the own address.
    """
    stats = psutil.net_if_stats()
    primary = _default_route_ip()
    candidates: list[tuple[int, ipaddress.IPv4Network]] = []
    for name, addrs in psutil.net_if_addrs().items():
        if not stats.get(name) or not stats[name].isup:
            continue
        if any(marker in name.lower() for marker in VIRTUAL_MARKERS):
            continue
        for addr in addrs:
            if addr.family != socket.AF_INET or not addr.netmask or not is_rfc1918(addr.address):
                continue
            net = ipaddress.IPv4Network(f"{addr.address}/{addr.netmask}", strict=False)
            if net.prefixlen < MAX_PREFIX:
                net = ipaddress.IPv4Network(f"{addr.address}/24", strict=False)
            # Home networks are mostly in 192.168/16, then 10/8, last 172.16/12
            if addr.address == primary:
                rank = -1  # Adapter with the default route wins
            elif addr.address.startswith("192.168."):
                rank = 0
            else:
                rank = 1 if addr.address.startswith("10.") else 2
            candidates.append((rank, net))
    candidates.sort(key=lambda c: c[0])
    return candidates[0][1] if candidates else None


class NetworkInventory:
    def __init__(self, svc: Services) -> None:
        self.svc = svc

    def subnet(self) -> ipaddress.IPv4Network | None:
        return self.svc.config.subnet() or detect_subnet()

    def read_arp(self) -> list[dict[str, str]]:
        if sys.platform != "win32":
            return []
        try:
            proc = subprocess.run(  # noqa: S603 - fixed path and fixed arguments
                [str(_system32("ARP.EXE")), "-a"],
                capture_output=True,
                timeout=15,
                check=False,
                shell=False,
                creationflags=NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        return parse_arp(proc.stdout.decode("utf-8", "replace"), self.subnet())

    def _ping(self, ip: str) -> bool:
        try:
            proc = subprocess.run(  # noqa: S603 - fixed path, IP checked beforehand
                [str(_system32("PING.EXE")), "-n", "1", "-w", "400", ip],
                capture_output=True,
                timeout=5,
                check=False,
                shell=False,
                creationflags=NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return proc.returncode == 0

    def sweep(self) -> int:
        subnet = self.subnet()
        if subnet is None:
            raise ActionDenied("No private subnet found or configured")
        if subnet.prefixlen < MAX_PREFIX or not any(subnet.subnet_of(p) for p in RFC1918):
            raise ActionDenied("Subnet is not allowed")
        if sys.platform != "win32":
            raise ActionDenied("Ping sweep is only implemented on Windows")
        hosts = [str(h) for h in subnet.hosts()]
        with ThreadPoolExecutor(max_workers=32) as pool:
            alive = sum(pool.map(self._ping, hosts))
        return alive

    def _hostname(self, ip: str) -> str | None:
        try:
            return socket.gethostbyaddr(ip)[0][:100]
        except OSError:
            return None

    def refresh(self, *, resolve_names: bool = True) -> dict[str, Any]:
        """Read ARP, store devices, report new devices."""
        now = time.time()
        seen = self.read_arp()
        first_inventory = self.svc.db.query_one("SELECT COUNT(*) AS n FROM devices")["n"] == 0
        new_devices = []
        names: dict[str, str | None] = {}
        if resolve_names and seen:
            with ThreadPoolExecutor(max_workers=16) as pool:
                for device, name in zip(
                    seen, pool.map(self._hostname, [d["ip"] for d in seen]), strict=True
                ):
                    names[device["mac"]] = name
        for device in seen:
            row = self.svc.db.query_one("SELECT mac FROM devices WHERE mac = ?", (device["mac"],))
            if row is None:
                self.svc.db.execute(
                    "INSERT INTO devices(mac, ip, hostname, first_seen, last_seen) "
                    "VALUES(?,?,?,?,?)",
                    (device["mac"], device["ip"], names.get(device["mac"]), now, now),
                )
                new_devices.append(device)
            else:
                self.svc.db.execute(
                    "UPDATE devices SET ip = ?, last_seen = ?, "
                    "hostname = COALESCE(?, hostname) WHERE mac = ?",
                    (device["ip"], now, names.get(device["mac"]), device["mac"]),
                )
        if not first_inventory:
            for device in new_devices:
                self.svc.alerts.handle(
                    Finding(
                        rule="new_device",
                        severity=Severity.WARN,
                        title="New device on the home network: "
                        + (self.svc.oui.vendor(device["mac"]) or device["ip"]),
                        detail={
                            "ip": device["ip"],
                            "mac": device["mac"],
                            "hostname": names.get(device["mac"]),
                        },
                        fingerprint=_fp("device", device["mac"]),
                    )
                )
        return {"seen": len(seen), "new": len(new_devices), "baseline": first_inventory}

    def devices(self) -> list[dict[str, Any]]:
        rows = self.svc.db.query("SELECT * FROM devices ORDER BY last_seen DESC")
        for row in rows:
            row["trusted"] = bool(row["trusted"])
            row["vendor"] = self.svc.oui.vendor(row["mac"]) if self.svc.oui else None
            row["ports"] = json.loads(row["ports"]) if row.get("ports") else None
        return rows

    def store_ports(self, ip: str, ports: list[dict[str, Any]]) -> None:
        self.svc.db.execute(
            "UPDATE devices SET ports = ?, ports_checked = ? WHERE ip = ?",
            (json.dumps(ports), time.time(), ip),
        )

    def get_device(self, mac: str) -> dict[str, Any] | None:
        return self.svc.db.query_one("SELECT * FROM devices WHERE mac = ?", (normalize_mac(mac),))

    def set_device(self, mac: str, *, label: str | None, trusted: bool | None) -> None:
        self.svc.db.execute(
            "UPDATE devices SET label = ?, trusted = ? WHERE mac = ?",
            (label, int(bool(trusted)), normalize_mac(mac)),
        )

    def check_target(self, ip: str) -> None:
        if not is_rfc1918(ip):
            raise ActionDenied("Only private addresses (RFC 1918) are allowed")
        if not ip_in_subnet(ip, self.subnet()):
            raise ActionDenied("Address is not in your own subnet")
        if self.svc.db.query_one("SELECT mac FROM devices WHERE ip = ?", (ip,)) is None:
            raise ActionDenied("Unknown device - update the inventory first")

    def port_check(self, ip: str, timeout: float = 0.6) -> list[dict[str, Any]]:
        def probe(port: int) -> dict[str, Any] | None:
            try:
                with socket.create_connection((ip, port), timeout=timeout):
                    return {
                        "port": port,
                        "service": PORT_NAMES.get(port, "?"),
                        "risky": port in RISKY_PORTS,
                    }
            except OSError:
                return None

        with ThreadPoolExecutor(max_workers=len(COMMON_PORTS)) as pool:
            results = [r for r in pool.map(probe, COMMON_PORTS) if r]
        return results
