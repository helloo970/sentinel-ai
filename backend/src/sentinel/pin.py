"""Owner PIN for critical approvals.

Only a scrypt hash with salt is stored. After 5 failed attempts the PIN is locked for
5 minutes. The PIN itself never ends up in logs, audit or memory.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from collections.abc import Callable
from typing import Any

from sentinel.audit import AuditLog
from sentinel.db import SettingsStore

PIN_RE = re.compile(r"^\d{6,12}$")
MAX_FAILS = 5
LOCK_S = 300
SCRYPT = {"n": 2**14, "r": 8, "p": 1}


class PinError(ValueError):
    pass


def _hash(pin: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(pin.encode(), salt=salt, n=n, r=r, p=p, dklen=32)


class PinStore:
    KEY = "owner_pin"
    FAILS = "owner_pin_fails"

    def __init__(
        self, store: SettingsStore, audit: AuditLog, clock: Callable[[], float] = time.time
    ) -> None:
        self.store = store
        self.audit = audit
        self.clock = clock

    def is_set(self) -> bool:
        return self.store.get(self.KEY) is not None

    def _fails(self) -> list[float]:
        try:
            fails = json.loads(self.store.get(self.FAILS, "[]") or "[]")
        except ValueError:
            fails = []
        since = self.clock() - LOCK_S
        return [float(t) for t in fails if float(t) > since]

    def locked_until(self) -> float | None:
        fails = self._fails()
        if len(fails) >= MAX_FAILS:
            return max(fails) + LOCK_S
        return None

    def status(self) -> dict[str, Any]:
        return {"set": self.is_set(), "locked_until": self.locked_until()}

    def set_pin(self, new_pin: str, old_pin: str | None = None, *, actor: str = "user") -> None:
        if not PIN_RE.match(new_pin or ""):
            raise PinError("PIN must consist of 6 to 12 digits")
        if self.is_set() and not self.verify(old_pin, actor=actor):
            raise PinError("Old PIN wrong or locked")
        salt = secrets.token_bytes(16)
        record = {"salt": salt.hex(), "hash": _hash(new_pin, salt, **SCRYPT).hex(), **SCRYPT}
        self.store.set(self.KEY, json.dumps(record))
        self.audit.append(actor=actor, source="user", action="pin_set", decision="execute")

    def verify(self, pin: str | None, *, actor: str = "user") -> bool:
        raw = self.store.get(self.KEY)
        if raw is None or not pin or self.locked_until() is not None:
            if raw is not None and self.locked_until() is not None:
                self.audit.append(
                    actor=actor,
                    source="user",
                    action="pin_verify",
                    decision="deny",
                    detail="locked",
                )
            return False
        record = json.loads(raw)
        expected = bytes.fromhex(record["hash"])
        actual = _hash(pin, bytes.fromhex(record["salt"]), record["n"], record["r"], record["p"])
        if hmac.compare_digest(expected, actual):
            self.store.set(self.FAILS, "[]")
            return True
        fails = [*self._fails(), self.clock()]
        self.store.set(self.FAILS, json.dumps(fails))
        self.audit.append(
            actor=actor,
            source="user",
            action="pin_verify",
            decision="deny",
            detail=f"wrong PIN ({len(fails)}/{MAX_FAILS})",
        )
        return False
