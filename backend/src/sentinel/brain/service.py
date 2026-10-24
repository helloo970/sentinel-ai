"""Brain service: voice commands, live glow, learning while idle, server backup.

Voice commands only come from the user's own messages (chat, voice, phone).
"Forget …" only deletes after a "yes" – with an entry in the audit log.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from datetime import date
from typing import TYPE_CHECKING, Any

from sentinel.brain import backup, ingest, learner, seed, system
from sentinel.brain.privacy import blocked_reason
from sentinel.brain.store import UNTRUSTED_ORIGINS, BrainRejected, BrainStore
from sentinel.server_agent.client import ServerAgentError

if TYPE_CHECKING:
    from sentinel.services import Services

log = logging.getLogger(__name__)

TICK_S = 60
SYNC_EVERY_S = 120  # SENTINEL data
SCAN_EVERY_S = 3600  # folders
FORGET_TTL_S = 60
MIN_LEARN_CHARS = 25
BUSY_VOICE_STATES = {"listening", "thinking", "speaking"}
MIRRORED = ("sentinel:memory:", "sentinel:reminder:", "sentinel:shop:")

_ASK = re.compile(
    r"^(?:what\s+do\s+you\s+(?:know|remember)\s+about"
    r"|tell\s+me\s+(?:something\s+|more\s+|everything\s+)?about)\s+(.{2,80})$",
    re.IGNORECASE,
)
_REMEMBER = re.compile(
    r"^(?:please\s+)?(?:remember|note|keep\s+in\s+mind)(?:\s+that)?[\s,:]+"
    r"(?:please\s+)?(?:that\s+)?(.{3,300})$",
    re.I,
)
_FORGET = re.compile(
    r"^(?:please\s+)?forget\s+(?:please\s+)?"
    r"(?:everything\s+about\s+|all\s+about\s+|about\s+|the\s+thing\s+(?:about|with)\s+)?"
    r"(.{2,80})$",
    re.I,
)
_SHOW = re.compile(r"^(?:(?:show|open)\s+(?:me\s+)?(?:my\s+|the\s+|your\s+)?)?brain$", re.I)
_CONFIRM = re.compile(
    r"^(?:yes|yeah|yep|yup|sure|of\s+course|do\s+it|confirm|"
    r"yes,?\s*(?:please|sure|forget\s+it|delete\s+it|do\s+it))$",
    re.I,
)
_CANCEL = re.compile(r"^(?:no|nope|cancel|never\s*mind|keep\s+it|don['’]?t)$", re.I)
_PREFIX = re.compile(r"^(?:(?:hey|ok(?:ay)?)\s+)?(?:jarvis|sentinel)[\s,:]*", re.I)
_TRAILING = re.compile(r"[\s.!?]+$")


def _normalize(text: str) -> str:
    return _TRAILING.sub("", _PREFIX.sub("", text.strip())).strip()


def _short_label(statement: str) -> str:
    statement = statement.strip().rstrip(".")
    if len(statement) <= 60:
        return statement[0].upper() + statement[1:]
    cut = statement[:60].rsplit(" ", 1)[0]
    return cut[0].upper() + cut[1:] + " …"


class BrainService:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self.store = BrainStore(svc.db)
        self._pending: dict[str, tuple[int, float]] = {}
        self._task: asyncio.Task[None] | None = None
        self._last_sync = 0.0
        self._last_scan = 0.0
        self._last_stats: dict[str, int] = {}
        self.last_error: str | None = None

    # ---------------------------------------------------------------- Events

    def _publish(self, kind: str, data: dict[str, Any]) -> None:
        self.svc.bus.publish(kind, data)

    def focus(self, node_id: int, *, forget: bool = False) -> None:
        self._publish("brain_focus", {"id": node_id, "forget": forget})

    def changed(self) -> None:
        self._publish("brain_changed", self.store.stats())

    # ---------------------------------------------------------------- Voice commands

    async def handle_command(self, text: str, *, via: str = "chat") -> str | None:
        """Answer to a brain command, or None if it isn't one."""
        norm = _normalize(text)
        if not norm or len(norm) > 320:
            return None
        pending = self._pending.get(via)
        if pending and pending[1] < time.time():
            self._pending.pop(via, None)
            pending = None
        if pending and _CONFIRM.match(norm):
            return self._confirm_forget(via)
        if pending and _CANCEL.match(norm):
            self._pending.pop(via, None)
            return "Okay, I'll keep it."
        if _SHOW.match(norm):
            self._publish("brain_show", {})
            stats = self.store.stats()
            return f"Here's my brain: {stats['nodes']} thoughts, {stats['edges']} connections."
        found = _REMEMBER.match(norm)
        if found:
            return self._remember(found.group(1))
        found = _ASK.match(norm)
        if found:
            return self._ask(found.group(1))
        found = _FORGET.match(norm)
        if found:
            return self._stage_forget(found.group(1), via)
        return None

    def _remember(self, statement: str) -> str:
        statement = statement.strip()
        reason = blocked_reason(statement)
        if reason:
            return f"I won't remember that: {reason}."
        try:
            node = self.store.add(
                _short_label(statement),
                category="ich",
                summary=statement[0].upper() + statement[1:],
                origin="user",
                source="voice",
            )
        except BrainRejected as exc:
            return f"I won't remember that: {exc}."
        for other in self.store.search(statement, limit=3):
            if other["id"] != node["id"]:
                self.store.link(int(node["id"]), int(other["id"]))
        self.svc.audit.append(
            actor="user",
            source="user",
            action="brain_remember",
            params={"id": node["id"]},
            decision="execute",
            result=node["label"],
        )
        self.changed()
        self.focus(int(node["id"]))
        return f"Got it: {node['summary'] or node['label']}"

    def _ask(self, topic: str) -> str:
        node = self.store.lookup(topic)
        if node is None:
            return f'I don\'t know anything about "{topic}" yet. Just say "Remember that …".'
        self.store.touch([int(node["id"])])
        self.focus(int(node["id"]))
        parts = [node["label"] + (f": {node['summary']}" if node["summary"] else "")]
        neighbors = [n["label"] for n in self.store.neighbors(int(node["id"]))][:5]
        if neighbors:
            parts.append("Connected to " + ", ".join(neighbors) + ".")
        return " ".join(parts)

    def _stage_forget(self, topic: str, via: str) -> str:
        node = self.store.lookup(topic)
        if node is None:
            return f'I have nothing stored about "{topic}".'
        if not self.forgettable(node):
            return (
                f'"{node["label"]}" belongs to SENTINEL itself and comes back automatically. '
                "I can't forget that."
            )
        links = len(self.store.neighbors(int(node["id"])))
        self._pending[via] = (int(node["id"]), time.time() + FORGET_TTL_S)
        self.focus(int(node["id"]), forget=True)
        extra = f" and {links} connections" if links else ""
        return f'Should I forget "{node["label"]}"{extra}? Say "yes" to confirm.'

    def _confirm_forget(self, via: str) -> str:
        node_id, _ = self._pending.pop(via)
        node = self.store.get(node_id)
        if node is None or not self.store.delete(node_id):
            return "That's already gone."
        self._delete_mirrored(node)
        self.svc.audit.append(
            actor="user",
            source="user",
            action="brain_forget",
            params={"id": node_id},
            decision="execute",
            result=node["label"],
            detail=f"via {via}",
        )
        self.changed()
        return f"Forgotten: {node['label']}."

    @staticmethod
    def forgettable(node: dict[str, Any]) -> bool:
        """System nodes are recreated from SENTINEL; only mirrored data can be forgotten."""
        if node["origin"] not in ("system", "sensor"):
            return True
        return str(node["source"]).startswith(MIRRORED)

    def _delete_mirrored(self, node: dict[str, Any]) -> None:
        """Also delete mirrored entries (memory, reminder, shopping) at their source."""
        kind, _, ref = str(node["source"]).removeprefix("sentinel:").partition(":")
        if not ref.isdigit():
            return
        if kind == "memory":
            self.svc.memory.delete(int(ref))
        elif kind == "reminder":
            self.svc.reminders.cancel(int(ref))
        elif kind == "shop":
            self.svc.shopping.set_done(int(ref), True)

    def delete_from_ui(self, node_id: int) -> bool:
        node = self.store.get(node_id)
        if node is None or not self.forgettable(node) or not self.store.delete(node_id):
            return False
        self._delete_mirrored(node)
        self.svc.audit.append(
            actor="user",
            source="user",
            action="brain_forget",
            params={"id": node_id},
            decision="execute",
            result=node["label"],
            detail="via ui",
        )
        self.changed()
        return True

    # ---------------------------------------------------------------- Chat integration

    def enqueue_chat(self, text: str, message_id: int) -> None:
        if len(text) < MIN_LEARN_CHARS or blocked_reason(text):
            return
        self.svc.db.execute(
            "INSERT OR IGNORE INTO brain_queue(kind, ref, text, created) VALUES('chat',?,?,?)",
            (f"chat:{message_id}", text[:3000], time.time()),
        )

    def relevant(self, text: str) -> tuple[list[str], list[str]]:
        """Matching thoughts (trusted, untrusted) and live glow in the 3D brain."""
        rows = self.store.search(text, limit=6)
        if not rows:
            return [], []
        ids = [int(r["id"]) for r in rows]
        self.store.touch(ids)
        self._publish("brain_active", {"ids": ids})
        trusted, untrusted = [], []
        for row in rows:
            line = row["label"] + (f": {row['summary']}" if row["summary"] else "")
            (untrusted if row["origin"] in UNTRUSTED_ORIGINS else trusted).append(line)
        return trusted, untrusted

    def style(self) -> list[str]:
        """The user's style wishes (only their own entries), always go into the context."""
        return [
            row["summary"] or row["label"]
            for row in self.store.in_category("stil", limit=12)
            if row["origin"] in ("user", "seed")
        ]

    # ---------------------------------------------------------------- Background

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def prepare(self) -> None:
        if self.svc.store.get("brain_seeded") != "1":
            seed.seed(self.store)
            self.svc.store.set("brain_seeded", "1")
        self.sync()

    def sync(self, *, scan: bool = True) -> None:
        system.sync(self.svc)
        self._last_sync = time.time()
        if scan:
            ingest.scan(self.svc.db, list(self.svc.config.brain_folders))
            self._last_scan = time.time()
        stats = self.store.stats()
        if stats != self._last_stats:
            self._last_stats = stats
            self.changed()

    async def _run(self) -> None:
        try:
            await asyncio.to_thread(self.prepare)
        except Exception:
            log.exception("brain: preparation failed")
        while True:
            await asyncio.sleep(TICK_S)
            try:
                await self.tick()
            except Exception:
                log.exception("brain: run failed")

    async def tick(self) -> None:
        if time.time() - self._last_sync > SYNC_EVERY_S:
            scan = time.time() - self._last_scan > SCAN_EVERY_S
            await asyncio.to_thread(self.sync, scan=scan)
        if self.can_learn():
            added = await learner.learn_once(self.svc)
            if added is not None:
                self._count_request()
                if added:
                    self.changed()
        if self.backup_due():
            await self.backup_now()

    def idle(self) -> bool:
        if self.svc.engine.halted:
            return False
        voice_state = getattr(self.svc.voice, "state", "")
        if voice_state in BUSY_VOICE_STATES:
            return False
        row = self.svc.db.query_one("SELECT MAX(ts) AS ts FROM chat_messages")
        last = row["ts"] if row and row["ts"] else 0.0
        return time.time() - last >= self.svc.config.brain_idle_min * 60

    def requests_today(self) -> int:
        if self.svc.store.get("brain_ai_day") != date.today().isoformat():
            return 0
        return int(self.svc.store.get("brain_ai_count") or 0)

    def _count_request(self) -> None:
        count = self.requests_today() + 1
        self.svc.store.set("brain_ai_day", date.today().isoformat())
        self.svc.store.set("brain_ai_count", str(count))

    def can_learn(self) -> bool:
        if self.svc.config.ai_only_in_chat:
            return False  # AI only in conversation
        if not self.svc.llm.available or self.svc.llm.name == "offline":
            return False
        if self.requests_today() >= self.svc.config.brain_ai_daily:
            return False
        ok, _ = self.svc.costs.check()
        return ok and self.idle()

    # ---------------------------------------------------------------- Backup

    def backup_due(self) -> bool:
        hours = self.svc.config.brain_backup_hours
        if not hours or not self.svc.server.configured or backup.stored_key() is None:
            return False
        last = float(self.svc.store.get("brain_backup_ts") or 0)
        return time.time() - last >= hours * 3600

    async def backup_now(self) -> dict[str, Any]:
        key = backup.stored_key()
        if key is None:
            raise backup.BackupError(
                "No backup password set: run sentinel brain password in the terminal"
            )
        data = self.store.export()
        fp = backup.fingerprint(data)
        now = time.time()
        if fp == self.svc.store.get("brain_backup_fp"):
            self.svc.store.set("brain_backup_ts", str(now))
            return {"status": "unchanged"}
        blob = backup.encrypt(data, *key)
        try:
            await self.svc.server.put_vault("brain", backup.to_b64(blob))
        except (ServerAgentError, OSError) as exc:
            self.last_error = str(exc)
            retry = max(0, self.svc.config.brain_backup_hours - 1) * 3600
            self.svc.store.set("brain_backup_ts", str(now - retry))  # retry in 1 h
            raise backup.BackupError(f"Backup failed: {exc}") from exc
        self.last_error = None
        self.svc.store.set("brain_backup_ts", str(now))
        self.svc.store.set("brain_backup_fp", fp)
        self.svc.audit.append(
            actor="system",
            source="rule",
            action="brain_backup",
            decision="execute",
            result=f"{len(blob)} bytes encrypted",
        )
        return {"status": "saved", "bytes": len(blob)}

    async def restore(self, password: str) -> int:
        text = await self.svc.server.get_vault("brain")
        if text is None:
            raise backup.BackupError("There is no backup on the server yet")
        data = backup.decrypt(backup.from_b64(text), password=password)
        count = self.store.import_(data)
        self.svc.audit.append(
            actor="user",
            source="user",
            action="brain_restore",
            decision="execute",
            result=f"{count} nodes",
        )
        self.changed()
        return count

    # ---------------------------------------------------------------- Status

    def status(self) -> dict[str, Any]:
        queue = self.svc.db.query_one("SELECT COUNT(*) AS n FROM brain_queue WHERE done = 0")
        last = float(self.svc.store.get("brain_backup_ts") or 0)
        return {
            **self.store.stats(),
            "queue": queue["n"] if queue else 0,
            "ai_today": self.requests_today(),
            "ai_daily": self.svc.config.brain_ai_daily,
            "folders": [str(p) for p in self.svc.config.brain_folders],
            "backup_password": backup.stored_key() is not None,
            "backup_server": bool(self.svc.server.configured),
            "backup_last": last or None,
            "backup_error": self.last_error,
        }
