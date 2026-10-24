"""Allowlist for programs: name -> absolute path + fixed arguments.

The AI only picks the name. Custom entries live in <data_dir>/config/programs.json.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import psutil

log = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,31}$")


@dataclass(frozen=True)
class Program:
    name: str
    title: str
    path: Path
    args: tuple[str, ...] = field(default_factory=tuple)
    # A launcher (e.g. Discord Update.exe) starts the actual program in the same folder
    process: str = ""

    def matches(self, exe: str) -> bool:
        target = os.path.normcase(exe)
        if target == os.path.normcase(str(self.path)):
            return True
        root = os.path.normcase(str(self.path.parent)) + os.sep
        return bool(self.process) and (
            target.startswith(root) and os.path.basename(target) == self.process.lower()
        )


def _windows_defaults() -> dict[str, Program]:
    root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    pf = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    pf86 = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    appdata = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    candidates = [
        Program("notepad", "Editor", root / "System32" / "notepad.exe"),
        Program("calc", "Calculator", root / "System32" / "calc.exe"),
        Program("explorer", "Explorer", root / "explorer.exe"),
        Program("paint", "Paint", root / "System32" / "mspaint.exe"),
        Program(
            "edge", "Microsoft Edge", pf86 / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        ),
        Program("chrome", "Google Chrome", pf / "Google" / "Chrome" / "Application" / "chrome.exe"),
        Program("firefox", "Firefox", pf / "Mozilla Firefox" / "firefox.exe"),
        Program("steam", "Steam", pf86 / "Steam" / "steam.exe"),
        Program("spotify", "Spotify", appdata / "Spotify" / "Spotify.exe"),
        Program(
            "discord",
            "Discord",
            local / "Discord" / "Update.exe",
            ("--processStart", "Discord.exe"),
            process="discord.exe",
        ),
    ]
    return {p.name: p for p in candidates if p.path.is_file()}


def parse_programs(raw: dict[str, object]) -> dict[str, Program]:
    """Validates the entries from programs.json. Invalid ones are skipped."""
    result: dict[str, Program] = {}
    for name, entry in raw.items():
        if not isinstance(name, str) or not NAME_RE.match(name) or not isinstance(entry, dict):
            log.warning("skipping invalid program entry %r", name)
            continue
        path = Path(str(entry.get("path", "")))
        args = entry.get("args", [])
        if not path.is_absolute() or path.suffix.lower() != ".exe":
            log.warning("program %s: path must be an absolute .exe", name)
            continue
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            log.warning("program %s: args must be a list of strings", name)
            continue
        result[name] = Program(name, str(entry.get("title", name)), path, tuple(args))
    return result


class ProgramCatalog:
    def __init__(self, config_dir: Path, *, include_defaults: bool = True) -> None:
        self.file = config_dir / "programs.json"
        self.include_defaults = include_defaults and sys.platform == "win32"
        self._started: dict[int, str] = {}
        if not self.file.exists():
            self.file.parent.mkdir(parents=True, exist_ok=True)
            self.file.write_text("{}\n", encoding="utf-8")

    def all(self) -> dict[str, Program]:
        programs = _windows_defaults() if self.include_defaults else {}
        try:
            raw = json.loads(self.file.read_text(encoding="utf-8") or "{}")
            if isinstance(raw, dict):
                programs.update(parse_programs(raw))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("cannot read %s: %s", self.file, exc)
        return programs

    def get(self, name: str) -> Program | None:
        return self.all().get(name)

    def launch(self, program: Program) -> int:
        if not program.path.is_file():
            raise FileNotFoundError(f"{program.path} does not exist")
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        # Fixed argument list, no shell=True
        proc = subprocess.Popen(  # noqa: S603
            [str(program.path), *program.args],
            shell=False,
            close_fds=True,
            creationflags=flags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._started[proc.pid] = program.name
        return proc.pid

    def running(self, program: Program) -> list[psutil.Process]:
        me = psutil.Process().username()
        found = []
        for proc in psutil.process_iter(["exe", "username"]):
            try:
                exe = proc.info.get("exe")
                if exe and program.matches(exe) and proc.info.get("username") == me:
                    found.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return found

    def close(self, program: Program, *, timeout: float = 3.0) -> int:
        procs = self.running(program)
        for proc in procs:
            try:
                proc.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        _, alive = psutil.wait_procs(procs, timeout=timeout)
        return len(procs) - len(alive)
