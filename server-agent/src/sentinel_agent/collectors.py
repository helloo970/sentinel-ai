"""Data collectors for the server agent (read-only, no changes to the system)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

SERVICE_RE = re.compile(r"^[A-Za-z0-9@._\-]{1,64}$")
SYSTEMCTL = "/usr/bin/systemctl"

FAILED_RE = re.compile(
    r"(?:Failed password for (?:invalid user )?(?P<user1>\S+)|Invalid user (?P<user2>\S+))"
    r" from (?P<ip>[0-9a-fA-F.:]+)"
)
ISO_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")
SYSLOG_TS = re.compile(r"^([A-Z][a-z]{2}\s+\d{1,2} \d{2}:\d{2}:\d{2})")


def _read(path: str, default: str = "") -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return default


def os_name(os_release: str | None = None) -> str:
    text = os_release if os_release is not None else _read("/etc/os-release")
    match = re.search(r'^PRETTY_NAME="?([^"\n]+)"?', text, re.MULTILINE)
    return match.group(1) if match else "Linux"


def meminfo(text: str | None = None) -> dict[str, float]:
    text = text if text is not None else _read("/proc/meminfo")
    values = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        if key in ("MemTotal", "MemAvailable"):
            values[key] = int(rest.split()[0]) / 1024 / 1024
    total = values.get("MemTotal", 0.0)
    available = values.get("MemAvailable", 0.0)
    used_percent = round((1 - available / total) * 100, 1) if total else 0.0
    return {"memory_total_gb": round(total, 1), "memory_percent": used_percent}


def status() -> dict[str, Any]:
    uptime_s = float((_read("/proc/uptime", "0").split() or ["0"])[0])
    load = os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)
    disk = shutil.disk_usage("/")
    return {
        "os": os_name(),
        "kernel": os.uname().release if hasattr(os, "uname") else "",
        "uptime_h": round(uptime_s / 3600, 1),
        "load": [round(x, 2) for x in load],
        "cpu_count": os.cpu_count(),
        "disk_percent": round(disk.used / disk.total * 100, 1) if disk.total else 0.0,
        "disk_free_gb": round(disk.free / 2**30, 1),
        **meminfo(),
        "reboot_required": Path("/var/run/reboot-required").exists(),
    }


def service_states(names: list[str]) -> dict[str, Any]:
    services = []
    for name in names:
        if not SERVICE_RE.match(name):
            services.append({"name": name, "state": "invalid-name"})
            continue
        try:
            proc = subprocess.run(  # noqa: S603 - fixed path, name validated
                [SYSTEMCTL, "is-active", name], capture_output=True, timeout=5, check=False
            )
            state = proc.stdout.decode().strip() or "unknown"
        except (OSError, subprocess.TimeoutExpired):
            state = "unknown"
        services.append({"name": name, "state": state})
    return {"services": services}


def _line_time(line: str, now: datetime) -> datetime | None:
    if match := ISO_TS.match(line):
        return datetime.fromisoformat(match.group(1))
    if match := SYSLOG_TS.match(line):
        parsed = datetime.strptime(f"{now.year} {match.group(1)}", "%Y %b %d %H:%M:%S")
        if parsed > now + timedelta(days=1):
            parsed = parsed.replace(year=now.year - 1)
        return parsed
    return None


def auth_failures(log_text: str, now: datetime | None = None, window_h: int = 1) -> dict[str, Any]:
    now = now or datetime.now()
    since = now - timedelta(hours=window_h)
    sources: Counter[str] = Counter()
    users: Counter[str] = Counter()
    total = 0
    for line in log_text.splitlines()[-20000:]:
        match = FAILED_RE.search(line)
        if not match:
            continue
        stamp = _line_time(line, now)
        if stamp is None or stamp < since:
            continue
        total += 1
        sources[match.group("ip")] += 1
        users[(match.group("user1") or match.group("user2") or "?")[:32]] += 1
    return {
        "failures_1h": total,
        "top_sources": [{"ip": ip, "count": n} for ip, n in sources.most_common(20)],
        "top_users": [{"user": u, "count": n} for u, n in users.most_common(5)],
        "checked_at": time.time(),
    }


def read_auth_log(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 2_000_000))
            return handle.read()
    except OSError:
        return ""


def updates(text: str | None = None) -> dict[str, Any]:
    text = text if text is not None else _read("/var/lib/update-notifier/updates-available")
    pending = re.search(r"(\d+) (?:updates?|Aktualisierungen)", text)
    security = re.search(
        r"(\d+) (?:of these updates are|davon sind) .*?(?:security|Sicherheit)", text
    )
    return {
        "pending": int(pending.group(1)) if pending else 0,
        "security": int(security.group(1)) if security else 0,
        "reboot_required": Path("/var/run/reboot-required").exists(),
    }
