"""System control without a shell: volume/media keys and lock screen (ctypes)."""

from __future__ import annotations

import sys

VK = {
    "volume_up": 0xAF,
    "volume_down": 0xAE,
    "mute": 0xAD,
    "play_pause": 0xB3,
    "next_track": 0xB0,
    "previous_track": 0xB1,
}
KEYEVENTF_KEYUP = 0x0002
COMMANDS = (*VK.keys(), "lock")


def run(command: str, repeat: int = 1) -> str:
    if sys.platform != "win32":
        raise RuntimeError("System control is only available on Windows")
    import ctypes

    user32 = ctypes.windll.user32
    if command == "lock":
        if not user32.LockWorkStation():
            raise RuntimeError("Locking failed")
        return "Screen locked"
    code = VK.get(command)
    if code is None:
        raise ValueError(f"unknown command {command}")
    for _ in range(max(1, min(repeat, 20))):
        user32.keybd_event(code, 0, 0, 0)
        user32.keybd_event(code, 0, KEYEVENTF_KEYUP, 0)
    return f"{command} executed"
