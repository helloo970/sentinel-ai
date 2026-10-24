"""Automation: programs, reminders, system control, routines, files."""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import psutil
from pydantic import Field, model_validator

from sentinel.automation import files, syscontrol
from sentinel.automation.programs import NAME_RE
from sentinel.permissions.models import (
    CURRENT_REQUEST,
    ActionDenied,
    ActionResult,
    ActionSpec,
    Risk,
    Source,
    StrictParams,
)
from sentinel.secrets_filter import contains_secret

if TYPE_CHECKING:
    from sentinel.permissions import ActionRegistry
    from sentinel.services import Services

NAME_PATTERN = NAME_RE.pattern


class ProgramParams(StrictParams):
    name: str = Field(pattern=NAME_PATTERN)


def _program(p: ProgramParams, svc: Services) -> Any:
    program = svc.programs.get(p.name)
    if program is None:
        raise ActionDenied(f"'{p.name}' is not on the program allowlist")
    return program


def _check_program(p: ProgramParams, svc: Services) -> None:
    _program(p, svc)


def _launch(p: ProgramParams, svc: Services) -> ActionResult:
    program = _program(p, svc)
    pid = svc.programs.launch(program)
    return ActionResult(
        f"{program.title} started", {"pid": pid}, undo={"pid": pid, "name": program.name}
    )


def _undo_launch(data: dict[str, Any], svc: Services) -> str:
    program = svc.programs.get(data["name"])
    try:
        proc = psutil.Process(int(data["pid"]))
        if program and program.matches(proc.exe()):
            proc.terminate()
            return f"{program.title} closed again"
    except psutil.Error:
        pass
    return "Program was no longer running"


def _close(p: ProgramParams, svc: Services) -> ActionResult:
    program = _program(p, svc)
    closed = svc.programs.close(program)
    if closed == 0:
        return ActionResult(f"{program.title} was not running", {"closed": 0})
    return ActionResult(
        f"{program.title} closed ({closed})", {"closed": closed}, undo={"name": program.name}
    )


def _undo_close(data: dict[str, Any], svc: Services) -> str:
    program = svc.programs.get(data["name"])
    if program is None:
        raise ActionDenied("Program is no longer on the allowlist")
    svc.programs.launch(program)
    return f"{program.title} started again"


class ReminderParams(StrictParams):
    text: str = Field(min_length=1, max_length=200)
    minutes: int | None = Field(default=None, ge=1, le=60 * 24 * 14)
    at: str | None = Field(
        default=None,
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
        description="Time of day HH:MM (today or tomorrow)",
    )

    @model_validator(mode="after")
    def _one_time(self) -> ReminderParams:
        if (self.minutes is None) == (self.at is None):
            raise ValueError("provide exactly one of 'minutes' or 'at'")
        return self


