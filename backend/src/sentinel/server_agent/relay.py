"""Phone view via the server: report status and pick up the emergency stop from the phone.

The PC opens no port for this. Every few seconds it reports a small status to its own
server agent and gets waiting commands in the reply. Without a signature only "halt"
(trigger the emergency stop) is accepted. Everything else must be signed by the paired
phone and is verified on the PC (sentinel.phone.link, ADR 0009).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any

import httpx

from sentinel.monitor import simulate
from sentinel.secrets_filter import redact
from sentinel.server_agent.client import ServerAgentError, ServerForbidden

if TYPE_CHECKING:
    from sentinel.services import Services

log = logging.getLogger(__name__)

INTERVAL_S = 5
UNSUPPORTED_BACKOFF_S = 300
ERROR_BACKOFF_S = 30
FORBIDDEN_BACKOFF_S = 300
ALLOWED_COMMANDS = {"halt", "phone"}
MAX_RESULTS = 20


class RelayService:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self._task: asyncio.Task[None] | None = None
        self.last_ok: float | None = None
        self.error: str | None = None
        self.supported: bool | None = None
        self._seen: set[str] = set()
        self._results: list[dict[str, Any]] = []

    @property
    def enabled(self) -> bool:
        return self.svc.config.server_relay and self.svc.server.configured

    def snapshot(self) -> dict[str, Any]:
        svc = self.svc
        recent = svc.alerts.list(status="new", limit=10)
        mode = svc.modes.active() if svc.modes is not None else None
        snap: dict[str, Any] = {
            "halted": svc.engine.halted,
            "autonomy": int(svc.engine.autonomy),
            "alerts": svc.alerts.counts(),
            "recent": [
                {
                    "id": a["id"],
                    "title": redact(a["title"])[:160],
                    "severity": a["severity"],
                    "ts": a["ts"],
                }
                for a in recent
            ],
            "mode": mode.get("title") if mode else None,
            "quiet": svc.notifier.quiet,
            "paired": svc.phone.paired,
            "results": list(self._results),
        }
        if snap["paired"]:
            snap.update(self._control_data())
        return snap

    def _control_data(self) -> dict[str, Any]:
        """Only for a paired phone: what it can control (no parameters, no paths)."""
        svc = self.svc
        active = (svc.modes.active() or {}).get("name") if svc.modes is not None else None
        return {
            "approvals": [
                {
                    "id": a["id"],
                    "title": redact(str(a["preview"] or a["action"]))[:200],
                    "risk": a["risk"],
                    "requires_pin": a["requires_pin"],
                    "expires": a["expires"],
                }
                for a in svc.engine.list_approvals()[:10]
            ],
            "undo": [
                {"id": u["id"], "title": redact(str(u["summary"]))[:160], "expires": u["expires"]}
                for u in svc.engine.list_undo()[:10]
            ],
            "modes": [
                {"name": m.name, "title": m.title, "active": m.name == active}
                for m in list(svc.modes.all().values())[:10]
            ],
            "routines": [
                {"name": r.name, "title": r.title} for r in list(svc.routines.all().values())[:10]
            ],
            "tests": [
                {"rule": t["rule"], "group": t["group"], "title": t["protects"]}
                for t in simulate.catalog()
            ],
            "programs": [
                {"name": p.name, "title": p.title} for p in list(svc.programs.all().values())[:20]
            ],
        }

    async def handle_all(self, commands: list[dict[str, Any]]) -> int:
        """Emergency stop directly, signed phone commands via PhoneLink."""
        phone = [c for c in commands if c.get("action") == "phone"]
        done = await asyncio.to_thread(self.handle, [c for c in commands if c not in phone])
        for command in phone:
            command_id = str(command.get("id", ""))[:32]
            if not command_id or command_id in self._seen:
                continue
            self._seen.add(command_id)
            envelope = command.get("envelope") if isinstance(command.get("envelope"), dict) else {}
            result = await self.svc.phone.handle(envelope)
            self._results = [
                *self._results[-(MAX_RESULTS - 1) :],
                {
                    "id": command_id,
                    "status": result["status"],
                    "message": redact(str(result.get("message", "")))[:500],
                    "ts": time.time(),
                },
            ]
            done += 1
        return done

    def handle(self, commands: list[dict[str, Any]]) -> int:
        """Run the unsigned emergency stop. Returns the number of executed commands."""
        done = 0
        for command in commands:
            action = command.get("action")
            command_id = str(command.get("id", ""))[:32]
            if command_id in self._seen:
                continue
            self._seen.add(command_id)
            if action not in ALLOWED_COMMANDS or action == "phone":
                log.warning("ignoring unknown relay command %r", action)
                self.svc.audit.append(
                    actor="phone",
                    source="untrusted",
                    action=f"relay:{str(action)[:40]}",
                    decision="deny",
                    detail="Unknown command from the server ignored",
                )
                continue
            if not self.svc.engine.halted:
                self.svc.engine.halt(actor="phone", via="phone")
                self.svc.notifier.notify(
                    "Emergency stop from the phone",
                    "All actions and agents are locked. It can only be released here on the PC.",
                    speak=True,
                )
            done += 1
        return done

    async def tick(self) -> float:
        """One pass. Returns the wait time until the next one."""
        if not self.enabled:
            return INTERVAL_S * 6
        try:
            commands = await self.svc.server.post_relay(self.snapshot())
        except ServerForbidden as exc:
            # Rejected: do not knock again every 30 s
            self.error = str(exc)
            return FORBIDDEN_BACKOFF_S
        except (ServerAgentError, httpx.HTTPError, ValueError) as exc:
            self.error = str(exc) or type(exc).__name__
            return ERROR_BACKOFF_S
        if commands is None:
            self.supported = False
            self.error = "Server agent does not know the phone view yet (update needed)"
            return UNSUPPORTED_BACKOFF_S
        self.supported = True
        self.error = None
        self.last_ok = time.time()
        if commands:
            await self.handle_all(commands)
            # Report the new status right away so the phone sees the emergency stop
            return 0.5
        return INTERVAL_S

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "supported": self.supported,
            "last_ok": self.last_ok,
            "error": self.error,
        }

    async def _loop(self) -> None:
        while True:
            try:
                delay = await self.tick()
            except Exception:
                log.exception("relay failed")
                delay = ERROR_BACKOFF_S
            await asyncio.sleep(delay)

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
