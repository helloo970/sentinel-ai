"""Simple event bus for server-sent events to the app (thread-safe)."""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import threading
import time
from collections.abc import AsyncIterator
from typing import Any


class EventBus:
    def __init__(self, max_queue: int = 200) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()
        self._max_queue = max_queue
        self._ids = itertools.count(1)
        self.history: list[dict[str, Any]] = []

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def listeners(self) -> int:
        """How many front ends are currently listening (e.g. the app)."""
        return len(self._subscribers)

    def publish(self, kind: str, data: dict[str, Any] | None = None) -> None:
        event = {"id": next(self._ids), "kind": kind, "ts": time.time(), "data": data or {}}
        with self._lock:
            self.history = [*self.history[-49:], event]
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._deliver(event)
        else:
            loop.call_soon_threadsafe(self._deliver, event)

    def _deliver(self, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(event)

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(self._max_queue)
        self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)
