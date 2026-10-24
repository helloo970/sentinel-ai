"""KPI board: big numbers from all areas of SENTINEL (read only)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sentinel.services import Services


def _tile(
    key: str, label: str, value: Any, unit: str = "", tone: str = "info", detail: str = ""
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "value": value,
        "unit": unit,
        "tone": tone,
        "detail": detail,
    }


def _count(svc: Services, sql: str, args: tuple[Any, ...] = ()) -> int:
    row = svc.db.query_one(sql, args)
    return int(row["n"]) if row else 0


def collect(svc: Services) -> list[dict[str, Any]]:
    from sentinel.monitor import processes

    tiles: list[dict[str, Any]] = []
    alerts = svc.alerts.counts()
    open_alerts = alerts.get("critical", 0) + alerts.get("warn", 0)
    tone = "crit" if alerts.get("critical") else "warn" if open_alerts else "ok"
    tiles.append(
        _tile(
            "alerts",
            "Open alerts",
            open_alerts,
            "",
            tone,
            f"{alerts.get('critical', 0)} critical",
        )
    )

    pc = processes.system_info()
    tiles.append(
        _tile(
            "cpu",
            "PC load",
            round(pc.get("cpu_percent", 0)),
            "%",
            "info",
            f"{pc.get('process_count', 0)} processes",
        )
    )
    mem = pc.get("memory_percent", 0)
    tiles.append(
        _tile(
            "memory",
            "Memory",
            round(mem),
            "%",
            "warn" if mem > 85 else "info",
            f"of {pc.get('memory_total_gb', 0)} GB",
        )
    )
    disk = pc.get("disk_percent", 0)
    tiles.append(
        _tile(
            "disk",
            "PC disk",
            round(disk),
            "%",
            "warn" if disk > 90 else "info",
            f"{pc.get('disk_free_gb', 0)} GB free",
        )
    )

    devices = _count(svc, "SELECT COUNT(*) AS n FROM devices")
    unknown = _count(svc, "SELECT COUNT(*) AS n FROM devices WHERE trusted = 0")
    tiles.append(
        _tile(
            "devices",
            "Devices on home network",
            devices,
            "",
            "warn" if unknown else "ok",
            f"{unknown} unknown",
        )
    )

    if svc.server.configured:
        last = svc.server.last or {}
        status = last.get("status") or {}
        server_disk = status.get("disk_percent")
        if server_disk is not None:
            tiles.append(
                _tile(
                    "server_disk",
                    "Server disk",
                    round(server_disk),
                    "%",
                    "warn" if server_disk > 90 else "info",
                    f"{status.get('disk_free_gb', 0)} GB free",
                )
            )
        auth = (last.get("auth_failures") or {}).get("failures_1h")
        if auth is not None:
            tiles.append(
                _tile(
                    "server_auth",
                    "Login attempts (1 h)",
                    auth,
                    "",
                    "warn" if auth > 20 else "ok",
                    "failed on the server",
                )
            )
        backups = last.get("backups") or {}
        if backups.get("available"):
            newest = max(
                (p.get("newest") or 0 for p in backups.get("paths", []) if isinstance(p, dict)),
                default=0,
            )
            age = round((time.time() - newest) / 3600) if newest else None
            if age is not None:
                tone = "warn" if age > svc.config.backup_max_age_h else "ok"
                tiles.append(_tile("backup", "Last backup", age, "h", tone, "on the server"))

    costs = svc.costs.summary()
    tiles.append(
        _tile(
            "ai_today",
            "AI costs today",
            round(svc.costs.today_eur(), 2),
            "€",
            "info",
            f"Limit €{costs.get('daily_limit_eur', '')}",
        )
    )
    tiles.append(
        _tile(
            "ai_month",
            "AI costs this month",
            round(svc.costs.month_eur(), 2),
            "€",
            "info",
            f"Limit €{costs.get('monthly_limit_eur', '')}",
        )
    )

    soon = time.time() + 24 * 3600
    open_reminders = _count(svc, "SELECT COUNT(*) AS n FROM reminders WHERE done = 0")
    tiles.append(
        _tile(
            "reminders",
            "Due in 24 h",
            _count(svc, "SELECT COUNT(*) AS n FROM reminders WHERE done = 0 AND due <= ?", (soon,)),
            "",
            "info",
            f"{open_reminders} open",
        )
    )
    tiles.append(
        _tile(
            "shopping",
            "Shopping list",
            _count(svc, "SELECT COUNT(*) AS n FROM shopping_items WHERE done = 0"),
            "",
            "info",
            "items",
        )
    )
    stats = svc.brain.store.stats()
    tiles.append(
        _tile(
            "brain",
            "Thoughts in the brain",
            stats["nodes"],
            "",
            "info",
            f"{stats['edges']} connections",
        )
    )
    tiles.append(
        _tile(
            "autonomy",
            "Autonomy level",
            int(svc.engine.autonomy),
            "",
            "crit" if svc.engine.halted else "info",
            "Emergency stop active"
            if svc.engine.halted
            else f"{len(svc.engine.list_approvals())} approvals open",
        )
    )
    return tiles


def spoken(tiles: list[dict[str, Any]]) -> str:
    by_key = {t["key"]: t for t in tiles}
    parts = []
    alerts = by_key.get("alerts")
    if alerts:
        parts.append("No open alerts" if not alerts["value"] else f"{alerts['value']} open alerts")
    cpu = by_key.get("cpu")
    if cpu:
        parts.append(f"The PC is running at {cpu['value']} percent")
    devices = by_key.get("devices")
    if devices:
        parts.append(f"{devices['value']} devices on the home network, {devices['detail']}")
    backup = by_key.get("backup")
    if backup:
        parts.append(f"Last backup {backup['value']} hours ago")
    today = by_key.get("ai_today")
    if today:
        parts.append(f"AI costs today {today['value']:.2f} euros")
    reminders = by_key.get("reminders")
    if reminders and reminders["value"]:
        parts.append(f"{reminders['value']} items due in the next 24 hours")
    return ". ".join(parts) + "." if parts else "No KPIs available."
