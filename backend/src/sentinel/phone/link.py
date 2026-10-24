"""Phone pairing and signed commands (ADR 0009).

Flow:
- Pairing on the PC with the owner PIN: the PC generates a secret (pairing code/QR)
  and stores only two keys derived from it in the Windows vault:
  K_pair = HMAC(secret, "pair") and K_pin = HMAC(secret, "pin:" + PIN).
  The phone knows the secret; you type the PIN for sensitive commands.
- Every phone command is signed with K_pair, sensitive ones additionally with K_pin. The
  server only relays and can neither forge commands nor see the PIN.
- Timestamp (+-5 minutes) and one-time nonce prevent replays. Wrong PIN signatures
  lock out for 5 minutes after 5 attempts.
- Every command goes through the permission engine like a click in the app.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import re
import secrets
import time
from typing import TYPE_CHECKING, Any

from sentinel.permissions.models import Autonomy, Source
from sentinel.secrets_filter import redact

if TYPE_CHECKING:
    from sentinel.services import Services

KEYRING_SERVICE = "SentinelAI-Phone"
MAX_SKEW_S = 300
MAX_PAYLOAD = 2000
PIN_FAILS, PIN_LOCK_S = 5, 300
HEX64 = re.compile(r"^[0-9a-f]{64}$")
NONCE = re.compile(r"^[0-9a-f]{32}$")
# Commands that additionally need the PIN signature
PIN_COMMANDS = {"resume", "autonomy"}
COMMANDS = {
    "halt",
    "resume",
    "autonomy",
    "approve",
    "reject",
    "undo",
    "ack",
    "ack_all",
    "action",
    "chat",
    "test_alert",
}


class PhoneError(ValueError):
    pass


def _keyring() -> Any:
    import keyring

    return keyring


def derive(secret: bytes, label: str) -> bytes:
    return hmac.new(secret, label.encode(), hashlib.sha256).digest()


def sign(key: bytes, nonce: str, ts: float | int, payload: str) -> str:
    message = f"{nonce}.{int(ts)}.{payload}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def encode_secret(secret: bytes) -> str:
    return base64.b32encode(secret).decode().rstrip("=")


class PhoneLink:
    def __init__(self, svc: Services, clock: Any = time.time) -> None:
        self.svc = svc
        self.clock = clock
        self._nonces: dict[str, float] = {}
        self._pin_fails: list[float] = []

    # ------------------------------------------------------------------ Pairing

    def _get(self, name: str) -> bytes | None:
        try:
            value = _keyring().get_password(KEYRING_SERVICE, name)
        except Exception:
            return None
        return bytes.fromhex(value) if value else None

    @property
    def paired(self) -> bool:
        return self._get("pair_key") is not None

    def pair(self, pin: str) -> dict[str, str]:
        """Pair a new phone (replaces an old one). Needs the owner PIN."""
        if not self.svc.pin.is_set():
            raise PhoneError("Set an owner PIN first (Settings)")
        if not self.svc.pin.verify(pin, actor="user"):
            raise PhoneError("PIN wrong or locked")
        secret = secrets.token_bytes(20)
        store = _keyring()
        store.set_password(KEYRING_SERVICE, "pair_key", derive(secret, "pair").hex())
        store.set_password(KEYRING_SERVICE, "pin_key", derive(secret, "pin:" + pin).hex())
        self.svc.audit.append(actor="user", source="user", action="phone_pair", decision="execute")
        code = encode_secret(secret)
        return {"code": code, "fragment": f"pair={code}"}

    def unpair(self) -> None:
        store = _keyring()
        for name in ("pair_key", "pin_key"):
            with contextlib.suppress(Exception):
                store.delete_password(KEYRING_SERVICE, name)
        self.svc.audit.append(
            actor="user", source="user", action="phone_unpair", decision="execute"
        )

    # ------------------------------------------------------------------ Verify

    def _pin_locked(self) -> bool:
        now = self.clock()
        self._pin_fails = [t for t in self._pin_fails if t > now - PIN_LOCK_S]
        return len(self._pin_fails) >= PIN_FAILS

    def verify(self, envelope: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Check signature, time and nonce. Returns (command, PIN verified)."""
        pair_key = self._get("pair_key")
        if pair_key is None:
            raise PhoneError("No phone paired")
        payload = envelope.get("payload")
        nonce = str(envelope.get("nonce", ""))
        sig = str(envelope.get("sig", ""))
        try:
            ts = int(envelope.get("ts") or 0)
        except (TypeError, ValueError) as exc:
            raise PhoneError("Invalid timestamp") from exc
        if not isinstance(payload, str) or len(payload) > MAX_PAYLOAD:
            raise PhoneError("Invalid command")
        if not NONCE.match(nonce) or not HEX64.match(sig):
            raise PhoneError("Invalid signature")
        if not hmac.compare_digest(sign(pair_key, nonce, ts, payload), sig):
            raise PhoneError("Signature does not match (re-pair the phone?)")
        now = self.clock()
        if abs(now - ts) > MAX_SKEW_S:
            raise PhoneError("Command too old - check the clock on the phone and PC")
        self._nonces = {n: t for n, t in self._nonces.items() if t > now - 2 * MAX_SKEW_S}
        if nonce in self._nonces:
            raise PhoneError("Command was already executed")
        self._nonces[nonce] = now
        try:
            command = json.loads(payload)
        except ValueError as exc:
            raise PhoneError("Invalid command") from exc
        if not isinstance(command, dict) or command.get("cmd") not in COMMANDS:
            raise PhoneError("Unknown command")

        pin_ok = False
        pin_sig = envelope.get("pin_sig")
        if pin_sig:
            if self._pin_locked():
                raise PhoneError("PIN locked - wait 5 minutes")
            pin_key = self._get("pin_key")
            if (
                pin_key is None
                or not HEX64.match(str(pin_sig))
                or not hmac.compare_digest(sign(pin_key, nonce, ts, payload), str(pin_sig))
            ):
                self._pin_fails.append(now)
                self.svc.audit.append(
                    actor="phone",
                    source="user",
                    action="phone_pin",
                    decision="deny",
                    detail=f"wrong PIN ({len(self._pin_fails)}/{PIN_FAILS})",
                )
                raise PhoneError("PIN wrong")
            self._pin_fails = []
            pin_ok = True
        return command, pin_ok

    # ------------------------------------------------------------------ Execute

    async def handle(self, envelope: dict[str, Any]) -> dict[str, Any]:
        import asyncio

        try:
            command, pin_ok = self.verify(envelope)
        except PhoneError as exc:
            self.svc.audit.append(
                actor="phone",
                source="user",
                action="phone_command",
                decision="deny",
                detail=str(exc),
            )
            return {"status": "denied", "message": str(exc)}
        cmd = command["cmd"]
        if cmd in PIN_COMMANDS and not pin_ok:
            return {"status": "denied", "message": "I need your PIN for that"}
        if cmd == "chat":
            text = str(command.get("text", ""))[:500]
            if not text.strip():
                return {"status": "denied", "message": "Empty message"}
            result = await self.svc.chat.send(text, conversation="phone", via="phone")
            return {"status": "executed", "message": result.get("text", "")}
        return await asyncio.to_thread(self._run, command, pin_ok)

    def _run(self, command: dict[str, Any], pin_ok: bool) -> dict[str, Any]:
        engine = self.svc.engine
        cmd = command["cmd"]
        if cmd == "halt":
            engine.halt(actor="phone", via="phone")
            return {"status": "executed", "message": "Emergency stop active"}
        if cmd == "resume":
            engine.resume(actor="phone")
            return {"status": "executed", "message": "Emergency stop released"}
        if cmd == "autonomy":
            level = command.get("level")
            if not isinstance(level, int) or not 0 <= level <= 3:
                return {"status": "denied", "message": "Level 0 to 3"}
            engine.set_autonomy(Autonomy(level), actor="phone", reason="from the phone")
            return {"status": "executed", "message": f"Autonomy level {level}"}
        if cmd == "approve":
            outcome = engine.approve(str(command.get("id", "")), actor="phone", pin_verified=pin_ok)
            return _outcome(outcome)
        if cmd == "reject":
            ok = engine.reject(str(command.get("id", "")), actor="phone")
            return {
                "status": "executed" if ok else "denied",
                "message": "Rejected" if ok else "Approval not found",
            }
        if cmd == "undo":
            return _outcome(engine.undo(str(command.get("id", "")), actor="phone"))
        if cmd == "ack":
            alert_id = command.get("id")
            ok = isinstance(alert_id, int) and self.svc.alerts.ack(alert_id)
            return {"status": "executed" if ok else "denied", "message": "Acknowledged"}
        if cmd == "test_alert":
            from sentinel.monitor import simulate

            try:
                result = simulate.run_test(self.svc, str(command.get("rule", "")), actor="phone")
            except ValueError as exc:
                return {"status": "denied", "message": str(exc)}
            steps = " → ".join(result["defense"])
            return {"status": "executed", "message": f"{result['title']} | {steps}"}
        if cmd == "ack_all":
            self.svc.alerts.ack_all()
            return {"status": "executed", "message": "All alerts acknowledged"}
        # cmd == "action": any allowed action, like a click in the app
        name = str(command.get("name", ""))
        params = command.get("params") if isinstance(command.get("params"), dict) else {}
        outcome = engine.request(
            name, params, source=Source.USER, actor="phone", reason="from the phone"
        )
        return _outcome(outcome)


def _outcome(outcome: Any) -> dict[str, Any]:
    return {"status": outcome.status, "message": redact(str(outcome.message))[:300]}
