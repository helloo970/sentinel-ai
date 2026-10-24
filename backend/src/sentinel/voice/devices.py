"""Microphone selection: list real capture devices and find them again.

What is stored is name + driver interface (not the index, which changes on every
restart or re-plug). Virtual devices (Stereo Mix, Sound Mapper ...) are hidden.
"""

from __future__ import annotations

import re
from typing import Any

# Preferred Windows interfaces: MME resamples any sample rate itself, WASAPI is modern
PREFERRED_APIS = ("MME", "Windows WASAPI", "Windows DirectSound")
HIDDEN = re.compile(
    r"stereomix|stereo mix|soundmapper|primärer soundaufnahmetreiber|primary sound capture|"
    r"virtual|lautsprecher|speaker|kopfhörer \d",
    re.I,
)


def _clean(name: str) -> str:
    """Windows-Treiberpfade wie '@System32\\...;(Arctis Nova 7X)' → 'Arctis Nova 7X'."""
    inner = re.findall(r"\(([^()]+)\)\)?\s*$", name)
    if "@System32" in name and inner:
        return inner[-1].strip()
    return re.sub(r"\s+", " ", name).strip()


def list_inputs(sd: Any = None) -> list[dict[str, Any]]:
    if sd is None:
        import sounddevice as sd  # type: ignore[no-redef]
    apis = sd.query_hostapis()
    default_index = sd.default.device[0] if sd.default.device else -1
    seen: dict[str, dict[str, Any]] = {}
    for index, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] < 1:
            continue
        api = apis[dev["hostapi"]]["name"]
        if api not in PREFERRED_APIS:
            continue
        name = _clean(str(dev["name"]))
        if not name or HIDDEN.search(name):
            continue
        key = name.lower()[:28]  # MME truncates names to 31 characters
        entry = {
            "index": index,
            "name": name,
            "api": api,
            "rate": int(dev["default_samplerate"]),
            "default": index == default_index,
        }
        current = seen.get(key)
        if current is None or PREFERRED_APIS.index(api) < PREFERRED_APIS.index(current["api"]):
            if current is not None:
                entry["default"] = entry["default"] or current["default"]
            seen[key] = entry
    return sorted(seen.values(), key=lambda d: (not d["default"], d["name"].lower()))


def resolve(choice: dict[str, Any] | None, sd: Any = None) -> dict[str, Any] | None:
    """Stored choice (name + API) -> current device, or None for the default."""
    if not choice:
        return None
    for dev in list_inputs(sd):
        if dev["name"] == choice.get("name") and dev["api"] == choice.get("api"):
            return dev
    for dev in list_inputs(sd):  # same device via another interface
        if dev["name"].lower()[:28] == str(choice.get("name", "")).lower()[:28]:
            return dev
    return None
