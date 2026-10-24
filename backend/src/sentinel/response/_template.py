"""TEMPLATE for a custom defense command.

How to create a new command:
1. Copy this file to  %LOCALAPPDATA%\\SentinelAI\\response_plugins\\my_command.py
   (Files starting with "_" are never loaded, so copy and rename it.)
2. Adjust the class and fields below. Exactly ONE class per file.
3. In SENTINEL: Settings -> Defense commands -> "Reload". The plugin then shows as
   "waiting for approval". Approve it with your PIN. Any later change to the file
   locks it again until you approve the new version.
4. Test with autonomy level 0 (observer) first: then commands are only suggested.
   A command is triggered in the Security Center on an alert via "Defense".

Rules:
- Defensive only and only on your own PC. No counter-attacks, no access to
  foreign systems, no shell (no shell=True, no os.system, no eval/exec).
- event.* comes from logs and process data and is NOT trustworthy:
  always check paths and names, never run them as a command.
- Never delete anything permanently. Anything undoable returns undo data.
- Never log secrets (passwords, tokens).
- Order/priority and disabling: %LOCALAPPDATA%\\SentinelAI\\config\\response_plugins.json
  {"order": ["raise_alarm", "my_command"], "disabled": []}
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sentinel.response.base import (
    CommandResult,
    PluginContext,
    ResponseCommand,
    ResponseEvent,
    RiskLevel,
)


class MyCommand(ResponseCommand):
    # Unique name: only a-z, 0-9 and "_"
    name = "my_command"
    # Short and clear - shown in the approval preview
    description = "Describes in one sentence what the command does."
    # low = harmless, medium = undoable (undo() required),
    # high = not undoable, critical = always confirmation + PIN
    risk_level = RiskLevel.MEDIUM
    # True = approval only with PIN
    requires_pin = False
    # True = never automatic, always confirmation in the UI
    requires_confirm = True
    # Maximum runtime in seconds (at most 60)
    timeout_s = 10.0

    def can_run(self, event: ResponseEvent) -> bool:
        # Only check, change nothing. Example: only for a specific rule.
        return event.rule == "double_extension" and isinstance(event.detail.get("path"), str)

    def run(self, event: ResponseEvent, ctx: PluginContext) -> CommandResult:
        # The actual action. On errors return CommandResult(ok=False, ...).
        path = Path(str(event.detail.get("path", ""))).resolve()
        allowed = [Path(folder).resolve() for folder in ctx.managed_folders]
        if not any(path.is_relative_to(folder) for folder in allowed):
            return CommandResult(ok=False, message="Path is outside the approved folders")
        ctx.log.info("my_command for alert %s", event.alert_id)
        # ... the action goes here ...
        return CommandResult(
            ok=True,
            message="Action executed",
            log=[f"Alert {event.alert_id}: action for {path.name}"],
            undo={"path": str(path)},  # only for undoable commands
        )

    def undo(self, data: dict[str, Any], ctx: PluginContext) -> str:
        # Undoes run() (offered for up to 60 s after the action).
        return f"Undone for {Path(data['path']).name}"
