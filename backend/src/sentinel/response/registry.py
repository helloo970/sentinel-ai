"""Loads, checks and manages response plugins.

Security:
- Built-in plugins come from the package `sentinel.response.builtin`. Custom plugins
  only from <data_dir>/response_plugins/, never from the working directory.
- A custom plugin is only loaded once the user has approved exactly this file version
  (SHA-256) with a PIN. Any change locks it again. Exactly the bytes whose hash was
  checked are loaded (the file is not read again).
- Every plugin is registered as an ActionSpec: autonomy, emergency stop, rate limit,
  approvals and audit log apply unchanged. The language model does not see plugins.
- Errors and timeouts are reported, never passed through to the core.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.abc
import importlib.util
import inspect
import json
import logging
import pkgutil
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

from pydantic import Field

from sentinel.permissions.models import (
    ActionDenied,
    ActionResult,
    ActionSpec,
    Risk,
    Severity,
    StrictParams,
)
from sentinel.response import builtin
from sentinel.response.base import (
    CommandResult,
    PluginContext,
    ResponseCommand,
    ResponseEvent,
    RiskLevel,
)

if TYPE_CHECKING:
    from sentinel.services import Services

log = logging.getLogger(__name__)
MAX_PLUGIN_BYTES = 200_000
PREFIX = "response_"


class AlertParams(StrictParams):
    """Plugins only get an alert ID - the registry loads the event itself."""

    alert_id: int = Field(ge=1)


@dataclass
class PluginInfo:
    name: str
    file: str
    origin: str  # builtin | user
    sha256: str
    status: str  # loaded | pending_approval | disabled | error
    error: str | None = None
    description: str | None = None
    risk_level: str | None = None
    requires_pin: bool | None = None
    priority: int = 999


class _BytesLoader(importlib.abc.SourceLoader):
    """Loads a module from already verified bytes (prevents swapping after the check)."""

    def __init__(self, path: Path, source: bytes) -> None:
        self.path = str(path)
        self.source = source

    def get_filename(self, fullname: str) -> str:
        return self.path

    def get_data(self, path: str) -> bytes:
        return self.source


def map_risk(cmd: type[ResponseCommand]) -> Risk:
    if cmd.risk_level is RiskLevel.CRITICAL:
        return Risk.FINAL  # always confirmation
    if cmd.risk_level is RiskLevel.HIGH or cmd.requires_confirm or cmd.requires_pin:
        return Risk.HIGH
    if cmd.risk_level is RiskLevel.MEDIUM:
        return Risk.REVERSIBLE
    return Risk.LOW


class PluginTimeoutError(RuntimeError):
    pass


def call_with_timeout(fn: Any, timeout: float) -> Any:
    """Calls plugin code with a time limit and turns every error into a RuntimeError."""
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:
            box["error"] = exc

    worker = threading.Thread(target=target, daemon=True, name="response-plugin")
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        # Python cannot hard-kill threads: the result is discarded and reported
        raise PluginTimeoutError(f"Time limit of {timeout:.0f} s exceeded")
    error = box.get("error")
    if isinstance(error, ActionDenied):
        raise error
    if error is not None:
        raise RuntimeError(f"{type(error).__name__}: {error}")
    return box.get("value")


class PluginRegistry:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self.user_dir = svc.config.data_dir / "response_plugins"
        self.config_file = svc.config.config_dir / "response_plugins.json"
        self.commands: dict[str, ResponseCommand] = {}
        self.infos: list[PluginInfo] = []
        self._lock = threading.Lock()
        if not self.config_file.exists():
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            self.config_file.write_text(
                json.dumps({"order": [], "disabled": []}, indent=2) + "\n", encoding="utf-8"
            )

    # ------------------------------------------------------------------ Configuration

    def _config(self) -> dict[str, list[str]]:
        try:
            data = json.loads(self.config_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        order = [str(x) for x in data.get("order", []) if isinstance(x, str)]
        disabled = [str(x) for x in data.get("disabled", []) if isinstance(x, str)]
        return {"order": order, "disabled": disabled}

    def _approved(self) -> dict[str, str]:
        raw = self.svc.store.get("response_plugins_approved", "{}") or "{}"
        try:
            return {str(k): str(v) for k, v in json.loads(raw).items()}
        except (ValueError, AttributeError):
            return {}

    def approve(self, file: str, sha256: str, pin: str | None, *, actor: str = "user") -> bool:
        """Approves exactly this file version. Only from the UI and only with a PIN."""
        if not self.svc.pin.verify(pin, actor=actor):
            return False
        info = next((i for i in self.infos if i.file == file and i.origin == "user"), None)
        if info is None or info.sha256 != sha256:
            return False
        approved = self._approved()
        approved[file] = sha256
        self.svc.store.set("response_plugins_approved", json.dumps(approved))
        self.svc.audit.append(
            actor=actor,
            source="user",
            action="response_plugin_approve",
            params={"file": file, "sha256": sha256},
            decision="execute",
        )
        self.reload()
        return True

    def revoke(self, file: str, *, actor: str = "user") -> None:
        approved = self._approved()
        approved.pop(file, None)
        self.svc.store.set("response_plugins_approved", json.dumps(approved))
        self.svc.audit.append(
            actor=actor,
            source="user",
            action="response_plugin_revoke",
            params={"file": file},
            decision="execute",
        )
        self.reload()

    # ------------------------------------------------------------------ Loading

    def _builtin_classes(self) -> list[tuple[str, type[ResponseCommand] | None, str | None]]:
        result: list[tuple[str, type[ResponseCommand] | None, str | None]] = []
        for module_info in pkgutil.iter_modules(builtin.__path__):
            if module_info.name.startswith("_"):
                continue
            try:
                module = importlib.import_module(f"{builtin.__name__}.{module_info.name}")
                result.append((module_info.name, self._single_class(module), None))
            except Exception as exc:
                result.append((module_info.name, None, f"{type(exc).__name__}: {exc}"))
        return result

    @staticmethod
    def _single_class(module: ModuleType) -> type[ResponseCommand]:
        classes = [
            obj
            for _, obj in inspect.getmembers(module, inspect.isclass)
            if issubclass(obj, ResponseCommand)
            and obj is not ResponseCommand
            and obj.__module__ == module.__name__
        ]
        if len(classes) != 1:
            raise ValueError("File must contain exactly one ResponseCommand class")
        return classes[0]

    def _load_user_module(self, path: Path, source: bytes) -> ModuleType:
        name = f"sentinel_user_response_{path.stem}"
        loader = _BytesLoader(path, source)
        spec = importlib.util.spec_from_loader(name, loader, origin=str(path))
        if spec is None:
            raise ImportError("Module cannot be loaded")
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        return module

    def _user_files(self) -> list[Path]:
        if not self.user_dir.is_dir():
            return []
        return [
            p
            for p in sorted(self.user_dir.glob("*.py"))
            if not p.name.startswith("_") and p.is_file() and not p.is_symlink()
        ]

    def _accept(
        self,
        cls: type[ResponseCommand],
        info: PluginInfo,
        commands: dict[str, ResponseCommand],
        disabled: list[str],
    ) -> None:
        errors = cls.validate_class()
        if errors:
            raise ValueError("; ".join(errors))
        if cls.name in commands:
            raise ValueError(f"Name {cls.name} is duplicated")
        info.name = cls.name
        info.description = cls.description
        info.risk_level = str(cls.risk_level)
        info.requires_pin = bool(cls.requires_pin or cls.risk_level is RiskLevel.CRITICAL)
        if cls.name in disabled:
            info.status = "disabled"
            return
        commands[cls.name] = cls()
        info.status = "loaded"

    def reload(self) -> list[PluginInfo]:
        """Reloads all plugins. Broken ones are reported, never loaded."""
        config = self._config()
        approved = self._approved()
        commands: dict[str, ResponseCommand] = {}
        infos: list[PluginInfo] = []
        with self._lock:
            for module_name, cls, error in self._builtin_classes():
                info = PluginInfo(module_name, f"{module_name}.py", "builtin", "", "error", error)
                infos.append(info)
                if cls is None:
                    continue
                try:
                    self._accept(cls, info, commands, config["disabled"])
                except Exception as exc:
                    info.status, info.error = "error", f"{type(exc).__name__}: {exc}"[:300]

            for path in self._user_files():
                info = PluginInfo(path.stem, path.name, "user", "", "error")
                infos.append(info)
                try:
                    source = path.read_bytes()
                    if len(source) > MAX_PLUGIN_BYTES:
                        raise ValueError("Plugin file too large")
                    info.sha256 = hashlib.sha256(source).hexdigest()
                    if approved.get(path.name) != info.sha256:
                        info.status = "pending_approval"
                        continue
                    module = self._load_user_module(path, source)
                    self._accept(self._single_class(module), info, commands, config["disabled"])
                except Exception as exc:  # Plugin errors must never stop the core
                    info.status, info.error = "error", f"{type(exc).__name__}: {exc}"[:300]
                    log.warning("response plugin %s rejected: %s", path.name, info.error)

            order = {name: index for index, name in enumerate(config["order"])}
            for info in infos:
                info.priority = order.get(info.name, 999)
            infos.sort(key=lambda i: (i.priority, i.origin != "builtin", i.name))
            self.commands = dict(
                sorted(commands.items(), key=lambda kv: (order.get(kv[0], 999), kv[0]))
            )
            self.infos = infos
            self.svc.registry.unregister_prefix(PREFIX)
            for command in self.commands.values():
                self.svc.registry.register(self._spec_for(command))
        self.svc.audit.append(
            actor="system",
            source="system",
            action="response_plugins_reload",
            params={"loaded": sorted(self.commands)},
            decision="execute",
        )
        return self.infos

    # ------------------------------------------------------------------ Engine integration

    def _context(self, name: str) -> PluginContext:
        cfg = self.svc.config

        def notify(title: str, message: str, severity: str) -> None:
            level = Severity(severity) if severity in Severity._value2member_map_ else Severity.INFO
            self.svc.notifier.notify(
                title[:120], message[:500], severity=level, speak=level is Severity.CRITICAL
            )

        def record_quarantine(meta: dict[str, Any]) -> int:
            return self.svc.db.execute(
                "INSERT INTO quarantine(original_path, stored_path, sha256, ts) VALUES(?,?,?,?)",
                (
                    str(meta["original_path"]),
                    str(meta["stored_path"]),
                    str(meta["sha256"]),
                    time.time(),
                ),
            )

        return PluginContext(
            quarantine_dir=str(cfg.quarantine_dir),
            managed_folders=tuple(str(p) for p in cfg.managed_folders),
            rescue_dir=str(cfg.data_dir / "rescue"),
            rescue_folders=tuple(str(p) for p in cfg.rescue_folders),
            rescue_max_mb=cfg.rescue_max_mb,
            log=logging.getLogger(f"sentinel.response.{name}"),
            notify=notify,
            record_quarantine=record_quarantine,
        )

    def event(self, alert_id: int) -> ResponseEvent:
        row = self.svc.db.query_one("SELECT * FROM alerts WHERE id = ?", (alert_id,))
        if row is None:
            raise ActionDenied("Alert not found")
        return ResponseEvent(
            alert_id, row["rule"], row["severity"], row["title"], json.loads(row["detail"])
        )

    def matching(self, alert_id: int) -> list[dict[str, Any]]:
        """Commands that fit an alert (for the UI)."""
        event = self.event(alert_id)
        result = []
        for command in self.commands.values():
            cls = type(command)
            try:
                fits = bool(call_with_timeout(lambda c=command: c.can_run(event), cls.timeout_s))
            except Exception:
                fits = False
            if fits:
                spec = self.svc.registry.get(PREFIX + cls.name)
                result.append(
                    {
                        "action": PREFIX + cls.name,
                        "name": cls.name,
                        "description": cls.description,
                        "risk_level": cls.risk_level,
                        "requires_pin": bool(spec and spec.requires_pin),
                    }
                )
        return result

    def _spec_for(self, command: ResponseCommand) -> ActionSpec:
        cls = type(command)

        def check(p: AlertParams, _: Services) -> None:
            event = self.event(p.alert_id)
            try:
                fits = call_with_timeout(lambda: bool(command.can_run(event)), cls.timeout_s)
            except RuntimeError as exc:
                raise ActionDenied(f"{cls.name}: check failed ({exc})") from exc
            if not fits:
                raise ActionDenied(f"{cls.name} does not fit this alert")

        def execute(p: AlertParams, _: Services) -> ActionResult:
            event = self.event(p.alert_id)
            result = call_with_timeout(
                lambda: command.run(event, self._context(cls.name)), cls.timeout_s
            )
            if not isinstance(result, CommandResult):
                raise RuntimeError("Plugin did not return a CommandResult")
            if not result.ok:
                raise ActionDenied(f"{cls.name}: {result.message}")
            undo = result.undo if cls.undo is not ResponseCommand.undo else None
            return ActionResult(
                f"{cls.name}: {result.message}",
                {"log": [str(x)[:300] for x in result.log[:50]]},
                undo=undo,
            )

        def undo(data: dict[str, Any], _: Services) -> str:
            return str(
                call_with_timeout(
                    lambda: command.undo(data, self._context(cls.name)), cls.timeout_s
                )
            )

        has_undo = cls.undo is not ResponseCommand.undo
        return ActionSpec(
            name=PREFIX + cls.name,
            title=f"Defense: {cls.name}",
            description=cls.description,
            risk=map_risk(cls),
            params=AlertParams,
            execute=execute,
            undo=undo if has_undo else None,
            check=check,
            preview=lambda p, s: f"{cls.name} for alert #{p.alert_id}: {cls.description}",
            llm_exposed=False,
            requires_pin=bool(cls.requires_pin or cls.risk_level is RiskLevel.CRITICAL),
        )

    def list(self) -> list[dict[str, Any]]:
        return [asdict(info) for info in self.infos]
