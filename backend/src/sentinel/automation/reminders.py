"""Reminders and timers with notification and spoken announcement."""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sentinel.services import Services

MAX_OPEN = 100


class ReminderService:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self._task: asyncio.Task[None] | None = None

    def add(self, text: str, due: float) -> dict[str, Any]:
        open_count = self.svc.db.query_one("SELECT COUNT(*) AS n FROM reminders WHERE done = 0")
        if open_count and open_count["n"] >= MAX_OPEN:
            raise ValueError("Too many open reminders")
        reminder_id = self.svc.db.execute(
            "INSERT INTO reminders(text, due, created) VALUES(?,?,?)", (text, due, time.time())
        )
        return self.get(reminder_id) or {}

    def get(self, reminder_id: int) -> dict[str, Any] | None:
        return self.svc.db.query_one("SELECT * FROM reminders WHERE id = ?", (reminder_id,))

    def list(self, include_done: bool = False) -> list[dict[str, Any]]:
        if include_done:
            return self.svc.db.query("SELECT * FROM reminders ORDER BY due DESC LIMIT 200")
        return self.svc.db.query("SELECT * FROM reminders WHERE done = 0 ORDER BY due ASC")

    def cancel(self, reminder_id: int) -> bool:
        return bool(
            self.svc.db.execute(
                "UPDATE reminders SET done = 2 WHERE id = ? AND done = 0", (reminder_id,)
            )
        )

    def restore(self, reminder_id: int) -> bool:
        return bool(
            self.svc.db.execute(
                "UPDATE reminders SET done = 0 WHERE id = ? AND done = 2", (reminder_id,)
            )
        )

    def fire_due(self, now: float | None = None) -> list[dict[str, Any]]:
        now = now or time.time()
        due = self.svc.db.query("SELECT * FROM reminders WHERE done = 0 AND due <= ?", (now,))
        for reminder in due:
            self.svc.db.execute("UPDATE reminders SET done = 1 WHERE id = ?", (reminder["id"],))
            self.svc.notifier.notify("Reminder", reminder["text"], speak=True, push=True)
        return due

    async def _loop(self) -> None:
        while True:
            await asyncio.to_thread(self.fire_due)
            await asyncio.sleep(5)

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
