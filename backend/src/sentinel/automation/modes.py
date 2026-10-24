"""Modes like gaming or focus: start/close programs, go quiet, optionally with a timer.

Defined in <data_dir>/config/modes.json. Every program step goes through the
permissions engine individually (allowlist names only). When a mode ends, everything is undone:
close the programs it started, restart the ones it closed, restore the previous quiet mode.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sentinel.automation.programs import NAME_RE
from sentinel.permissions.models import CURRENT_REQUEST, Source

if TYPE_CHECKING:
    from sentinel.services import Services

log = logging.getLogger(__name__)

MAX_PROGRAMS = 10
MAX_MINUTES = 8 * 60

DEFAULT_MODES: dict[str, Any] = {
    "gaming": {
        "title": "Gaming mode",
        "launch": ["steam", "discord"],
        "close": [],
        "quiet": True,
    },
    "fokus": {
        "title": "Focus mode",
        "launch": ["spotify"],
        "close": ["discord"],
        "quiet": True,
        "minutes": 25,
    },
}


@dataclass(frozen=True)
class Mode:
    name: str
    title: str
    launch: tuple[str, ...]
    close: tuple[str, ...]
    quiet: bool
    minutes: int | None


def _names(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, list) or len(value) > MAX_PROGRAMS:
        return None
    if not all(isinstance(v, str) and NAME_RE.match(v) for v in value):
        return None
    return tuple(value)


def parse_modes(raw: dict[str, Any]) -> dict[str, Mode]:
    result: dict[str, Mode] = {}
    for name, entry in raw.items():
        if not isinstance(name, str) or not NAME_RE.match(name) or not isinstance(entry, dict):
            continue
        launch = _names(entry.get("launch", []))
        close = _names(entry.get("close", []))
        minutes = entry.get("minutes")
        if launch is None or close is None:
            log.warning("mode %s: invalid program list", name)
            continue
        if minutes is not None and not (
            isinstance(minutes, int)
            and not isinstance(minutes, bool)
            and 0 < minutes <= MAX_MINUTES
        ):
            log.warning("mode %s: invalid minutes", name)
            continue
        result[name] = Mode(
            name=name,
            title=str(entry.get("title", name))[:60],
            launch=launch,
            close=close,
            quiet=bool(entry.get("quiet", False)),
            minutes=minutes,
        )
    return result


class ModeService:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self.file: Path = svc.config.config_dir / "modes.json"
        if not self.file.exists():
            self.file.parent.mkdir(parents=True, exist_ok=True)
            self.file.write_text(
                json.dumps(DEFAULT_MODES, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )

    # ------------------------------------------------------------------ Catalog

    def all(self) -> dict[str, Mode]:
        try:
            raw = json.loads(self.file.read_text(encoding="utf-8") or "{}")
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("cannot read %s: %s", self.file, exc)
            return {}
        return parse_modes(raw) if isinstance(raw, dict) else {}

    def get(self, name: str) -> Mode | None:
        return self.all().get(name)

    # ------------------------------------------------------------------ State

    def active(self) -> dict[str, Any] | None:
        raw = self.svc.store.get("active_mode")
        if not raw:
            return None
        try:
            state = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return state if isinstance(state, dict) else None

    def _save(self, state: dict[str, Any] | None) -> None:
        self.svc.store.set("active_mode", json.dumps(state) if state else "")

    def status(self) -> dict[str, Any]:
        modes = [
            {
                "name": m.name,
                "title": m.title,
                "launch": list(m.launch),
                "close": list(m.close),
                "quiet": m.quiet,
                "minutes": m.minutes,
            }
            for m in self.all().values()
        ]
        return {"modes": modes, "active": self.active(), "quiet": self.svc.notifier.quiet}

    # ------------------------------------------------------------------ Start / End

    def start(self, mode: Mode, minutes: int | None = None) -> dict[str, Any]:
        if self.active():
            self.stop(reason="switch")
        ctx = CURRENT_REQUEST.get()
        source = ctx.source if ctx else Source.USER
        tainted = bool(ctx and ctx.tainted)
        actor = f"mode:{mode.name}"
        state: dict[str, Any] = {
            "name": mode.name,
            "title": mode.title,
            "started": time.time(),
            "ends": None,
            "launched": [],
            "closed": [],
            "quiet_before": self.svc.notifier.quiet,
            "steps": [],
        }
        for name in mode.close:
            program = self.svc.programs.get(name)
            if program is None or not self.svc.programs.running(program):
                continue
            outcome = self.svc.engine.request(
                "close_program", {"name": name}, source=source, actor=actor, tainted=tainted
            )
            state["steps"].append({"action": f"close:{name}", "status": outcome.status})
            if outcome.status == "executed":
                state["closed"].append(name)
        for name in mode.launch:
            program = self.svc.programs.get(name)
            if program is None:
                state["steps"].append({"action": f"launch:{name}", "status": "missing"})
                continue
            if self.svc.programs.running(program):
                continue
            outcome = self.svc.engine.request(
                "launch_program", {"name": name}, source=source, actor=actor, tainted=tainted
            )
            state["steps"].append({"action": f"launch:{name}", "status": outcome.status})
            if outcome.status == "executed":
                state["launched"].append(name)
        if mode.quiet:
            self.svc.notifier.set_quiet(True)
        duration = minutes or mode.minutes
        if duration:
            state["ends"] = time.time() + duration * 60
        self._save(state)
        self.svc.bus.publish("mode", {"active": state})
        return state

    def stop(self, *, reason: str = "ended", source: Source = Source.USER) -> str:
        state = self.active()
        if not state:
            return "No mode active"
        for name in state.get("launched", []):
            program = self.svc.programs.get(name)
            if program is not None:
                self.svc.programs.close(program)
        for name in state.get("closed", []):
            program = self.svc.programs.get(name)
            if program is not None:
                try:
                    self.svc.programs.launch(program)
                except OSError as exc:
                    log.warning("cannot restart %s: %s", name, exc)
        self.svc.notifier.set_quiet(bool(state.get("quiet_before")))
        self._save(None)
        self.svc.audit.append(
            actor=f"mode:{state.get('name')}",
            source=source,
            action="mode_end",
            decision="execute",
            result="ok",
            detail=reason,
        )
        self.svc.bus.publish("mode", {"active": None})
        return f"{state.get('title', 'Mode')} {reason}"

    def tick(self, now: float | None = None) -> bool:
        """Ends a mode whose timer has run out. True if something was ended."""
        state = self.active()
        if not state or not state.get("ends") or (now or time.time()) < state["ends"]:
            return False
        title = state.get("title", "Mode")
        self.stop(reason="time expired", source=Source.RULE)
        self.svc.notifier.notify(title, "Time's up. Well done!", speak=True, push=False)
        return True
