"""Read Sysmon events via wevtutil (fixed arguments, no admin rights).

Prerequisite: Sysmon is installed and the user is in "Event Log Readers"
(see scripts/setup-sysmon.ps1).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from defusedxml import ElementTree

from sentinel.winproc import NO_WINDOW

CHANNEL = "Microsoft-Windows-Sysmon/Operational"
NS = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}

EVENT_NAMES = {
    1: "Process started",
    3: "Network connection",
    7: "Image loaded",
    8: "Remote thread created",
    10: "Process access",
    11: "File created",
    12: "Registry object",
    13: "Registry value set",
    22: "DNS query",
    23: "File deleted",
}


def _wevtutil() -> Path:
    return Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "wevtutil.exe"


def _decode(raw: bytes) -> str:
    """wevtutil writes UTF-8 or the console's OEM code page."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("oem" if sys.platform == "win32" else "latin-1", "replace")


def parse_events(xml_text: str) -> list[dict[str, Any]]:
    """Parses the XML output of wevtutil (several <Event> elements without a root)."""
    text = xml_text.strip()
    if not text:
        return []
    root = ElementTree.fromstring(f"<Events>{text}</Events>")
    events = []
    for node in root.findall("e:Event", NS):
        system = node.find("e:System", NS)
        if system is None:
            continue
        event_id = int(system.findtext("e:EventID", default="0", namespaces=NS))
        record = int(system.findtext("e:EventRecordID", default="0", namespaces=NS))
        created = system.find("e:TimeCreated", NS)
        data = {}
        event_data = node.find("e:EventData", NS)
        if event_data is not None:
            for item in event_data.findall("e:Data", NS):
                name = item.get("Name")
                if name:
                    data[name] = (item.text or "")[:1000]
        events.append(
            {
                "record": record,
                "event_id": event_id,
                "event": EVENT_NAMES.get(event_id, f"Event {event_id}"),
                "time": created.get("SystemTime") if created is not None else "",
                "data": data,
            }
        )
    events.sort(key=lambda e: e["record"])
    return events


class SysmonReader:
    def __init__(self) -> None:
        self.last_record = 0
        self.status = "unknown"

    def available(self) -> bool:
        return sys.platform == "win32" and _wevtutil().is_file()

    def read_latest(self, count: int = 200) -> list[dict[str, Any]]:
        if not self.available():
            self.status = "only available on Windows"
            return []
        args = [str(_wevtutil()), "qe", CHANNEL, f"/c:{int(count)}", "/rd:true", "/f:xml"]
        try:
            proc = subprocess.run(  # noqa: S603 - fixed arguments, no shell
                args,
                capture_output=True,
                timeout=20,
                check=False,
                shell=False,
                creationflags=NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.status = f"Error: {exc}"
            return []
        if proc.returncode != 0:
            self.status = "not available (Sysmon installed? Group 'Event Log Readers'?)"
            return []
        self.status = "active"
        try:
            return parse_events(_decode(proc.stdout))
        except ElementTree.ParseError:
            self.status = "Output not readable"
            return []

    def read_new(self) -> list[dict[str, Any]]:
        events = self.read_latest()
        new = [e for e in events if e["record"] > self.last_record]
        if events:
            first_run = self.last_record == 0
            self.last_record = max(e["record"] for e in events)
            if first_run:
                return []  # on the first run only remember the current state
        return new
