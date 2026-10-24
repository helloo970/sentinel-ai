"""Morning briefing (first clap of the day) and the evening daily report.

Both are assembled from our own data, without a language model: free and always
accurate. Mail subjects are only read aloud, never given to the AI.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sentinel.mail.service import MailError
from sentinel.mail.service import spoken as spoken_mail

if TYPE_CHECKING:
    from sentinel.services import Services

log = logging.getLogger(__name__)

MORNING_FROM_HOUR = 4
READ_ACTIONS_PREFIXES = (
    "list_",
    "system_info",
    "memory_search",
    "network_devices",
    "server_status",
    "news_headlines",
    "sysmon_events",
    "weather_",
    "mail_",
)


def greeting(name: str, hour: int) -> str:
    name = name.strip() or "Sir"
    if hour < 11:
        return f"Good morning, {name}."
    if hour < 18:
        return f"Hello {name}."
    return f"Good evening, {name}."


def day_start(now: float | None = None) -> float:
    moment = datetime.fromtimestamp(now or time.time())
    return moment.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


class ReportService:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self._task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------ Morning

    def is_first_wake_today(self, now: float | None = None) -> bool:
        """True exactly once per day (from 4 am) - remembers the day."""
        moment = datetime.fromtimestamp(now or time.time())
        if moment.hour < MORNING_FROM_HOUR:
            return False
        today = moment.strftime("%Y-%m-%d")
        if self.svc.store.get("morning_briefing_date") == today:
            return False
        self.svc.store.set("morning_briefing_date", today)
        return True

    def security_sentence(self) -> str:
        counts = self.svc.alerts.counts()
        server = self.svc.server.last or {}
        parts = []
        if counts["critical"]:
            parts.append(f"{counts['critical']} critical alerts")
        if counts["warn"]:
            parts.append(f"{counts['warn']} warnings")
        text = (
            "Security status: " + " and ".join(parts) + " open."
            if parts
            else "Security status: all quiet."
        )
        if self.svc.engine.halted:
            text += " Heads up, the emergency stop is still active."
        updates = server.get("updates") or {}
        if updates.get("security"):
            text += f" {updates['security']} security updates are waiting on the server."
        return text

    async def morning(self) -> dict[str, Any]:
        now = datetime.now()
        parts: dict[str, str] = {"greeting": greeting(self.svc.config.user_name, now.hour)}
        weather = await self.svc.weather.home_weather()
        if weather.get("spoken"):
            parts["weather"] = weather["spoken"]
        if self.svc.mail.configured:
            try:
                parts["mail"] = spoken_mail(await asyncio.to_thread(self.svc.mail.unread))
            except MailError:
                parts["mail"] = "I couldn't read Gmail just now."
        parts["security"] = self.security_sentence()
        parts["outro"] = "Now the world briefing."
        return {"text": " ".join(parts.values()), "parts": parts, "weather": weather}

    # ------------------------------------------------------------------ Evening

    def daily_data(self, now: float | None = None) -> dict[str, Any]:
        start = day_start(now)
        db = self.svc.db
        alerts = db.query(
            "SELECT severity, title FROM alerts WHERE ts >= ? ORDER BY ts DESC", (start,)
        )
        by_severity = {"info": 0, "warn": 0, "critical": 0}
        for alert in alerts:
            by_severity[alert["severity"]] = by_severity.get(alert["severity"], 0) + 1
        # Audit timestamps are UTC in the format isoformat(timespec="milliseconds")
        start_iso = datetime.fromtimestamp(start, UTC).isoformat(timespec="milliseconds")
        audit = db.query("SELECT action, decision FROM audit WHERE ts >= ?", (start_iso,))
        acting = [a for a in audit if not a["action"].startswith(READ_ACTIONS_PREFIXES)]
        autonomous = db.query_one(
            "SELECT COUNT(*) AS n FROM autonomous_actions WHERE ts >= ?", (start,)
        )
        devices = db.query("SELECT ip, mac FROM devices WHERE first_seen >= ?", (start,))
        pihole = (self.svc.server.last or {}).get("pihole") or {}
        return {
            "alerts": by_severity,
            "top_alerts": [a["title"] for a in alerts if a["severity"] != "info"][:5],
            "executed": sum(1 for a in acting if a["decision"] in ("execute", "approved")),
            "denied": sum(1 for a in acting if a["decision"] == "deny"),
            "autonomous": int(autonomous["n"]) if autonomous else 0,
            "new_devices": len(devices),
            "costs_eur": round(self.svc.costs.today_eur(), 3),
            "pihole_blocked": pihole.get("queries_blocked"),
        }

    @staticmethod
    def daily_text(data: dict[str, Any]) -> str:
        alerts = data["alerts"]
        lines = [
            f"Alerts: {alerts['critical']} critical, {alerts['warn']} warnings, "
            f"{alerts['info']} notices.",
            f"Actions: {data['executed']} executed, {data['autonomous']} of them autonomous; "
            f"{data['denied']} rejected or blocked.",
            f"New devices on the home network: {data['new_devices']}.",
        ]
        if data.get("pihole_blocked") is not None:
            lines.append(f"Pi-hole blocked {data['pihole_blocked']} queries.")
        lines.append(f"AI costs today: €{data['costs_eur']:.2f}.")
        if data["top_alerts"]:
            lines.append("Top alerts: " + "; ".join(data["top_alerts"]) + ".")
        return "\n".join(lines)

    def daily_report(self, *, notify: bool = True) -> dict[str, Any]:
        data = self.daily_data()
        body = self.daily_text(data)
        title = f"Daily report {datetime.now():%b %d, %Y}"
        report_id = self.svc.db.execute(
            "INSERT INTO agent_reports(agent, ts, title, body) VALUES(?,?,?,?)",
            ("daily_report", time.time(), title, body),
        )
        self.svc.bus.publish(
            "agent_report", {"id": report_id, "agent": "daily_report", "title": title}
        )
        if notify:
            first_line = body.splitlines()[0]
            self.svc.notifier.notify(title, first_line, push=True)
        return {"id": report_id, "title": title, "body": body, "data": data}

    def evening_due(self, now: float | None = None) -> bool:
        moment = datetime.fromtimestamp(now or time.time())
        hour, minute = (int(x) for x in self.svc.config.evening_report_time.split(":"))
        if (moment.hour, moment.minute) < (hour, minute):
            return False
        today = moment.strftime("%Y-%m-%d")
        if self.svc.store.get("daily_report_date") == today:
            return False
        self.svc.store.set("daily_report_date", today)
        return True

    async def _loop(self) -> None:
        while True:
            if self.svc.config.evening_report_time and self.evening_due():
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(self.daily_report)
            await asyncio.sleep(60)

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
