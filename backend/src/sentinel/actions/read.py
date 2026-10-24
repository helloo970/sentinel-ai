"""Read actions. Results from foreign sources are marked untrusted."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from sentinel.monitor import autostart, processes
from sentinel.permissions.models import ActionResult, ActionSpec, NoParams, Risk, StrictParams

if TYPE_CHECKING:
    from sentinel.permissions import ActionRegistry
    from sentinel.services import Services


class ProcessParams(StrictParams):
    name_filter: str | None = Field(default=None, max_length=60)
    limit: int = Field(default=15, ge=1, le=50)


class AlertParams(StrictParams):
    status: Literal["new", "ack", "all"] = "new"
    limit: int = Field(default=20, ge=1, le=100)


class QueryParams(StrictParams):
    query: str = Field(min_length=1, max_length=200)


class LimitParams(StrictParams):
    limit: int = Field(default=30, ge=1, le=200)


def _system_info(_: NoParams, svc: Services) -> ActionResult:
    return ActionResult("System info read", processes.system_info())


def _processes(p: ProcessParams, svc: Services) -> ActionResult:
    return ActionResult(
        "Processes read", processes.top_processes(p.limit, p.name_filter), untrusted=True
    )


def _ports(_: NoParams, svc: Services) -> ActionResult:
    return ActionResult("Open ports read", processes.list_listening_ports()[:100], untrusted=True)


def _autostart(_: NoParams, svc: Services) -> ActionResult:
    return ActionResult("Autostart read", autostart.list_autostart()[:100], untrusted=True)


def _alerts(p: AlertParams, svc: Services) -> ActionResult:
    rows = svc.alerts.list(None if p.status == "all" else p.status, p.limit)
    data = [
        {k: r[k] for k in ("id", "ts", "rule", "severity", "title", "detail", "status")}
        for r in rows
    ]
    return ActionResult(f"{len(data)} alerts", data, untrusted=True)


def _memory_search(p: QueryParams, svc: Services) -> ActionResult:
    hits = svc.memory.search(p.query, limit=8)
    return ActionResult(
        f"{len(hits)} memories",
        [{"id": h["id"], "text": h["text"], "category": h["category"]} for h in hits],
    )


def _reminders(_: NoParams, svc: Services) -> ActionResult:
    return ActionResult("Reminders read", svc.reminders.list())


def _devices(_: NoParams, svc: Services) -> ActionResult:
    return ActionResult("Devices read", svc.network.devices(), untrusted=True)


def _server_status(_: NoParams, svc: Services) -> ActionResult:
    if not svc.server.configured:
        return ActionResult("Server agent is not set up", None)
    data = asyncio.run(svc.server.fetch())
    return ActionResult("Server status read", data, untrusted=True)


def _headlines(_: NoParams, svc: Services) -> ActionResult:
    data = asyncio.run(svc.news.headlines())
    compact = [
        {"quelle": f["name"], "region": f["region"], "meldungen": [i["title"] for i in f["items"]]}
        for f in data["feeds"]
    ]
    return ActionResult("Headlines read", compact, untrusted=True)


def _sysmon(p: LimitParams, svc: Services) -> ActionResult:
    events = svc.monitor.sysmon.read_latest(min(p.limit, 100))
    return ActionResult(
        f"{len(events)} Sysmon events ({svc.monitor.sysmon.status})", events, untrusted=True
    )


def _weather(_: NoParams, svc: Services) -> ActionResult:
    data = asyncio.run(svc.weather.home_weather())
    if not data.get("configured"):
        return ActionResult("No location set (SENTINEL_HOME_CITY)", data)
    return ActionResult(data.get("spoken") or data.get("error", "Weather not available"), data)


def _mail(_: NoParams, svc: Services) -> ActionResult:
    from sentinel.mail.service import MailError

    if not svc.mail.configured:
        return ActionResult("Gmail is not connected", [])
    try:
        mails = svc.mail.unread()
    except MailError as exc:
        return ActionResult(f"Gmail not readable: {exc}", [])
    return ActionResult(f"{len(mails)} unread emails", mails, untrusted=True)


def _programs(_: NoParams, svc: Services) -> ActionResult:
    return ActionResult(
        "Programs on the allowlist",
        [{"name": p.name, "title": p.title} for p in svc.programs.all().values()],
    )


def _routines(_: NoParams, svc: Services) -> ActionResult:
    return ActionResult(
        "Routines",
        [
            {"name": r.name, "title": r.title, "steps": [s[0] for s in r.steps]}
            for r in svc.routines.all().values()
        ],
    )


def register(registry: ActionRegistry) -> None:
    specs = [
        ActionSpec(
            "system_info",
            "System info",
            "CPU, RAM, disk, uptime of the PC.",
            Risk.READ,
            NoParams,
            _system_info,
        ),
        ActionSpec(
            "list_processes",
            "Processes",
            "Running processes (sorted by RAM, optionally filtered by name).",
            Risk.READ,
            ProcessParams,
            _processes,
            untrusted_output=True,
        ),
        ActionSpec(
            "list_ports",
            "Open ports",
            "Listening network ports and processes.",
            Risk.READ,
            NoParams,
            _ports,
            untrusted_output=True,
        ),
        ActionSpec(
            "list_autostart",
            "Autostart",
            "Autostart entries (registry, folders).",
            Risk.READ,
            NoParams,
            _autostart,
            untrusted_output=True,
        ),
        ActionSpec(
            "list_alerts",
            "Alerts",
            "Security alerts from SENTINEL.",
            Risk.READ,
            AlertParams,
            _alerts,
            untrusted_output=True,
        ),
        ActionSpec(
            "memory_search",
            "Search memory",
            "Searches the user's saved memories.",
            Risk.READ,
            QueryParams,
            _memory_search,
        ),
        ActionSpec(
            "list_reminders",
            "Reminders",
            "Open reminders and timers.",
            Risk.READ,
            NoParams,
            _reminders,
        ),
        ActionSpec(
            "network_devices",
            "Home network devices",
            "Known devices on your own subnet.",
            Risk.READ,
            NoParams,
            _devices,
            untrusted_output=True,
        ),
        ActionSpec(
            "server_status",
            "Server status",
            "State of the Ubuntu server via the server agent.",
            Risk.READ,
            NoParams,
            _server_status,
            untrusted_output=True,
        ),
        ActionSpec(
            "news_headlines",
            "Headlines",
            "Current headlines from Germany and the world (RSS).",
            Risk.READ,
            NoParams,
            _headlines,
            untrusted_output=True,
        ),
        ActionSpec(
            "sysmon_events",
            "Sysmon events",
            "Latest Sysmon events.",
            Risk.READ,
            LimitParams,
            _sysmon,
            untrusted_output=True,
        ),
        ActionSpec(
            "weather_today",
            "Weather",
            "Current weather and daily forecast at the home location.",
            Risk.READ,
            NoParams,
            _weather,
        ),
        ActionSpec(
            "mail_unread",
            "Unread emails",
            "Unread and important Gmail emails of the last 3 days (sender, subject).",
            Risk.READ,
            NoParams,
            _mail,
            untrusted_output=True,
        ),
        ActionSpec(
            "list_programs",
            "Programs",
            "Programs on the allowlist (names).",
            Risk.READ,
            NoParams,
            _programs,
        ),
        ActionSpec(
            "list_routines", "Routines", "Available routines.", Risk.READ, NoParams, _routines
        ),
    ]
    for spec in specs:
        registry.register(spec)
