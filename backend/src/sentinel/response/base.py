"""Base class for defense commands (response plugins).

A plugin never decides by itself whether it may run. It only describes what it does
and how risky it is. Execution happens exclusively through the permissions engine.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")


class RiskLevel(StrEnum):
    LOW = "low"  # e.g. show a notice, copy files
    MEDIUM = "medium"  # undoable (undo() required)
    HIGH = "high"  # not undoable, always confirmation
    CRITICAL = "critical"  # always confirmation + PIN


@dataclass(frozen=True)
class ResponseEvent:
    """Event from an alert. ALL fields are untrusted."""

    alert_id: int
    rule: str
    severity: str
    title: str
    detail: dict[str, Any]


@dataclass
class CommandResult:
    ok: bool
    message: str
    log: list[str] = field(default_factory=list)
    undo: dict[str, Any] | None = None  # only for undoable commands


@dataclass(frozen=True)
class PluginContext:
    """What a plugin may use - deliberately kept small.

    No access to the engine, database, network clients or secrets.
    """

    quarantine_dir: str
    managed_folders: tuple[str, ...]
    rescue_dir: str
    rescue_folders: tuple[str, ...]
    rescue_max_mb: int
    log: Any  # logging.Logger named after the plugin
    notify: Callable[[str, str, str], None]  # (title, text, severity)
    record_quarantine: Callable[[dict[str, Any]], int]


class ResponseCommand:
    """Base class. Required fields as class attributes, plus can_run() and run()."""

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    risk_level: ClassVar[RiskLevel] = RiskLevel.HIGH
    requires_pin: ClassVar[bool] = False
    requires_confirm: ClassVar[bool] = True
    timeout_s: ClassVar[float] = 10.0

    def can_run(self, event: ResponseEvent) -> bool:
        """Does the command fit this event? Must not change anything."""
        raise NotImplementedError

    def run(self, event: ResponseEvent, ctx: PluginContext) -> CommandResult:
        """Runs the action. Only called after approval by the engine."""
        raise NotImplementedError

    def undo(self, data: dict[str, Any], ctx: PluginContext) -> str:
        """Optional: undoes run(). Only needed if run() returns undo data."""
        raise NotImplementedError

    @classmethod
    def validate_class(cls) -> list[str]:
        """Returns errors in the plugin definition (empty = valid)."""
        errors = []
        if not NAME_RE.match(cls.name or ""):
            errors.append("name missing or invalid (a-z, 0-9, _; 3-41 characters)")
        if not cls.description or len(cls.description) > 300:
            errors.append("description missing or longer than 300 characters")
        if not isinstance(cls.risk_level, RiskLevel):
            errors.append("risk_level must be a RiskLevel")
        try:
            timeout_ok = 0 < float(cls.timeout_s) <= 60
        except (TypeError, ValueError):
            timeout_ok = False
        if not timeout_ok:
            errors.append("timeout_s must be between 0 and 60")
        if cls.risk_level is RiskLevel.CRITICAL and not (cls.requires_pin and cls.requires_confirm):
            errors.append("critical commands need requires_pin and requires_confirm")
        for method in ("can_run", "run"):
            if getattr(cls, method) is getattr(ResponseCommand, method):
                errors.append(f"{method}() is not implemented")
        if cls.risk_level is RiskLevel.MEDIUM and cls.undo is ResponseCommand.undo:
            errors.append("medium means undoable: undo() is missing")
        return errors
