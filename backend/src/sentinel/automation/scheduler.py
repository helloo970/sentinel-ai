"""Fixed schedules: mode timer, weekly tidy-up, price alerts.

The tidy-up runs as a fixed rule (Source.RULE) through the permissions engine: automatically
in autopilot, otherwise as an approval. Undo stays possible for one week.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sentinel.automation import files
from sentinel.permissions.models import ActionDenied, Severity, Source

if TYPE_CHECKING:
    from sentinel.services import Services

log = logging.getLogger(__name__)

TICK_S = 5
CLEANUP_EVERY_S = 60
DB_BACKUP_EVERY_S = 24 * 3600


class AutomationScheduler:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self._task: asyncio.Task[None] | None = None
        self._last_cleanup_check = 0.0

    # ------------------------------------------------------------------ Tidy-up

    def cleanup_due(self, now: float | None = None) -> bool:
        cfg = self.svc.config
        if not cfg.downloads_cleanup or not cfg.managed_folders:
            return False
        moment = datetime.fromtimestamp(now or time.time())
        hour, minute = (int(x) for x in cfg.downloads_cleanup_time.split(":"))
        if moment.weekday() != cfg.downloads_cleanup_day or (moment.hour, moment.minute) < (
            hour,
            minute,
        ):
            return False
        week = moment.strftime("%G-W%V")
        if self.svc.store.get("downloads_cleanup_week") == week:
            return False
        self.svc.store.set("downloads_cleanup_week", week)
        return True

    def run_cleanup(self) -> list[dict[str, Any]]:
        results = []
        for folder in self.svc.config.managed_folders:
            try:
                path = files.resolve_inside(str(folder), self.svc.config.managed_folders)
                if not files.plan_organize(path):
                    continue
            except (ActionDenied, OSError):
                continue
            outcome = self.svc.engine.request(
                "organize_folder",
                {"folder": str(path)},
                source=Source.RULE,
                actor="rule:weekly_cleanup",
                reason="Weekly tidy-up",
            )
            if outcome.status == "executed":
                self.svc.notifier.notify(
                    "Downloads tidied",
                    f"{outcome.message}. Undo is possible for one week.",
                    undo_id=outcome.undo_id,
                    push=False,
                )
            results.append({"folder": str(path), "status": outcome.status})
        return results

    # ------------------------------------------------------------------ Loop

    def tick(self, now: float | None = None) -> None:
        now = now or time.time()
        self.svc.modes.tick(now)
        if now - self._last_cleanup_check >= CLEANUP_EVERY_S:
            self._last_cleanup_check = now
            if not self.svc.engine.halted and self.cleanup_due(now):
                self.run_cleanup()
            # Price alerts: at most one page per minute (read only, no execution)
            self.svc.prices.check_due(now)
            self.backup_db_if_due(now)

    def backup_db_if_due(self, now: float) -> None:
        """Back up the database once a day (the last 7 are kept)."""
        from sentinel.db import backup_database

        last = float(self.svc.store.get("db_backup_ts") or 0)
        if now - last < DB_BACKUP_EVERY_S:
            return
        self.svc.store.set("db_backup_ts", str(now))
        target = backup_database(self.svc.db, self.svc.config.data_dir / "backup" / "auto")
        if target is None:
            log.error("database check failed – no backup written")
            self.svc.notifier.notify(
                "Database damaged",
                "Quit SENTINEL and run in a terminal: sentinel db restore",
                severity=Severity.WARN,
                push=False,
            )

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.tick)
            except Exception:
                log.exception("automation scheduler failed")
            await asyncio.sleep(TICK_S)

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
