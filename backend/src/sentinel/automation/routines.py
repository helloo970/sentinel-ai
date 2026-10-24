"""Routines: fixed sequences of permitted actions (e.g. "work mode").

Defined in <data_dir>/config/routines.json or via plugins. Every step goes through the
permissions engine individually. High-risk and final actions are not allowed in
routines.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sentinel.plugins.loader import PluginManager

log = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,31}$")
ALLOWED_RISKS = {"read", "low", "reversible"}
MAX_STEPS = 10

EXAMPLE = {
    "arbeitsmodus": {
        "title": "Work mode",
        "steps": [
            {"action": "launch_program", "params": {"name": "edge"}},
            {"action": "system_control", "params": {"command": "mute"}},
        ],
    }
}


@dataclass(frozen=True)
class Routine:
    name: str
    title: str
    steps: tuple[tuple[str, dict[str, Any]], ...]
    origin: str = "config"


def parse_routines(raw: dict[str, Any], *, origin: str = "config") -> dict[str, Routine]:
    result: dict[str, Routine] = {}
    for name, entry in raw.items():
        if not isinstance(name, str) or not NAME_RE.match(name) or not isinstance(entry, dict):
            continue
        steps_raw = entry.get("steps", [])
        if not isinstance(steps_raw, list) or not 0 < len(steps_raw) <= MAX_STEPS:
            continue
        steps = []
        for step in steps_raw:
            if (
                isinstance(step, dict)
                and isinstance(step.get("action"), str)
                and isinstance(step.get("params", {}), dict)
            ):
                steps.append((step["action"], dict(step.get("params", {}))))
        if len(steps) == len(steps_raw):
            result[name] = Routine(name, str(entry.get("title", name)), tuple(steps), origin)
    return result


class RoutineCatalog:
    def __init__(self, config_dir: Path, plugins: PluginManager | None = None) -> None:
        self.file = config_dir / "routines.json"
        self.plugins = plugins
        if not self.file.exists():
            self.file.parent.mkdir(parents=True, exist_ok=True)
            self.file.write_text(
                json.dumps(EXAMPLE, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )

    def all(self) -> dict[str, Routine]:
        routines: dict[str, Routine] = {}
        if self.plugins is not None:
            routines.update(self.plugins.routines())
        try:
            raw = json.loads(self.file.read_text(encoding="utf-8") or "{}")
            if isinstance(raw, dict):
                routines.update(parse_routines(raw))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("cannot read %s: %s", self.file, exc)
        return routines

    def get(self, name: str) -> Routine | None:
        return self.all().get(name)