def due_time(p: ReminderParams, now: datetime | None = None) -> float:
    now = now or datetime.now()
    if p.minutes is not None:
        return (now + timedelta(minutes=p.minutes)).timestamp()
    hour, minute = map(int, (p.at or "00:00").split(":"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target.timestamp()


def _check_reminder(p: ReminderParams, svc: Services) -> None:
    if contains_secret(p.text):
        raise ActionDenied("Reminders must not contain secrets")


def _reminder(p: ReminderParams, svc: Services) -> ActionResult:
    reminder = svc.reminders.add(p.text, due_time(p))
    when = datetime.fromtimestamp(reminder["due"]).strftime("%b %d, %H:%M")
    return ActionResult(f"Reminder set for {when}", reminder, undo={"id": reminder["id"]})


def _undo_reminder(data: dict[str, Any], svc: Services) -> str:
    svc.reminders.cancel(int(data["id"]))
    return "Reminder removed"


class IdParams(StrictParams):
    id: int = Field(ge=1)


def _cancel_reminder(p: IdParams, svc: Services) -> ActionResult:
    if not svc.reminders.cancel(p.id):
        raise ActionDenied("No open reminder with this ID")
    return ActionResult("Reminder deleted", None, undo={"id": p.id})


def _undo_cancel(data: dict[str, Any], svc: Services) -> str:
    svc.reminders.restore(int(data["id"]))
    return "Reminder restored"


class ControlParams(StrictParams):
    command: Literal[
        "volume_up", "volume_down", "mute", "play_pause", "next_track", "previous_track", "lock"
    ]
    repeat: int = Field(default=1, ge=1, le=10)


def _control(p: ControlParams, svc: Services) -> ActionResult:
    return ActionResult(syscontrol.run(p.command, p.repeat))


class RoutineParams(StrictParams):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_\-]{0,31}$")


def _check_routine(p: RoutineParams, svc: Services) -> None:
    routine = svc.routines.get(p.name)
    if routine is None:
        raise ActionDenied(f"Routine '{p.name}' does not exist")
    for action, _params in routine.steps:
        spec = svc.registry.get(action)
        if spec is None:
            raise ActionDenied(f"Routine contains unknown action '{action}'")
        if spec.risk not in (Risk.READ, Risk.LOW, Risk.REVERSIBLE) or action == "run_routine":
            raise ActionDenied(f"Action '{action}' is not allowed in routines")


def _run_routine(p: RoutineParams, svc: Services) -> ActionResult:
    routine = svc.routines.get(p.name)
    if routine is None:
        raise ActionDenied(f"Routine '{p.name}' does not exist")
    ctx = CURRENT_REQUEST.get()
    source = ctx.source if ctx else Source.USER
    results = []
    for action, params in routine.steps:
        outcome = svc.engine.request(
            action,
            params,
            source=source,
            actor=f"routine:{routine.name}",
            tainted=bool(ctx and ctx.tainted),
        )
        results.append({"action": action, "status": outcome.status, "message": outcome.message})
    ok = sum(1 for r in results if r["status"] == "executed")
    return ActionResult(f"Routine {routine.title}: {ok}/{len(results)} steps executed", results)


class FolderParams(StrictParams):
    folder: str = Field(min_length=3, max_length=400)


def _check_organize(p: FolderParams, svc: Services) -> None:
    path = files.resolve_inside(p.folder, svc.config.managed_folders)
    if not path.is_dir():
        raise ActionDenied("Not a folder")


def _preview_organize(p: FolderParams, svc: Services) -> str:
    path = files.resolve_inside(p.folder, svc.config.managed_folders)
    moves = files.plan_organize(path)
    counts: dict[str, int] = {}
    for _src, dst in moves:
        counts[dst.parent.name] = counts.get(dst.parent.name, 0) + 1
    summary = ", ".join(f"{n}× {cat}" for cat, n in sorted(counts.items())) or "nothing to do"
    return f"Tidy {path.name}: {summary}"


def _organize(p: FolderParams, svc: Services) -> ActionResult:
    path = files.resolve_inside(p.folder, svc.config.managed_folders)
    moved = files.apply_moves(files.plan_organize(path))
    return ActionResult(
        f"{len(moved)} files sorted into {path.name}",
        {"moved": len(moved)},
        undo={"moves": moved} if moved else None,
    )


def _undo_organize(data: dict[str, Any], svc: Services) -> str:
    return f"{files.revert_moves(data['moves'])} files moved back"


class PathParams(StrictParams):
    path: str = Field(min_length=3, max_length=400)


def _roots(svc: Services) -> list[Path]:
    return list(svc.config.managed_folders)


def _check_path(p: PathParams, svc: Services) -> None:
    path = files.resolve_inside(p.path, _roots(svc))
    if not path.is_file():
        raise ActionDenied("Only single files are allowed")
    if path.is_relative_to(svc.config.data_dir.resolve()):
        raise ActionDenied("SENTINEL's own files are excluded")


def _preview_path(p: PathParams, svc: Services) -> str:
    path = Path(p.path)
    size = path.stat().st_size if path.is_file() else 0
    return f"{path.name} ({size / 1024:.0f} KB) in {path.parent}"


def _quarantine(p: PathParams, svc: Services) -> ActionResult:
    path = files.resolve_inside(p.path, _roots(svc))
    meta = files.quarantine(path, svc.config.quarantine_dir)
    qid = svc.db.execute(
        "INSERT INTO quarantine(original_path, stored_path, sha256, ts) VALUES(?,?,?,?)",
        (meta["original_path"], meta["stored_path"], meta["sha256"], time.time()),
    )
    return ActionResult(f"{path.name} moved to quarantine", {"id": qid}, undo={**meta, "id": qid})


def _undo_quarantine(data: dict[str, Any], svc: Services) -> str:
    target = files.restore(data)
    svc.db.execute("UPDATE quarantine SET restored = 1 WHERE id = ?", (data["id"],))
    return f"{target.name} restored"


def _check_restore(p: IdParams, svc: Services) -> None:
    row = svc.db.query_one("SELECT * FROM quarantine WHERE id = ? AND restored = 0", (p.id,))
    if row is None:
        raise ActionDenied("No entry in quarantine")


def _restore(p: IdParams, svc: Services) -> ActionResult:
    row = svc.db.query_one("SELECT * FROM quarantine WHERE id = ?", (p.id,))
    if row is None:
        raise ActionDenied("No entry in quarantine")
    target = files.restore(row)
    svc.db.execute("UPDATE quarantine SET restored = 1 WHERE id = ?", (p.id,))
    return ActionResult(f"{target.name} restored", {"path": str(target)})


def _recycle(p: PathParams, svc: Services) -> ActionResult:
    path = files.resolve_inside(p.path, _roots(svc))
    files.to_recycle_bin(path)
    return ActionResult(f"{path.name} moved to the recycle bin")


class ModeParams(StrictParams):
    name: str = Field(pattern=NAME_PATTERN)
    minutes: int | None = Field(default=None, ge=1, le=8 * 60)


def _check_mode(p: ModeParams, svc: Services) -> None:
    if svc.modes.get(p.name) is None:
        raise ActionDenied(f"Mode '{p.name}' does not exist")


def _preview_mode(p: ModeParams, svc: Services) -> str:
    mode = svc.modes.get(p.name)
    if mode is None:
        return p.name
    parts = []
    if mode.launch:
        parts.append("starts " + ", ".join(mode.launch))
    if mode.close:
        parts.append("closes " + ", ".join(mode.close))
    if mode.quiet:
        parts.append("quiet")
    minutes = p.minutes or mode.minutes
    if minutes:
        parts.append(f"{minutes} minutes")
    return f"{mode.title}: " + (", ".join(parts) or "no steps")


def _start_mode(p: ModeParams, svc: Services) -> ActionResult:
    mode = svc.modes.get(p.name)
    if mode is None:
        raise ActionDenied(f"Mode '{p.name}' does not exist")
    state = svc.modes.start(mode, p.minutes)
    message = f"{mode.title} on"
    if state.get("ends"):
        message += f" until {datetime.fromtimestamp(state['ends']):%H:%M}"
    missing = [s["action"].split(":", 1)[1] for s in state["steps"] if s["status"] == "missing"]
    if missing:
        message += f" (not on the allowlist: {', '.join(missing)})"
    return ActionResult(message, state, undo={"name": mode.name})


def _undo_mode(data: dict[str, Any], svc: Services) -> str:
    active = svc.modes.active()
    if not active or active.get("name") != data.get("name"):
        return "Mode had already ended"
    return svc.modes.stop(reason="undone")


class NoParams(StrictParams):
    pass


def _stop_mode(p: NoParams, svc: Services) -> ActionResult:
    return ActionResult(svc.modes.stop())


_SAFE_TEXT = re.compile(r"^[^\x00-\x1f]*$")


def register(registry: ActionRegistry) -> None:
    specs = [
        ActionSpec(
            "launch_program",
            "Start program",
            "Starts a program from the allowlist by its name.",
            Risk.LOW,
            ProgramParams,
            _launch,
            undo=_undo_launch,
            check=_check_program,
            preview=lambda p, s: f"Start {_program(p, s).title}",
        ),
        ActionSpec(
            "close_program",
            "Close program",
            "Closes a program from the allowlist (normal exit).",
            Risk.REVERSIBLE,
            ProgramParams,
            _close,
            undo=_undo_close,
            check=_check_program,
            preview=lambda p, s: f"Close {_program(p, s).title}",
        ),
        ActionSpec(
            "set_reminder",
            "Reminder",
            "Sets a reminder or timer (in minutes or at a time of day).",
            Risk.LOW,
            ReminderParams,
            _reminder,
            undo=_undo_reminder,
            check=_check_reminder,
            preview=lambda p, s: f"Reminder: {p.text}",
        ),
        ActionSpec(
            "cancel_reminder",
            "Delete reminder",
            "Deletes an open reminder.",
            Risk.LOW,
            IdParams,
            _cancel_reminder,
            undo=_undo_cancel,
        ),
        ActionSpec(
            "system_control",
            "System control",
            "Volume, media or lock the screen.",
            Risk.LOW,
            ControlParams,
            _control,
            preview=lambda p, s: f"System control: {p.command}",
        ),
        ActionSpec(
            "run_routine",
            "Run routine",
            "Runs a fixed routine; every step is checked individually.",
            Risk.LOW,
            RoutineParams,
            _run_routine,
            check=_check_routine,
            preview=lambda p, s: f"Routine {p.name}",
        ),
        ActionSpec(
            "start_mode",
            "Start mode",
            "Starts a fixed mode (e.g. gaming, fokus): start/close programs, "
            "quiet notifications, optional timer in minutes.",
            Risk.LOW,
            ModeParams,
            _start_mode,
            undo=_undo_mode,
            check=_check_mode,
            preview=_preview_mode,
        ),
        ActionSpec(
            "stop_mode",
            "Stop mode",
            "Ends the active mode and undoes everything.",
            Risk.LOW,
            NoParams,
            _stop_mode,
        ),
        ActionSpec(
            "organize_folder",
            "Tidy folder",
            "Sorts files of an approved folder into subfolders by type (undoable).",
            Risk.REVERSIBLE,
            FolderParams,
            _organize,
            undo=_undo_organize,
            check=_check_organize,
            preview=_preview_organize,
            undo_window_s=7 * 24 * 3600,
        ),
        ActionSpec(
            "quarantine_file",
            "Quarantine",
            "Moves a suspicious file from an approved folder into quarantine (undoable).",
            Risk.REVERSIBLE,
            PathParams,
            _quarantine,
            undo=_undo_quarantine,
            check=_check_path,
            preview=_preview_path,
        ),
        ActionSpec(
            "restore_quarantine",
            "Restore from quarantine",
            "Restores a file.",
            Risk.REVERSIBLE,
            IdParams,
            _restore,
            check=_check_restore,
            llm_exposed=False,
        ),
        ActionSpec(
            "recycle_file",
            "Move to recycle bin",
            "Moves a file from an approved folder into the recycle bin (always with confirmation).",
            Risk.FINAL,
            PathParams,
            _recycle,
            check=_check_path,
            preview=_preview_path,
        ),
    ]
    for spec in specs:
        registry.register(spec)
