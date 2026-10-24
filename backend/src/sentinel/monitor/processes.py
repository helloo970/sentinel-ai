"""Process and port overview via psutil, plus protection of system processes."""

from __future__ import annotations

import os
import platform
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import psutil

# These processes are never ended, no matter what a rule says
PROTECTED_NAMES = {
    "system",
    "system idle process",
    "idle",
    "registry",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "services.exe",
    "lsass.exe",
    "lsaiso.exe",
    "winlogon.exe",
    "svchost.exe",
    "explorer.exe",
    "dwm.exe",
    "fontdrvhost.exe",
    "sihost.exe",
    "taskhostw.exe",
    "msmpeng.exe",
    "securityhealthservice.exe",
    "spoolsv.exe",
    "memory compression",
    "conhost.exe",
    "ctfmon.exe",
    "runtimebroker.exe",
    "searchhost.exe",
    "startmenuexperiencehost.exe",
    "sysmon.exe",
    "sysmon64.exe",
    "systemd",
    "init",
    "launchd",
    "kernel_task",
}

SUSPICIOUS_DIR_MARKERS = (
    os.sep + "temp" + os.sep,
    os.sep + "tmp" + os.sep,
    os.sep + "downloads" + os.sep,
    os.sep + "appdata" + os.sep + "local" + os.sep + "temp" + os.sep,
)


@dataclass
class ProcessInfo:
    pid: int
    ppid: int
    name: str
    exe: str
    username: str
    cpu: float
    memory_mb: float
    started: float


def system_info() -> dict[str, Any]:
    vm = psutil.virtual_memory()
    disk_root = Path.home().anchor or "/"
    disk = psutil.disk_usage(disk_root)
    return {
        "os": f"{platform.system()} {platform.release()}",
        "hostname_hidden": True,
        "cpu_percent": psutil.cpu_percent(interval=0.2),
        "cpu_count": psutil.cpu_count(),
        "memory_total_gb": round(vm.total / 2**30, 1),
        "memory_percent": vm.percent,
        "disk_percent": disk.percent,
        "disk_free_gb": round(disk.free / 2**30, 1),
        "uptime_h": round((time.time() - psutil.boot_time()) / 3600, 1),
        "process_count": len(psutil.pids()),
    }


def list_processes() -> list[ProcessInfo]:
    result = []
    attrs = ["pid", "ppid", "name", "exe", "username", "cpu_percent", "memory_info", "create_time"]
    for proc in psutil.process_iter(attrs):
        info = proc.info
        mem = info.get("memory_info")
        result.append(
            ProcessInfo(
                pid=info["pid"],
                ppid=info.get("ppid") or 0,
                name=info.get("name") or "?",
                exe=info.get("exe") or "",
                username=info.get("username") or "",
                cpu=float(info.get("cpu_percent") or 0.0),
                memory_mb=round((mem.rss if mem else 0) / 2**20, 1),
                started=float(info.get("create_time") or 0.0),
            )
        )
    return result


def top_processes(limit: int = 15, name_filter: str | None = None) -> list[dict[str, Any]]:
    procs = list_processes()
    if name_filter:
        needle = name_filter.lower()
        procs = [p for p in procs if needle in p.name.lower()]
    procs.sort(key=lambda p: p.memory_mb, reverse=True)
    return [asdict(p) for p in procs[:limit]]


def list_listening_ports() -> list[dict[str, Any]]:
    names: dict[int, str] = {}
    try:
        conns = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, OSError):
        return []
    result = []
    for conn in conns:
        if conn.status != psutil.CONN_LISTEN or not conn.laddr:
            continue
        pid = conn.pid or 0
        if pid and pid not in names:
            try:
                names[pid] = psutil.Process(pid).name()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                names[pid] = "?"
        result.append(
            {
                "address": conn.laddr.ip,
                "port": conn.laddr.port,
                "pid": pid,
                "process": names.get(pid, "?"),
                "public": conn.laddr.ip not in ("127.0.0.1", "::1"),
            }
        )
    result.sort(key=lambda r: r["port"])
    return result


def is_suspicious_location(exe: str) -> bool:
    lowered = os.path.normcase(exe).lower()
    return any(marker in lowered for marker in SUSPICIOUS_DIR_MARKERS)


def protection_reason(pid: int) -> str | None:
    """Reason why a process must not be ended, or None."""
    if pid <= 4:
        return "Kernel/system process"
    own = os.getpid()
    if pid == own:
        return "SENTINEL itself"
    try:
        me = psutil.Process(own)
        if pid in {p.pid for p in me.parents()}:
            return "Parent process of SENTINEL"
        proc = psutil.Process(pid)
        name = proc.name().lower()
        exe = proc.exe() if proc.exe() else ""
        username = proc.username() or ""
    except psutil.NoSuchProcess:
        return "Process does not exist"
    except psutil.AccessDenied:
        return "No access (probably a system process)"
    if name in PROTECTED_NAMES:
        return f"Protected system process ({name})"
    system_root = os.path.normcase(os.environ.get("SystemRoot", r"C:\Windows"))
    if exe and os.path.normcase(exe).startswith(system_root + os.sep):
        return "Program from the Windows directory"
    if username.upper().startswith("NT AUTHORITY") or username.lower() == "root":
        return "Runs as a system account"
    return None
