"""Protection and network actions: end process, devices, port check, ping sweep."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import psutil
from pydantic import Field

from sentinel.monitor.processes import protection_reason
from sentinel.permissions.models import (
    ActionDenied,
    ActionResult,
    ActionSpec,
    NoParams,
    Risk,
    StrictParams,
)

if TYPE_CHECKING:
    from sentinel.permissions import ActionRegistry
    from sentinel.services import Services

MAC_PATTERN = r"^[0-9a-fA-F]{2}([:-][0-9a-fA-F]{2}){5}$"
IPV4_PATTERN = r"^\d{1,3}(\.\d{1,3}){3}$"


class KillParams(StrictParams):
    pid: int = Field(ge=5, le=2**31)
    reason: str = Field(default="", max_length=200)


def _check_kill(p: KillParams, svc: Services) -> None:
    reason = protection_reason(p.pid)
    if reason:
        raise ActionDenied(f"Process {p.pid} is protected: {reason}")


def _preview_kill(p: KillParams, svc: Services) -> str:
    try:
        proc = psutil.Process(p.pid)
        return f"End process: {proc.name()} (PID {p.pid}) - {proc.exe()}"
    except psutil.Error:
        return f"End process: PID {p.pid}"


def _kill(p: KillParams, svc: Services) -> ActionResult:
    _check_kill(p, svc)  # State may have changed since approval
    proc = psutil.Process(p.pid)
    name = proc.name()
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except psutil.TimeoutExpired:
        proc.kill()
    return ActionResult(f"{name} (PID {p.pid}) ended", {"pid": p.pid, "name": name})


class MarkParams(StrictParams):
    mac: str = Field(pattern=MAC_PATTERN)
    label: str | None = Field(default=None, max_length=60)
    trusted: bool = True


def _check_mark(p: MarkParams, svc: Services) -> None:
    if svc.network.get_device(p.mac) is None:
        raise ActionDenied("Device is not in the inventory")


def _mark(p: MarkParams, svc: Services) -> ActionResult:
    before = svc.network.get_device(p.mac) or {}
    svc.network.set_device(p.mac, label=p.label, trusted=p.trusted)
    status = "trusted" if p.trusted else "not trusted"
    return ActionResult(
        f"Device {p.label or p.mac} marked as {status}",
        None,
        undo={"mac": p.mac, "label": before.get("label"), "trusted": bool(before.get("trusted"))},
    )


def _undo_mark(data: dict[str, Any], svc: Services) -> str:
    svc.network.set_device(data["mac"], label=data["label"], trusted=data["trusted"])
    return "Marking reset"


class IpParams(StrictParams):
    ip: str = Field(pattern=IPV4_PATTERN)


def _check_ip(p: IpParams, svc: Services) -> None:
    svc.network.check_target(p.ip)


def _port_check(p: IpParams, svc: Services) -> ActionResult:
    _check_ip(p, svc)
    open_ports = svc.network.port_check(p.ip)
    svc.network.store_ports(p.ip, open_ports)
    risky = [o for o in open_ports if o["risky"]]
    msg = f"{len(open_ports)} open ports on {p.ip}"
    if risky:
        msg += f", of which risky: {', '.join(o['service'] for o in risky)}"
    return ActionResult(msg, open_ports)


def _discover(_: NoParams, svc: Services) -> ActionResult:
    alive = svc.network.sweep()
    result = svc.network.refresh()
    return ActionResult(f"{alive} devices responding, {result['new']} new", result)


class ScanParams(StrictParams):
    path: str = Field(min_length=3, max_length=400)


def _scan_roots(svc: Services) -> list:
    return list(svc.config.managed_folders) + list(svc.config.watched_folders)


def _check_scan(p: ScanParams, svc: Services) -> None:
    from sentinel.automation.files import resolve_inside

    if not resolve_inside(p.path, _scan_roots(svc)).is_file():
        raise ActionDenied("Only single files can be checked")


def _scan_file(p: ScanParams, svc: Services) -> ActionResult:
    from sentinel.automation.files import resolve_inside
    from sentinel.monitor import rules

    path = resolve_inside(p.path, _scan_roots(svc))
    result = svc.filescan.scan(path)
    if "error" in result:
        raise ActionDenied(result["error"])
    finding = rules.check_file_scan(result)
    if finding:
        svc.alerts.handle(finding)
    verdict = "malware detected!" if result["verdict"] == "malicious" else "clean"
    return ActionResult(f"{path.name}: {verdict} (Defender: {result['defender']})", result)


def register(registry: ActionRegistry) -> None:
    specs = [
        ActionSpec(
            "kill_process",
            "End process",
            "Ends a suspicious process (not undoable, system processes are protected).",
            Risk.HIGH,
            KillParams,
            _kill,
            check=_check_kill,
            preview=_preview_kill,
            guardian_auto=True,
        ),
        ActionSpec(
            "mark_device",
            "Mark device",
            "Marks a home network device as trusted or not trusted, optionally with a name.",
            Risk.REVERSIBLE,
            MarkParams,
            _mark,
            undo=_undo_mark,
            check=_check_mark,
            preview=lambda p, s: f"Mark device {p.mac}",
        ),
        ActionSpec(
            "port_check",
            "Port check",
            "Checks common ports of your own home network device (RFC 1918 only).",
            Risk.REVERSIBLE,
            IpParams,
            _port_check,
            check=_check_ip,
            preview=lambda p, s: f"Port check of {p.ip}",
        ),
        ActionSpec(
            "network_discover",
            "Scan home network",
            "Ping sweep of your own subnet and update of the device inventory.",
            Risk.REVERSIBLE,
            NoParams,
            _discover,
            preview=lambda p, s: f"Ping sweep of {s.network.subnet()}",
        ),
    ]
    specs.append(
        ActionSpec(
            "scan_file",
            "Check file",
            "Checks a file from an approved "
            "folder by hash against known malware and with Windows Defender.",
            Risk.READ,
            ScanParams,
            _scan_file,
            check=_check_scan,
        )
    )
    for spec in specs:
        registry.register(spec)
