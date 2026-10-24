"""Read autostart entries from the registry (Run/RunOnce) and autostart folders."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

RUN_KEYS = [
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Run"),
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\RunOnce"),
    ("HKLM", r"Software\Microsoft\Windows\CurrentVersion\Run"),
    ("HKLM", r"Software\Microsoft\Windows\CurrentVersion\RunOnce"),
    ("HKLM", r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"),
]


def _registry_entries() -> list[dict[str, Any]]:
    if sys.platform != "win32":
        return []
    import winreg

    hives = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}
    entries = []
    for hive_name, key_path in RUN_KEYS:
        try:
            with winreg.OpenKey(hives[hive_name], key_path) as key:
                index = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    entries.append(
                        {
                            "location": f"{hive_name}\\{key_path}",
                            "name": name,
                            "command": str(value),
                        }
                    )
                    index += 1
        except OSError:
            continue
    return entries


def _startup_folders() -> list[Path]:
    folders = []
    appdata = os.environ.get("APPDATA")
    programdata = os.environ.get("ProgramData")
    if appdata:
        folders.append(
            Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        )
    if programdata:
        folders.append(
            Path(programdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "StartUp"
        )
    return folders


def list_autostart() -> list[dict[str, Any]]:
    entries = _registry_entries()
    for folder in _startup_folders():
        if not folder.is_dir():
            continue
        for item in folder.iterdir():
            if item.name.lower() == "desktop.ini":
                continue
            entries.append({"location": str(folder), "name": item.name, "command": str(item)})
    return entries


def is_own_entry(entry: dict[str, Any], app_exe: str | None) -> bool:
    """True for SENTINEL's own autostart entry."""
    if not app_exe:
        return False
    return os.path.normcase(app_exe) in os.path.normcase(entry.get("command", ""))


def entry_key(entry: dict[str, Any]) -> str:
    return f"{entry['location']}|{entry['name']}|{entry['command']}"
