"""Offline lookup: country of an IPv4 address and vendor of a MAC address.

Data lives locally in <data_dir>/geo/ (scripts/download-network-data.ps1):
- dbip-country-lite.csv  (IP Geolocation by DB-IP, CC BY 4.0)
- oui.csv                (IEEE registration of the MAC prefixes)
No addresses are sent to foreign services.
"""

from __future__ import annotations

import bisect
import csv
import ipaddress
import logging
import threading
from pathlib import Path

from sentinel.permissions.netguard import is_rfc1918

log = logging.getLogger(__name__)


class GeoIP:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._starts: list[int] = []
        self._ends: list[int] = []
        self._countries: list[str] = []
        self._loaded = False
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self.path.is_file()

    def _load(self) -> None:
        with self._lock:
            if self._loaded:
                return
            self._loaded = True
            if not self.path.is_file():
                return
            rows: list[tuple[int, int, str]] = []
            with self.path.open(encoding="utf-8", newline="") as handle:
                for row in csv.reader(handle):
                    if len(row) < 3 or ":" in row[0]:
                        continue  # IPv4 only
                    try:
                        start = int(ipaddress.IPv4Address(row[0]))
                        end = int(ipaddress.IPv4Address(row[1]))
                    except ValueError:
                        continue
                    rows.append((start, end, row[2].strip().upper()[:2]))
            rows.sort()
            self._starts = [r[0] for r in rows]
            self._ends = [r[1] for r in rows]
            self._countries = [r[2] for r in rows]
            log.info("geoip loaded %d ranges", len(rows))

    def country(self, address: str) -> str | None:
        """ISO alpha-2 code, 'LAN' for private addresses, None if unknown."""
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return None
        if is_rfc1918(address) or ip.is_loopback:
            return "LAN"
        if not isinstance(ip, ipaddress.IPv4Address):
            return None
        self._load()
        value = int(ip)
        index = bisect.bisect_right(self._starts, value) - 1
        if index >= 0 and self._ends[index] >= value:
            return self._countries[index]
        return None


def parse_manuf(text: str) -> dict[str, str]:
    """Wireshark manuf: 'AA:BB:CC<TAB>Short<TAB>Long name' - full 24-bit prefixes only."""
    vendors: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        prefix = parts[0].strip()
        if "/" in prefix or len(parts) < 2:
            continue
        hexdigits = prefix.replace(":", "").replace("-", "").upper()
        if len(hexdigits) == 6:
            name = (parts[2] if len(parts) > 2 and parts[2].strip() else parts[1]).strip()
            vendors[hexdigits] = name[:80]
    return vendors


class OuiVendors:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._vendors: dict[str, str] | None = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self.path.is_file() or self.manuf_path.is_file()

    @property
    def manuf_path(self) -> Path:
        return self.path.with_name("manuf.txt")

    def _load(self) -> dict[str, str]:
        with self._lock:
            if self._vendors is None:
                vendors: dict[str, str] = {}
                if self.path.is_file():
                    with self.path.open(encoding="utf-8", errors="replace", newline="") as handle:
                        for row in csv.reader(handle):
                            if len(row) >= 3 and len(row[1]) == 6:
                                vendors[row[1].upper()] = row[2].strip()[:80]
                elif self.manuf_path.is_file():
                    vendors = parse_manuf(
                        self.manuf_path.read_text(encoding="utf-8", errors="replace")
                    )
                self._vendors = vendors
            return self._vendors

    def vendor(self, mac: str) -> str | None:
        hexdigits = mac.replace(":", "").replace("-", "").upper()
        if len(hexdigits) != 12:
            return None
        if int(hexdigits[1], 16) & 0b10:
            # Locally administered (random) address, typical for phones with a private Wi-Fi address
            return "Private address (random)"
        return self._load().get(hexdigits[:6])
