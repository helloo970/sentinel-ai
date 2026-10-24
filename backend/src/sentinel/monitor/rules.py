"""Fixed detection rules. Only these may suggest autonomous protective measures.

All texts in `detail` come from the system (process names, paths, command lines)
and are treated as untrusted.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any

from sentinel.monitor.processes import is_suspicious_location
from sentinel.permissions.models import Severity

OFFICE_PARENTS = {
    "winword.exe",
    "excel.exe",
    "powerpnt.exe",
    "outlook.exe",
    "onenote.exe",
    "msaccess.exe",
    "mspub.exe",
    "acrord32.exe",
    "acrobat.exe",
}
SCRIPT_CHILDREN = {
    "powershell.exe",
    "pwsh.exe",
    "cmd.exe",
    "wscript.exe",
    "cscript.exe",
    "mshta.exe",
    "rundll32.exe",
    "regsvr32.exe",
    "certutil.exe",
    "bitsadmin.exe",
}
RISKY_EXTENSIONS = {
    ".exe",
    ".dll",
    ".scr",
    ".bat",
    ".cmd",
    ".ps1",
    ".vbs",
    ".vbe",
    ".js",
    ".jse",
    ".wsf",
    ".hta",
    ".msi",
    ".lnk",
    ".com",
    ".pif",
    ".jar",
}
DOC_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".txt",
    ".jpg",
    ".jpeg",
    ".png",
    ".zip",
    ".rtf",
    ".mp3",
    ".mp4",
}
DOWNLOAD_TEMP_EXTENSIONS = {".crdownload", ".part", ".tmp", ".partial", ".download"}


@dataclass
class Finding:
    rule: str
    severity: Severity
    title: str
    detail: dict[str, Any]
    fingerprint: str
    response: tuple[str, dict[str, Any]] | None = None
    notify: bool = field(default=True)


def _fp(*parts: object) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:24]


def _base(path: str) -> str:
    return PurePath(path.replace("\\", "/")).name.lower() if path else ""


def check_processes(
    processes: list[dict[str, Any]], listening: list[dict[str, Any]]
) -> list[Finding]:
    findings: list[Finding] = []
    listen_pids = {p["pid"]: p for p in listening if p["public"]}
    for proc in processes:
        exe = proc.get("exe") or ""
        if not exe or not is_suspicious_location(exe):
            continue
        listener = listen_pids.get(proc["pid"])
        if listener:
            findings.append(
                Finding(
                    rule="temp_listener",
                    severity=Severity.CRITICAL,
                    title="Program from Temp/Downloads is listening on the network",
                    detail={"pid": proc["pid"], "exe": exe, "port": listener["port"]},
                    fingerprint=_fp("temp_listener", exe, listener["port"]),
                    response=("kill_process", {"pid": proc["pid"], "reason": "temp_listener"}),
                )
            )
        else:
            findings.append(
                Finding(
                    rule="temp_exec",
                    severity=Severity.WARN,
                    title="Program is running from Temp/Downloads",
                    detail={"pid": proc["pid"], "exe": exe},
                    fingerprint=_fp("temp_exec", exe),
                )
            )
    return findings


def check_new_entries(
    kind: str,
    keys: dict[str, dict[str, Any]],
    baseline: set[str],
    *,
    title: str,
    severity: Severity,
) -> list[Finding]:
    return [
        Finding(
            rule=f"new_{kind}",
            severity=severity,
            title=title,
            detail=entry,
            fingerprint=_fp(kind, key),
        )
        for key, entry in keys.items()
        if key not in baseline
    ]


def check_sysmon(events: list[dict[str, Any]]) -> list[Finding]:
    findings: list[Finding] = []
    for event in events:
        data = event.get("data", {})
        eid = event.get("event_id")
        if eid == 1:
            parent = _base(data.get("ParentImage", ""))
            child = _base(data.get("Image", ""))
            if parent in OFFICE_PARENTS and child in SCRIPT_CHILDREN:
                pid = int(data.get("ProcessId", "0") or 0)
                findings.append(
                    Finding(
                        rule="office_spawns_shell",
                        severity=Severity.CRITICAL,
                        title=f"{parent} starts {child}",
                        detail={
                            "pid": pid,
                            "parent": data.get("ParentImage", ""),
                            "image": data.get("Image", ""),
                            "command_line": data.get("CommandLine", "")[:500],
                        },
                        fingerprint=_fp("office_shell", event.get("record")),
                        response=("kill_process", {"pid": pid, "reason": "office_spawns_shell"})
                        if pid > 4
                        else None,
                    )
                )
        elif eid in (8, 10) and _base(data.get("TargetImage", "")) == "lsass.exe":
            source = _base(data.get("SourceImage", ""))
            if source not in {
                "msmpeng.exe",
                "svchost.exe",
                "wininit.exe",
                "csrss.exe",
                "lsm.exe",
                "wmiprvse.exe",
                "taskmgr.exe",
            }:
                findings.append(
                    Finding(
                        rule="lsass_access",
                        severity=Severity.CRITICAL,
                        title="Access to lsass.exe (possible credential dumping)",
                        detail={"source": data.get("SourceImage", ""), "event_id": eid},
                        fingerprint=_fp("lsass", data.get("SourceImage", ""), event.get("record")),
                    )
                )
    return findings


def check_new_file(path: str) -> Finding | None:
    name = os.path.basename(path)
    suffixes = [s.lower() for s in PurePath(name).suffixes]
    if not suffixes:
        return None
    last = suffixes[-1]
    if last not in RISKY_EXTENSIONS:
        return None
    if len(suffixes) >= 2 and suffixes[-2] in DOC_EXTENSIONS:
        return Finding(
            rule="double_extension",
            severity=Severity.CRITICAL,
            title="Disguised executable file (double extension)",
            detail={"path": path},
            fingerprint=_fp("double_ext", path),
            response=("quarantine_file", {"path": path}),
        )
    return Finding(
        rule="new_executable",
        severity=Severity.INFO,
        title="New executable file",
        detail={"path": path},
        fingerprint=_fp("new_exec", path),
        notify=False,
    )


def check_burst(folder: str, count: int, window_s: int, bucket: int) -> Finding:
    return Finding(
        rule="mass_file_change",
        severity=Severity.CRITICAL,
        title="Very many file changes in a short time (possible ransomware)",
        detail={"folder": folder, "events": count, "window_s": window_s},
        fingerprint=_fp("burst", folder, bucket),
    )


def check_server(status: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    failures = int(status.get("auth_failures_1h", 0) or 0)
    if failures >= 20:
        findings.append(
            Finding(
                rule="server_ssh_bruteforce",
                severity=Severity.WARN,
                title=f"{failures} failed SSH logins in the last hour",
                detail={"failures": failures, "top_sources": status.get("auth_top_sources", [])},
                fingerprint=_fp("ssh_brute", int(status.get("hour_bucket", 0))),
            )
        )
    disk = float(status.get("disk_percent", 0) or 0)
    if disk >= 90:
        findings.append(
            Finding(
                rule="server_disk_full",
                severity=Severity.WARN,
                title=f"Server disk is {disk:.0f}% full",
                detail={"disk_percent": disk},
                fingerprint=_fp("disk", int(disk // 5)),
            )
        )
    for service in status.get("failed_services", []) or []:
        findings.append(
            Finding(
                rule="server_service_failed",
                severity=Severity.WARN,
                title=f"Service {service} is not running",
                detail={"service": service},
                fingerprint=_fp("svc", service, int(status.get("hour_bucket", 0))),
            )
        )
    return findings


def check_server_extras(data: dict[str, Any], previous: dict[str, str]) -> list[Finding]:
    """Updates, Docker containers and Pi-hole anomalies of the server."""
    findings: list[Finding] = []
    day = int(data.get("day_bucket", 0))
    updates = data.get("updates") or {}
    security = int(updates.get("security") or 0)
    if security > 0:
        findings.append(
            Finding(
                rule="server_security_updates",
                severity=Severity.WARN,
                title=f"{security} security updates pending on the server",
                detail={"security": security, "pending": updates.get("pending", 0)},
                fingerprint=_fp("sec_updates", day),
            )
        )
    if updates.get("reboot_required"):
        findings.append(
            Finding(
                rule="server_reboot_required",
                severity=Severity.INFO,
                title="Server needs a restart (after updates)",
                detail={},
                fingerprint=_fp("reboot", day),
                notify=True,
            )
        )

    hour = int(data.get("hour_bucket", 0))
    for container in (data.get("docker") or {}).get("containers", []):
        name, state = container.get("name", "?"), container.get("state", "")
        status = container.get("status", "")
        before = previous.get(name)
        problem = None
        if state in ("restarting", "dead"):
            problem = f"Container {name} is in state {state}"
        elif container.get("healthy") is False:
            problem = f"Container {name} is unhealthy"
        elif state == "exited" and before == "running":
            problem = f"Container {name} exited ({status})"
        if problem:
            findings.append(
                Finding(
                    rule="server_docker",
                    severity=Severity.WARN,
                    title=problem,
                    detail={
                        "container": name,
                        "image": container.get("image"),
                        "state": state,
                        "status": status,
                    },
                    fingerprint=_fp("docker", name, state, hour),
                )
            )

    findings += check_backups(
        data.get("backups") or {},
        max_age_h=int(data.get("backup_max_age_h") or 26),
        now=float(data.get("now") or 0),
        day=day,
    )

    pihole = data.get("pihole") or {}
    total = int(pihole.get("queries_total") or 0)
    for client in pihole.get("top_clients") or []:
        count = int(client.get("count") or 0)
        if total >= 5000 and count / total >= 0.4:
            label = client.get("name") or client.get("ip")
            findings.append(
                Finding(
                    rule="pihole_client_spike",
                    severity=Severity.WARN,
                    title=f"Device {label} makes {count / total:.0%} of all DNS queries",
                    detail={"client": client, "queries_total": total},
                    fingerprint=_fp("pihole_spike", client.get("ip"), day),
                )
            )
    return findings


def check_backups(
    backups: dict[str, Any], *, max_age_h: int, now: float, day: int
) -> list[Finding]:
    """Backup folders on the server: is one missing or is the newest file too old?"""
    if not backups.get("available"):
        return []
    findings: list[Finding] = []
    if backups.get("stale"):
        findings.append(
            Finding(
                rule="server_backup",
                severity=Severity.WARN,
                title="Backup overview on the server is no longer being updated",
                detail={"ts": backups.get("ts")},
                fingerprint=_fp("backup_stale", day),
            )
        )
    for entry in backups.get("paths") or []:
        path = str(entry.get("path", "?"))
        newest = entry.get("newest")
        problem = None
        if not entry.get("exists"):
            problem = f"Backup folder {path} is missing on the server"
        elif not newest:
            problem = f"Backup folder {path} is empty"
        elif now and (now - float(newest)) / 3600 > max_age_h:
            hours = int((now - float(newest)) / 3600)
            problem = f"Last backup in {path} is {hours} hours old"
        if problem:
            findings.append(
                Finding(
                    rule="server_backup",
                    severity=Severity.WARN,
                    title=problem,
                    detail={**entry, "max_age_h": max_age_h},
                    fingerprint=_fp("backup", path, day),
                )
            )
    return findings


def check_file_scan(result: dict[str, Any]) -> Finding | None:
    if result.get("verdict") != "malicious":
        return None
    reasons = []
    if result.get("known_malware"):
        reasons.append("known malware (MalwareBazaar)")
    if result.get("defender") == "threat":
        reasons.append("Windows Defender reports a threat")
    return Finding(
        rule="malware_file",
        severity=Severity.CRITICAL,
        title="Malware detected: " + PurePath(result["path"]).name,
        detail={"path": result["path"], "sha256": result.get("sha256"), "reasons": reasons},
        fingerprint=_fp("malware", result.get("sha256")),
        response=("quarantine_file", {"path": result["path"]}),
    )


def check_usb(devices: list[dict[str, Any]], known: set[str]) -> list[Finding]:
    findings = []
    for device in devices:
        if device["id"] in known:
            continue
        keyboard = device.get("keyboard", False)
        findings.append(
            Finding(
                rule="usb_new_device",
                severity=Severity.WARN if keyboard else Severity.INFO,
                title=("New USB keyboard: " if keyboard else "New USB device: ") + device["name"],
                detail=device,
                fingerprint=_fp("usb", device["id"]),
                notify=keyboard,
            )
        )
    return findings


def check_extensions(extensions: list[dict[str, Any]], known: set[str]) -> list[Finding]:
    findings = []
    for ext in extensions:
        key = f"{ext['browser']}|{ext['profile']}|{ext['id']}"
        if key in known:
            continue
        broad = bool(ext.get("broad"))
        findings.append(
            Finding(
                rule="browser_extension_new",
                severity=Severity.WARN if broad else Severity.INFO,
                title=f"New {ext['browser']} extension: {ext['name']}"
                + (" (access to all sites)" if broad else ""),
                detail=ext,
                fingerprint=_fp("ext", key),
                notify=broad,
            )
        )
    return findings


def check_capability_apps(
    capability: str, apps: list[dict[str, Any]], known: set[str]
) -> list[Finding]:
    label = "camera" if capability == "webcam" else "microphone"
    findings = []
    for app in apps:
        if app["app"] in known:
            continue
        findings.append(
            Finding(
                rule=f"{capability}_new_app",
                severity=Severity.WARN if capability == "webcam" else Severity.INFO,
                title=f"New program uses the {label}: " + PurePath(app["app"]).name,
                detail=app,
                fingerprint=_fp(capability, app["app"]),
                notify=capability == "webcam",
            )
        )
    return findings


def is_download_finish(src: str) -> bool:
    return PurePath(src).suffix.lower() in DOWNLOAD_TEMP_EXTENSIONS
