"""Raise a loud alarm: notification, spoken announcement and push to the phone."""

from __future__ import annotations

from sentinel.response.base import (
    CommandResult,
    PluginContext,
    ResponseCommand,
    ResponseEvent,
    RiskLevel,
)


class RaiseAlarm(ResponseCommand):
    name = "raise_alarm"
    description = "Raises the alert loudly (notification, voice, ntfy) and waits for you."
    risk_level = RiskLevel.LOW
    requires_pin = False
    requires_confirm = False
    timeout_s = 10.0

    def can_run(self, event: ResponseEvent) -> bool:
        return event.severity in ("warn", "critical")

    def run(self, event: ResponseEvent, ctx: PluginContext) -> CommandResult:
        ctx.notify("SENTINEL alert", f"{event.title} - please check", "critical")
        return CommandResult(
            ok=True, message="Alarm raised", log=[f"Alert {event.alert_id} reported"]
        )
