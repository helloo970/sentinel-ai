"""Move a file from an approved folder into quarantine (never delete)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sentinel.automation import files
from sentinel.permissions.models import ActionDenied
from sentinel.response.base import (
    CommandResult,
    PluginContext,
    ResponseCommand,
    ResponseEvent,
    RiskLevel,
)


class QuarantineFile(ResponseCommand):
    name = "quarantine_file"
    description = "Moves the file named in the alert into quarantine, renamed (undoable)."
    risk_level = RiskLevel.MEDIUM
    requires_pin = False
    requires_confirm = True
    timeout_s = 30.0

    def can_run(self, event: ResponseEvent) -> bool:
        path = event.detail.get("path")
        return isinstance(path, str) and Path(path).is_file()

    def run(self, event: ResponseEvent, ctx: PluginContext) -> CommandResult:
        roots = [Path(p) for p in ctx.managed_folders]
        try:
            path = files.resolve_inside(str(event.detail["path"]), roots)
        except ActionDenied as exc:
            return CommandResult(ok=False, message=str(exc))
        meta = files.quarantine(path, Path(ctx.quarantine_dir))
        meta["id"] = ctx.record_quarantine(meta)
        return CommandResult(
            ok=True,
            message=f"{path.name} in quarantine",
            log=[f"Alert {event.alert_id}: {path.name} -> quarantine"],
            undo=meta,
        )

    def undo(self, data: dict[str, Any], ctx: PluginContext) -> str:
        return f"{files.restore(data).name} restored"
