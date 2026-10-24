"""End a suspicious process (system processes and SENTINEL itself are protected)."""

from __future__ import annotations

import psutil

from sentinel.monitor.processes import protection_reason
from sentinel.response.base import (
    CommandResult,
    PluginContext,
    ResponseCommand,
    ResponseEvent,
    RiskLevel,
)


class KillProcess(ResponseCommand):
    name = "kill_process"
    description = "Ends the process named in the alert (not undoable, system processes protected)."
    risk_level = RiskLevel.HIGH
    requires_pin = True
    requires_confirm = True
    timeout_s = 10.0

    def can_run(self, event: ResponseEvent) -> bool:
        pid = event.detail.get("pid")
        return isinstance(pid, int) and pid > 4 and psutil.pid_exists(pid)

    def run(self, event: ResponseEvent, ctx: PluginContext) -> CommandResult:
        pid = int(event.detail["pid"])
        reason = protection_reason(pid)
        if reason:
            return CommandResult(ok=False, message=f"Process {pid} is protected: {reason}")
        proc = psutil.Process(pid)
        name = proc.name()
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except psutil.TimeoutExpired:
            proc.kill()
        return CommandResult(
            ok=True,
            message=f"{name} (PID {pid}) ended",
            log=[f"Alert {event.alert_id}: {name} (PID {pid}) ended"],
        )
