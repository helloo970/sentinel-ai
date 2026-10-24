"""Alert test: simulated attacks for every detection rule.

A test runs the whole reporting path (display, voice, push to the phone) but is clearly
marked as a TEST and never triggers a defense: test findings have no response, so even
at guardian level no process is ended and nothing is moved. Reachable only via the
UI API and the paired phone, not via Jarvis or agents.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sentinel.monitor import playbook
from sentinel.monitor.rules import Finding
from sentinel.permissions.models import Severity

if TYPE_CHECKING:
    from sentinel.services import Services


@dataclass(frozen=True)
class Scenario:
    rule: str
    group: str
    severity: Severity
    title: str
    protects: str


SCENARIOS: tuple[Scenario, ...] = (
    # PC
    Scenario(
        "malware_file",
        "PC",
        Severity.CRITICAL,
        "Malware in a download: invoice.pdf.exe",
        "Known malware or a Windows Defender finding in a new file",
    ),
    Scenario(
        "mass_file_change",
        "PC",
        Severity.CRITICAL,
        "Mass change: 400 files in 10 s in Documents",
        "Ransomware that encrypts many files at once",
    ),
    Scenario(
        "office_spawns_shell",
        "PC",
        Severity.CRITICAL,
        "Word starts PowerShell",
        "Macro attack via an Office document",
    ),
    Scenario(
        "lsass_access",
        "PC",
        Severity.CRITICAL,
        "Foreign process reads the password store (LSASS)",
        "Theft of Windows credentials",
    ),
    Scenario(
        "temp_exec",
        "PC",
        Severity.WARN,
        "Program starts from the Temp folder",
        "Malware hiding in Temp",
    ),
    Scenario(
        "temp_listener",
        "PC",
        Severity.WARN,
        "Program from Temp opens a network port",
        "Backdoor on your PC",
    ),
    Scenario(
        "double_extension",
        "PC",
        Severity.WARN,
        "File with a double extension: photo.jpg.exe",
        "Disguised programs that look like images or PDFs",
    ),
    Scenario(
        "new_executable",
        "PC",
        Severity.INFO,
        "New program file in Downloads: setup.exe",
        "Programs downloaded unnoticed",
    ),
    Scenario(
        "new_autostart",
        "PC",
        Severity.WARN,
        "New autostart entry: updater.exe",
        "Malware that persists at Windows startup",
    ),
    Scenario(
        "new_listener",
        "PC",
        Severity.INFO,
        "New open port: unknown.exe on 4444",
        "Programs that secretly become reachable from outside",
    ),
    Scenario(
        "usb_new_device",
        "PC",
        Severity.WARN,
        "New USB device behaves like a keyboard",
        "BadUSB sticks that type commands in a split second",
    ),
    Scenario(
        "browser_extension_new",
        "PC",
        Severity.WARN,
        "New browser extension may read all sites",
        "Extensions that read along passwords and banking data",
    ),
    Scenario(
        "webcam_new_app",
        "PC",
        Severity.WARN,
        "New program uses the camera",
        "Spying via the webcam",
    ),
    Scenario(
        "microphone_new_app",
        "PC",
        Severity.INFO,
        "New program uses the microphone",
        "Secret eavesdropping",
    ),
    # Heimnetz
    Scenario(
        "new_device",
        "Home network",
        Severity.WARN,
        "Unknown device on the Wi-Fi: 192.0.2.77",
        "Strangers on your Wi-Fi",
    ),
    Scenario(
        "pihole_client_spike",
        "Home network",
        Severity.WARN,
        "One device makes 60% of all DNS queries",
        "Infected devices phoning home",
    ),
    Scenario(
        "cert_expiring",
        "Home network",
        Severity.WARN,
        "Your service's certificate expires in 5 days",
        "Outages or warnings for your own services",
    ),
    # Server
    Scenario(
        "server_ssh_bruteforce",
        "Server",
        Severity.WARN,
        "80 failed SSH logins in one hour from 203.0.113.9",
        "Password-guessing attacks on your server",
    ),
    Scenario(
        "server_security_updates",
        "Server",
        Severity.WARN,
        "4 security updates pending on the server",
        "Known vulnerabilities that are not yet closed",
    ),
    Scenario(
        "server_service_failed",
        "Server",
        Severity.WARN,
        "Service ssh has failed",
        "Outages or disabled protection services",
    ),
    Scenario(
        "server_docker",
        "Server",
        Severity.WARN,
        "Container pihole exited",
        "Crashed or tampered containers",
    ),
    Scenario(
        "server_disk_full",
        "Server",
        Severity.WARN,
        "Server disk is 95% full",
        "Outage from a full disk (also log floods during attacks)",
    ),
    Scenario(
        "server_backup",
        "Server",
        Severity.WARN,
        "Last backup in /DATA/BackUp is 50 hours old",
        "No current backup if something does happen",
    ),
)
BY_RULE = {s.rule: s for s in SCENARIOS}


def catalog() -> list[dict[str, Any]]:
    return [
        {
            "rule": s.rule,
            "group": s.group,
            "severity": s.severity,
            "title": s.title,
            "protects": s.protects,
            **playbook.for_rule(s.rule),
        }
        for s in SCENARIOS
    ]


def run_test(svc: Services, rule: str, *, actor: str = "user") -> dict[str, Any]:
    scenario = BY_RULE.get(rule)
    if scenario is None:
        raise ValueError("Unknown rule")
    finding = Finding(
        rule=scenario.rule,
        # A test is always at least a warning, so push and sound are reliably tried out
        severity=scenario.severity if scenario.severity is not Severity.INFO else Severity.WARN,
        title=f"TEST: {scenario.title}",
        detail={"test": True, "protects": scenario.protects},
        fingerprint=f"test-{secrets.token_hex(8)}",
        response=None,
        notify=True,
    )
    alert_id = svc.alerts.handle(finding)
    svc.audit.append(
        actor=actor,
        source="user",
        action="alert_test",
        params={"rule": rule},
        decision="execute",
        detail=scenario.title,
    )
    return {
        "id": alert_id,
        "rule": rule,
        "severity": finding.severity,
        "title": finding.title,
        **playbook.for_rule(rule),
    }
