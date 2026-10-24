"""USB devices, removable drives, browser extensions and camera/microphone usage.

Everything is read only and needs no admin rights (registry, file system). Names and texts
come from devices or extensions and are treated as untrusted.
"""

from __future__ import annotations

import contextlib
import glob
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psutil

BROAD_PERMISSIONS = {
    "<all_urls>",
    "tabs",
    "webRequest",
    "webRequestBlocking",
    "cookies",
    "history",
    "downloads",
    "nativeMessaging",
    "debugger",
    "proxy",
    "clipboardRead",
    "management",
    "*://*/*",
    "http://*/*",
    "https://*/*",
}
HID_HINTS = ("keyboard", "tastatur", "hid")


# ---------------------------------------------------------------------- USB


def _desc(value: str) -> str:
    """Registry descriptions like '@input.inf,%x%;HID Keyboard' -> 'HID Keyboard'."""
    return value.split(";")[-1].strip()[:120] if value else ""


def usb_devices() -> list[dict[str, Any]]:
    if sys.platform != "win32":
        return []
    import winreg

    devices = []
    root = r"SYSTEM\CurrentControlSet\Enum\USB"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root) as usb:
            for i in range(winreg.QueryInfoKey(usb)[0]):
                vidpid = winreg.EnumKey(usb, i)
                with winreg.OpenKey(usb, vidpid) as dev:
                    for j in range(winreg.QueryInfoKey(dev)[0]):
                        serial = winreg.EnumKey(dev, j)
                        info: dict[str, str] = {}
                        try:
                            with winreg.OpenKey(dev, serial) as inst:
                                for name in ("FriendlyName", "DeviceDesc", "Service", "Mfg"):
                                    with contextlib.suppress(OSError):
                                        info[name] = str(winreg.QueryValueEx(inst, name)[0])
                        except OSError:
                            continue
                        label = _desc(info.get("FriendlyName", "")) or _desc(
                            info.get("DeviceDesc", "")
                        )
                        service = info.get("Service", "")
                        devices.append(
                            {
                                "id": f"{vidpid}\\{serial}"[:200],
                                "name": label or vidpid,
                                "vendor": _desc(info.get("Mfg", "")),
                                "service": service,
                                "keyboard": service.lower() in ("kbdhid", "hidusb")
                                and any(h in label.lower() for h in HID_HINTS),
                            }
                        )
    except OSError:
        return []
    return devices


def removable_drives() -> list[dict[str, Any]]:
    drives = []
    for part in psutil.disk_partitions(all=False):
        if "removable" in part.opts or "cdrom" in part.opts:
            drives.append({"mount": part.mountpoint, "fs": part.fstype, "opts": part.opts})
    return drives


# ---------------------------------------------------------------------- Browser


def _chromium_roots() -> dict[str, str]:
    local = os.environ.get("LOCALAPPDATA", "")
    return {
        "Chrome": os.path.join(local, "Google", "Chrome", "User Data"),
        "Edge": os.path.join(local, "Microsoft", "Edge", "User Data"),
        "Brave": os.path.join(local, "BraveSoftware", "Brave-Browser", "User Data"),
    }


def _resolve_message(name: str, version_dir: Path) -> str:
    if not name.startswith("__MSG_"):
        return name
    key = name[6:-2]
    for locale in ("en", "en_US", "de"):
        messages = version_dir / "_locales" / locale / "messages.json"
        if messages.is_file():
            try:
                data = json.loads(messages.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            for k, v in data.items():
                if k.lower() == key.lower() and isinstance(v, dict):
                    return str(v.get("message", name))
    return name


def chromium_extensions(roots: dict[str, str] | None = None) -> list[dict[str, Any]]:
    result = []
    for browser, root in (roots or _chromium_roots()).items():
        for ext_dir in glob.glob(os.path.join(root, "*", "Extensions", "*")):
            ext_id = os.path.basename(ext_dir)
            profile = os.path.basename(os.path.dirname(os.path.dirname(ext_dir)))
            versions = sorted(Path(ext_dir).glob("*/manifest.json"))
            if not versions:
                continue
            manifest_path = versions[-1]
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            perms = [
                str(p)
                for p in (manifest.get("permissions") or [])
                + (manifest.get("host_permissions") or [])
                if isinstance(p, str)
            ]
            result.append(
                {
                    "browser": browser,
                    "profile": profile,
                    "id": ext_id,
                    "name": _resolve_message(
                        str(manifest.get("name", ext_id)), manifest_path.parent
                    )[:120],
                    "version": str(manifest.get("version", ""))[:30],
                    "broad": sorted(set(perms) & BROAD_PERMISSIONS),
                }
            )
    return result


def firefox_extensions(pattern: str | None = None) -> list[dict[str, Any]]:
    pattern = pattern or os.path.join(
        os.environ.get("APPDATA", ""), "Mozilla", "Firefox", "Profiles", "*", "extensions.json"
    )
    result = []
    for path in glob.glob(pattern):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for addon in data.get("addons", []):
            if addon.get("type") != "extension" or addon.get("location") != "app-profile":
                continue
            perms = (addon.get("userPermissions") or {}).get("permissions", []) + (
                addon.get("userPermissions") or {}
            ).get("origins", [])
            result.append(
                {
                    "browser": "Firefox",
                    "profile": Path(path).parent.name,
                    "id": str(addon.get("id", ""))[:120],
                    "name": str((addon.get("defaultLocale") or {}).get("name") or addon.get("id"))[
                        :120
                    ],
                    "version": str(addon.get("version", ""))[:30],
                    "broad": sorted(set(map(str, perms)) & BROAD_PERMISSIONS),
                }
            )
    return result


def browser_extensions() -> list[dict[str, Any]]:
    return chromium_extensions() + firefox_extensions()


# ---------------------------------------------------------------------- Camera / microphone


def _filetime(value: int) -> float | None:
    if not value:
        return None
    return (datetime(1601, 1, 1, tzinfo=UTC) + timedelta(microseconds=value / 10)).timestamp()


def capability_usage(capability: str) -> list[dict[str, Any]]:
    """Which apps use or have used the camera ('webcam') or microphone ('microphone')."""
    if sys.platform != "win32":
        return []
    import winreg

    base = (
        r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore"
        f"\\{capability}"
    )
    apps = []

    def read_app(key: Any, name: str, packaged: bool) -> None:
        try:
            start = int(winreg.QueryValueEx(key, "LastUsedTimeStart")[0])
            stop = int(winreg.QueryValueEx(key, "LastUsedTimeStop")[0])
        except OSError:
            return
        label = name.replace("#", "\\") if not packaged else name.split("_")[0]
        apps.append(
            {
                "app": label[-160:],
                "packaged": packaged,
                "last_start": _filetime(start),
                "last_stop": _filetime(stop),
                "in_use": start > stop,
            }
        )

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base) as root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                name = winreg.EnumKey(root, i)
                with winreg.OpenKey(root, name) as sub:
                    if name == "NonPackaged":
                        for j in range(winreg.QueryInfoKey(sub)[0]):
                            app = winreg.EnumKey(sub, j)
                            with winreg.OpenKey(sub, app) as key:
                                read_app(key, app, False)
                    else:
                        read_app(sub, name, True)
    except OSError:
        return []
    apps.sort(key=lambda a: a["last_start"] or 0, reverse=True)
    return apps
